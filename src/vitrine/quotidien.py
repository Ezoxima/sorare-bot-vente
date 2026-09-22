"""Orchestration du passage quotidien : périmètre → coûts → marché → décisions
→ rapport → mail → publications, fortement bridée.

Le mode à blanc reste le défaut de tout l'outil (cf. `cli.py`) : `quotidien` ne
déroge pas à la règle. `--executer` reste nécessaire, et le nombre de
publications reste plafonné par `--max-cartes` (défaut bas : 3).

**Le périmètre.** La liste « À Vendre » n'existe pas dans l'API (seules des
watchlists de joueurs sont exposées, plafonnées à 5 éléments sans pagination —
mesuré le 2026-09-20). Le périmètre retenu est donc *la carte déjà mise en
vente au moins une fois* : l'union des annonces en cours et des annonces
closes sans acheteur, déjà lues par `inventaire.lire`. Mettre une carte en
vente à la main l'inscrit dans l'automatisation ; `perimetre.csv` permet de
l'exclure nommément (colonne `exclure`), ou, en repli, de restreindre à une
liste explicite (colonne `inclure`).

**La règle d'arrêt.** Si plus d'un tiers des cartes du périmètre tombent en
`BLOQUER`, le passage s'interrompt **avant la première écriture** et envoie un
mail. Une donnée qui se dégrade en masse est un symptôme, pas un cas
particulier à absorber carte par carte.

**Fraîcheur du marché.** `preparer_perimetre` (périmètre + coûts, stable) et
`decider_toutes` (marché + décision, volatile) sont deux étapes séparées :
`executer_passage` n'appelle la seconde qu'au moment de composer le rapport et
le mail, jamais plus tôt dans le passage — un prix concurrent lu en début de
passage peut déjà avoir changé au moment d'agir dessus.
"""

from __future__ import annotations

import csv
import dataclasses
import pathlib
from typing import Any

from vitrine import alerte, cle_ethereum
from vitrine import cout as co
from vitrine import marche as mar
from vitrine import offre as off
from vitrine.client import ClientSorare
from vitrine.config import Config
from vitrine.plan import RAIL_HISTORIQUE, RAIL_SOLANA
from vitrine.politique import Decision, Verdict, appliquer_quota, decider

FICHIER_STOP = "STOP"
SEUIL_BLOCAGE_MASSE = 1 / 3


@dataclasses.dataclass(frozen=True)
class CarteSuivie:
    """Une carte du périmètre, avec ce qu'il faut savoir d'elle pour décider et,
    si `REPOSITIONNER`, pour agir (faut-il d'abord annuler l'annonce en cours ?)."""

    asset_id: str
    slug: str
    nom: str
    rarete: str
    rail: str  # plan.RAIL_SOLANA | plan.RAIL_HISTORIQUE — cf. politique.decider
    prix_actuel_cents: int | None
    blockchain_id_actuel: str | None  # None = pas d'annonce en cours à annuler


def charger_perimetre(chemin: pathlib.Path) -> tuple[set[str], set[str]]:
    """Lit `perimetre.csv` s'il existe (colonnes `carte`, `exclure`, `inclure` ;
    `carte` est un slug ou un assetId). Rend `(exclusions, inclusions)` —
    `inclusions` vide = mode exclusion (défaut) ; non vide = liste explicite,
    le repli prévu si le périmètre par défaut ne convient pas.

    Fichier absent = aucune exclusion, aucune restriction : comportement
    par défaut inchangé.
    """
    if not chemin.exists():
        return set(), set()
    exclusions: set[str] = set()
    inclusions: set[str] = set()
    with chemin.open(encoding="utf-8-sig", newline="") as f:
        for rang in csv.DictReader(f, delimiter=";"):
            carte = (rang.get("carte") or "").strip()
            if not carte:
                continue
            if (rang.get("exclure") or "").strip().lower() in ("oui", "true", "1"):
                exclusions.add(carte)
            if (rang.get("inclure") or "").strip().lower() in ("oui", "true", "1"):
                inclusions.add(carte)
    return exclusions, inclusions


def _carte_unique(offre_dict: dict[str, Any]) -> dict[str, Any] | None:
    cartes = (offre_dict.get("senderSide") or {}).get("anyCards") or []
    return cartes[0] if len(cartes) == 1 else None


def cartes_perimetre(brut: dict[str, Any]) -> dict[str, CarteSuivie]:
    """Union des cartes en vente et closes sans acheteur — le signal « déjà
    mise en vente à la main » que le reste de l'outil utilise déjà (`plan.py`).
    Une carte close puis relistée n'apparaît qu'une fois, côté « en cours »."""
    cartes: dict[str, CarteSuivie] = {}
    for offre_dict in brut["en_cours"]:
        carte = _carte_unique(offre_dict)
        if carte is None:
            continue
        prix = ((offre_dict.get("receiverSide") or {}).get("amounts") or {}).get("eurCents")
        cartes[carte["assetId"]] = CarteSuivie(
            asset_id=carte["assetId"],
            slug=carte["slug"],
            nom=carte["name"],
            rarete=carte.get("rarityTyped") or "?",
            rail=RAIL_SOLANA if carte.get("solanaAddress") else RAIL_HISTORIQUE,
            prix_actuel_cents=prix,
            blockchain_id_actuel=offre_dict.get("blockchainId"),
        )
    for offre_dict in brut["terminees_sans_acheteur"]:
        carte = _carte_unique(offre_dict)
        if carte is None or carte["assetId"] in cartes:
            continue
        cartes[carte["assetId"]] = CarteSuivie(
            asset_id=carte["assetId"],
            slug=carte["slug"],
            nom=carte["name"],
            rarete=carte.get("rarityTyped") or "?",
            rail=RAIL_SOLANA if carte.get("solanaAddress") else RAIL_HISTORIQUE,
            prix_actuel_cents=None,
            blockchain_id_actuel=None,
        )
    return cartes


