"""Cœur pur : décider quelles cartes sont remettables en vente, et à quel prix.

Aucune entrée/sortie ici — ni réseau, ni fichier, ni horloge implicite. C'est ce
qui permet de rejouer la logique sur un inventaire figé et d'écrire des tests qui
mordent.

Les motifs d'exclusion ne sont pas théoriques : chacun a été observé le
2026-09-19 sur les 12 annonces closes d'un compte réel.

- 2 cartes portaient `tradeableStatus: NO` — elles ne peuvent pas être cédées ;
- 2 cartes étaient **déjà remises en vente à la main**, et l'API le dit de deux
  façons indépendantes (`card.liveSingleSaleOffer`, et la présence de la carte
  parmi les annonces en cours). On vérifie les deux : rater ce cas publierait un
  doublon d'annonce sur une carte déjà en vitrine.

Reste 8 candidates sur 12. Un outil qui aurait ignoré ces deux contrôles aurait
« relisté » 12 cartes et échoué 4 fois, dont 2 en silence.
"""

from __future__ import annotations

import dataclasses
from typing import Any

RAIL_SOLANA = "solana"
RAIL_HISTORIQUE = "historique"


@dataclasses.dataclass(frozen=True)
class Ligne:
    """Une carte de la file, retenue ou non, avec son motif."""

    asset_id: str
    slug: str
    nom: str
    rarete: str
    prix_origine_cents: int | None
    devise: str
    fin_annonce: str
    rail: str
    retenue: bool
    motif: str | None = None
    prix_propose_cents: int | None = None

    @property
    def prix_origine_eur(self) -> float | None:
        return None if self.prix_origine_cents is None else self.prix_origine_cents / 100

    @property
    def prix_propose_eur(self) -> float | None:
        return None if self.prix_propose_cents is None else self.prix_propose_cents / 100


def _carte_unique(offre: dict[str, Any]) -> dict[str, Any] | None:
    """Rend la carte d'une vente simple, ou None si l'offre n'en est pas une.

    Une vente simple porte exactement une carte. Zéro ou plusieurs signale un
    échange ou un lot : hors périmètre, et surtout pas quelque chose qu'on
    republie à l'aveugle.
    """
    cartes = (offre.get("senderSide") or {}).get("anyCards") or []
    return cartes[0] if len(cartes) == 1 else None


def _prix_cents(offre: dict[str, Any]) -> tuple[int | None, str]:
    """Prix demandé, lu sur `receiverSide` (cf. docstring d'`inventaire`)."""
    montants = (offre.get("receiverSide") or {}).get("amounts") or {}
    return montants.get("eurCents"), montants.get("referenceCurrency") or "?"


def assets_en_vente(offres_en_cours: list[dict[str, Any]]) -> set[str]:
    """Identifiants des cartes actuellement en vitrine."""
    return {
        carte["assetId"]
        for offre in offres_en_cours
        for carte in (offre.get("senderSide") or {}).get("anyCards") or []
    }


def analyser(
    terminees_sans_acheteur: list[dict[str, Any]],
    offres_en_cours: list[dict[str, Any]],
) -> list[Ligne]:
    """Transforme la file brute en lignes décidées, la plus récente d'abord.

    Une même carte peut avoir échoué plusieurs fois : on ne garde que son
    annonce close la plus récente, sinon elle serait proposée deux fois et la
    seconde tentative écraserait la première.
    """
    deja_en_vitrine = assets_en_vente(offres_en_cours)
    lignes: list[Ligne] = []
    vues: set[str] = set()

    plus_recentes_d_abord = sorted(
        terminees_sans_acheteur, key=lambda o: o.get("endDate") or "", reverse=True
    )
    for offre in plus_recentes_d_abord:
        carte = _carte_unique(offre)
        if carte is None:
            continue  # pas une vente simple : hors périmètre, silencieux par construction
        asset = carte["assetId"]
        if asset in vues:
            continue  # annonce close plus ancienne pour la même carte
        vues.add(asset)

        prix, devise = _prix_cents(offre)
        motif: str | None = None
        if carte.get("tradeableStatus") != "YES":
            motif = f"carte non cessible (tradeableStatus={carte.get('tradeableStatus')})"
        elif carte.get("liveSingleSaleOffer") or asset in deja_en_vitrine:
            motif = "déjà remise en vente"
        elif prix is None:
            # Échec bruyant : un prix illisible ne devient pas 0 € par défaut.
            motif = "prix d'origine illisible (eurCents absent)"

        lignes.append(
            Ligne(
                asset_id=asset,
                slug=carte["slug"],
                nom=carte["name"],
                rarete=carte.get("rarityTyped") or "?",
                prix_origine_cents=prix,
                devise=devise,
                fin_annonce=offre.get("endDate") or "",
                rail=RAIL_SOLANA if carte.get("solanaAddress") else RAIL_HISTORIQUE,
                retenue=motif is None,
                motif=motif,
            )
        )
    return lignes


class PrixInvalide(ValueError):
    """Un prix proposé ne tient pas debout : on s'arrête plutôt que de deviner."""


def appliquer_prix(lignes: list[Ligne], prix_par_asset: dict[str, int]) -> list[Ligne]:
    """Greffe les prix décidés par l'utilisateur sur les lignes retenues.

    Le prix est une **entrée explicite**, jamais une valeur calculée ici : les
    12 annonces closes observées n'ont pas trouvé preneur au prix demandé, donc
    toute règle automatique reproduirait l'échec ou brader une carte. Ce module
    valide la forme du prix, il n'en invente aucun.
    """
    sorties: list[Ligne] = []
    for ligne in lignes:
        if not ligne.retenue:
            sorties.append(ligne)
            continue
        propose = prix_par_asset.get(ligne.asset_id)
        if propose is None:
            sorties.append(dataclasses.replace(ligne, retenue=False, motif="aucun prix fourni"))
            continue
        if not isinstance(propose, int) or propose <= 0:
            raise PrixInvalide(
                f"{ligne.nom} : prix proposé {propose!r} — attendu un entier de centimes > 0."
            )
        sorties.append(dataclasses.replace(ligne, prix_propose_cents=propose))
    return sorties


def resume(lignes: list[Ligne]) -> dict[str, int]:
    """Effectifs, toujours affichés à côté des lignes."""
    retenues = [ligne for ligne in lignes if ligne.retenue]
    motifs: dict[str, int] = {}
    for ligne in lignes:
        if ligne.motif:
            motifs[ligne.motif] = motifs.get(ligne.motif, 0) + 1
    return {"total": len(lignes), "retenues": len(retenues), **motifs}


def trouver_annonce_en_cours(
    offres_en_cours: list[dict[str, Any]], reference: str
) -> dict[str, Any]:
    """Retrouve l'annonce en cours d'une carte, par identifiant technique ou par slug.

    Le slug est ce qu'on lit dans l'URL sorare.com, donc c'est ce qu'on a sous la
    main quand on veut retirer une annonce en urgence. L'identifiant technique
    reste accepté parce que c'est lui que produit le plan CSV.

    Échoue si la référence désigne zéro ou plusieurs annonces : annuler « la
    première trouvée » serait retirer une carte au hasard.
    """
    trouvees = [
        offre
        for offre in offres_en_cours
        for carte in (offre.get("senderSide") or {}).get("anyCards") or []
        if reference in (carte.get("assetId"), carte.get("slug"))
    ]
    if not trouvees:
        raise LookupError(
            f"Aucune annonce en cours pour « {reference} ». "
            "Vérifie la référence avec `vitrine inventaire`."
        )
    if len(trouvees) > 1:
        raise LookupError(
            f"{len(trouvees)} annonces en cours correspondent à « {reference} » — "
            "trop ambigu pour en annuler une."
        )
    offre = trouvees[0]
    if not offre.get("blockchainId"):
        raise LookupError(
            f"L'annonce {offre.get('id')} n'a pas de blockchainId : impossible de l'annuler."
        )
    return offre