def appliquer_exclusions(
    cartes: dict[str, CarteSuivie], exclusions: set[str], inclusions: set[str]
) -> dict[str, CarteSuivie]:
    def _reference(c: CarteSuivie) -> set[str]:
        return {c.asset_id, c.slug}

    if inclusions:
        return {aid: c for aid, c in cartes.items() if _reference(c) & inclusions}
    return {aid: c for aid, c in cartes.items() if not _reference(c) & exclusions}


def preparer_perimetre(
    client: ClientSorare,
    user_slug: str,
    brut: dict[str, Any],
    *,
    exclusions: set[str] = frozenset(),
    inclusions: set[str] = frozenset(),
) -> tuple[dict[str, CarteSuivie], dict[str, co.Cout]]:
    """Périmètre + coûts d'achat. Stable : le coût payé pour une carte ne change
    pas pendant un passage, donc rien n'oblige à le relire deux fois."""
    cartes = appliquer_exclusions(cartes_perimetre(brut), exclusions, inclusions)
    if not cartes:
        return cartes, {}
    slugs = [c.slug for c in cartes.values()]
    couts = co.resoudre_couts(client, user_slug, slugs)
    return cartes, couts


def decider_toutes(
    client: ClientSorare,
    user_slug: str,
    cartes: dict[str, CarteSuivie],
    couts: dict[str, co.Cout],
    *,
    marge_pct: float = 5.0,
    baisse_max_pct: float | None = 10.0,
) -> list[Decision]:
    """Marché + politique. Volatile : le prix concurrent bouge en continu, donc
    cette fonction est faite pour être appelée au tout dernier moment avant de
    présenter ou publier quoi que ce soit — jamais mise en cache plus tôt dans
    le passage (demandé le 2026-09-21 : un prix « moins cher » vu en tout début
    de passage peut déjà ne plus être le bon)."""
    a_comparer = [
        mar.Carte(
            slug=c.slug,
            player_slug=couts[c.slug].player_slug,
            rarete=couts[c.slug].rarete or c.rarete,
            in_season=couts[c.slug].in_season,
        )
        for c in cartes.values()
        if couts[c.slug].player_slug  # sans playerSlug, aucune comparaison possible
    ]
    marches = (
        mar.plus_bas_prix_concurrent(client, a_comparer, mon_slug=user_slug)
        if a_comparer
        else {}
    )
    return [
        decider(
            asset_id=carte.asset_id,
            slug=carte.slug,
            nom=carte.nom,
            prix_actuel_cents=carte.prix_actuel_cents,
            cout=couts[carte.slug],
            marche=marches.get(carte.slug, mar.PrixConcurrent(carte.slug, None, 0)),
            marge_pct=marge_pct,
            baisse_max_pct=baisse_max_pct,
        )
        for carte in cartes.values()
    ]


def preparer_passage(
    client: ClientSorare,
    user_slug: str,
    brut: dict[str, Any],
    *,
    exclusions: set[str] = frozenset(),
    inclusions: set[str] = frozenset(),
    marge_pct: float = 5.0,
    baisse_max_pct: float | None = 10.0,
) -> tuple[list[Decision], dict[str, CarteSuivie]]:
    """Calcule les décisions du jour : coûts + marché + politique, sans mail ni
    écriture. Isolée du reste pour rester testable sans réseau (le `client`
    n'est utilisé qu'ici, jamais dans `politique.py`). `executer_passage`
    n'appelle pas cette fonction telle quelle : il sépare `preparer_perimetre`
    de `decider_toutes` pour recalculer le marché au plus près du mail."""
    cartes, couts = preparer_perimetre(
        client, user_slug, brut, exclusions=exclusions, inclusions=inclusions
    )
    if not cartes:
        return [], cartes
    decisions = decider_toutes(
        client, user_slug, cartes, couts, marge_pct=marge_pct, baisse_max_pct=baisse_max_pct
    )
    return decisions, cartes


def _gmail_configure(config: Config) -> bool:
    return bool(config.gmail_expediteur and config.gmail_mot_de_passe_application)


def _envoyer_mail(config: Config, decisions: list[Decision]) -> str:
    if not _gmail_configure(config):
        return "Gmail non configuré (.env) : mail non envoyé."
    alerte.envoyer_rapport(
        expediteur=config.gmail_expediteur,
        mot_de_passe_application=config.gmail_mot_de_passe_application,
        destinataire=config.gmail_destinataire,
        decisions=decisions,
    )
    return f"Mail envoyé à {config.gmail_destinataire}."


def executer_passage(
    client: ClientSorare,
    config: Config,
    *,
    inventaire_brut: dict[str, Any],
    perimetre_chemin: pathlib.Path,
    executer: bool,
    max_cartes: int,
    baisse_max_pct: float,
    confirmer: Any = input,
) -> int:
    """Le passage complet. Rend un code de sortie (0 = fait ou rien à faire,
    1 = interrompu ou annulé). N'écrit jamais sans `executer=True` **et** une
    confirmation tapée — même modèle que `cmd_relister`."""
    if pathlib.Path(FICHIER_STOP).exists():
        print(f"Fichier « {FICHIER_STOP} » présent : passage interrompu avant toute écriture.")
        return 1

    exclusions, inclusions = charger_perimetre(perimetre_chemin)
    user_slug = inventaire_brut["slug"]
    cartes, couts = preparer_perimetre(
        client, user_slug, inventaire_brut, exclusions=exclusions, inclusions=inclusions
    )
    if not cartes:
        print("Aucune carte dans le périmètre.")
        return 0

    # Marché recalculé ici, au point le plus proche possible du rapport/mail/
    # publication — jamais réutilisé d'un calcul fait plus tôt (cf. `decider_toutes`).
    decisions = decider_toutes(
        client, user_slug, cartes, couts, baisse_max_pct=baisse_max_pct
    )

    ordonnees = sorted(decisions, key=lambda d: d.verdict is not Verdict.REPOSITIONNER)
    decisions = appliquer_quota(ordonnees, max_cartes)

    # Le rail n'influence plus le verdict depuis le 2026-09-22 (`politique.py`
    # sait désormais signer le rail Solana) : un `BLOQUER` compte donc à
    # nouveau normalement, quel que soit le rail — ce n'est plus un artefact
    # structurel à exclure de la règle d'arrêt en masse.
    n_bloque = sum(1 for d in decisions if d.verdict is Verdict.BLOQUER)
    if cartes and n_bloque / len(cartes) > SEUIL_BLOCAGE_MASSE:
        print(alerte.composer_rapport(decisions))
        print(_envoyer_mail(config, decisions))
        print(
            f"\nInterrompu : {n_bloque}/{len(cartes)} cartes bloquées "
            f"(> {SEUIL_BLOCAGE_MASSE:.0%} du périmètre). Rien n'a été publié."
        )
        return 1

    print(alerte.composer_rapport(decisions))
    # Le mail sert aussi à travailler à la main : une carte REPOSITIONNER porte
    # déjà le prix visé (`prix_vise_cents`), donc un passage à blanc régulier
    # (sans --executer) transforme le mail en « voici quoi publier et à quel
    # prix », pas seulement « voici ce qui est bloqué ».
    if any(d.verdict in (Verdict.ALERTER, Verdict.REPOSITIONNER) for d in decisions):
        print(_envoyer_mail(config, decisions))

    a_publier = [d for d in decisions if d.verdict is Verdict.REPOSITIONNER]

    if not executer:
        print("\nMode à blanc : aucune annonce publiée. Ajoute --executer pour agir.")
        return 0

    if not a_publier:
        print("\nRien à repositionner.")
        return 0

    reponse = confirmer(
        f"\nRepositionner ces {len(a_publier)} annonce(s) ? Tape le nombre pour confirmer : "
    )
    if reponse.strip() != str(len(a_publier)):
        print("Annulé — rien n'a été envoyé.")
        return 1

    # Lue une seule fois : `None` si aucune clé n'a jamais été enregistrée
    # (`vitrine.outils.enregistrer_cle_ethereum`) — pas une erreur en soi, une
    # carte du rail historique n'en a jamais eu besoin. `off.preparer` lève
    # `SignatureRequise` si une carte Solana en a besoin et qu'elle manque.
    cle_privee_eth = cle_ethereum.lire_cle_privee()

    for decision in a_publier:
        assert decision.prix_vise_cents is not None
        carte = cartes[decision.asset_id]
        # `preparer` d'abord, `annuler` ensuite seulement : `preparer` ne fait
        # rien (aucune écriture), donc c'est le seul ordre qui ne risque pas de
        # retirer une annonce en cours puis échouer à la recréer (mesuré en
        # conditions réelles le 2026-09-20 — une carte a fini délistée quand
        # `preparer` a levé `SignatureRequise` après l'annulation).
        approbations = off.preparer(
            client, decision.asset_id, decision.prix_vise_cents, cle_privee_eth=cle_privee_eth
        )
        if carte.blockchain_id_actuel:
            off.annuler(client, carte.blockchain_id_actuel)
        annonce = off.creer_annonce(
            client, decision.asset_id, decision.prix_vise_cents, approbations=approbations
        )
        print(
            f"  repositionnée — {decision.nom[:40]:40} "
            f"{decision.prix_vise_cents / 100:.2f} € → {annonce.statut}"
        )
    return 0
