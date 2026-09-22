"""Le prix concurrent le plus bas pour une carte, sans jamais compter mes propres annonces.

Mesuré le 2026-09-20 : `lowestPriceAnyCard` compare des cartes de saisons
différentes entre elles, ce qui n'a pas de sens économique. Ce module
interroge donc `tokens.liveSingleSaleOffers(playerSlug:)` et filtre lui-même —
mais pas sur l'année de saison exacte (`seasonYear`), qui n'est **pas** le bon
critère (piège corrigé le 2026-09-21, cf. ci-dessous).

⚠️ **Le bon critère est `inSeasonEligible`, pas `seasonYear`** (expliqué par
l'utilisateur le 2026-09-21, confirmé contre l'API sur Willi Orbán). Sorare
distingue deux régimes :

- une carte **encore *in season*** (`inSeasonEligible: true`) ne se compare
  qu'à des cartes de la même année précise — il n'y en a qu'une en cours à la
  fois, donc rareté + `inSeasonEligible=true` suffit à identifier la bonne
  année sans avoir besoin de comparer `seasonYear` en plus ;
- une carte **« classic »** (`inSeasonEligible: false`, la grande majorité du
  stock une fois la saison terminée) se compare à **toutes** les autres cartes
  classic de la même rareté, **peu importe leur année d'origine**. L'API elle-
  même le confirme : `lowestPriceAnyCard(inSeason: Boolean, rarity: Rarity)`
  ne prend pas de paramètre d'année, seulement ce booléen.

Comparer par `seasonYear` exact sur une carte classic est donc trop strict et
rate les vrais concurrents : mesuré sur Willi Orbán (classic, 2023), le
minimum réel toutes années classic confondues était 1,12 €, contre 1,50 €/
1,92 € en ne regardant que l'année 2023 — un écart de prix visé qui aurait
laissé filer une vraie occasion de se repositionner plus bas.

Deuxième piège, mesuré le même jour sur Matte Smets : la carte la moins chère
renvoyée par l'API pour ce joueur **est ma propre annonce**, à 8,49 €. Une
règle naïve se sous-coterait donc elle-même chaque jour jusqu'au plancher. Ce
module écarte systématiquement les annonces dont `sender` est l'utilisateur.

⚠️ **Troisième piège, mesuré le 2026-09-21** : `liveSingleSaleOffers` est
« sorted by updated time », pas par prix (c'est écrit dans la doc du champ
lui-même). Sur Willi Orbán, mesuré à 40 annonces en cours pour un plafond de
30 par page : la 30e page laissait croire que 1,92 € était le plus bas, alors
qu'une annonce à 1,50 € existait bel et bien, simplement pas parmi les 30
dernières mises à jour. Une page tronquée ne peut donc **jamais** être prise
pour le minimum réel — ce module pagine jusqu'à épuisement (`hasNextPage`)
avant de chercher un minimum, pas seulement sur la première page.

Coût mesuré : ~1758 de complexité pour 15 annonces d'un joueur (plafond 30 000
par requête) → environ 15 joueurs par appel, groupés par alias GraphQL. La
pagination supplémentaire ne coûte qu'aux joueurs qui en ont réellement besoin
(`hasNextPage`), les autres restent à un seul appel partagé.

Absence de concurrent comparable = `moins_cher_cents is None`, un résultat à
part entière et non un zéro : `politique.py` en fait un verdict `LAISSER`
(« pas de marché à battre »), jamais un prix à viser.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from vitrine.client import ClientSorare

JOUEURS_PAR_APPEL = 15
PREMIERES_OFFRES = 30
MAX_PAGES_SUPPLEMENTAIRES = 20  # garde-fou : pas de pagination sans fin sur un joueur atypique

_SLUG_VALIDE = re.compile(r"^[a-z0-9-]+$")

REQUETE_PAGE = """
query MarchePage($slug: String!, $cursor: String, $first: Int!) {
  tokens {
    liveSingleSaleOffers(playerSlug: $slug, first: $first, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        sender { ... on User { slug } }
        receiverSide { amounts { eurCents } }
        senderSide { anyCards { assetId rarityTyped inSeasonEligible } }
      }
    }
  }
}
"""


@dataclasses.dataclass(frozen=True)
class Carte:
    """Ce qu'il faut savoir d'une carte pour la comparer au marché."""

    slug: str
    player_slug: str
    rarete: str
    # cf. docstring du module : le bon critère, pas `seasonYear`. `None` = statut
    # inconnu → aucune annonce ne pourra jamais matcher, prudent par construction.
    in_season: bool | None


@dataclasses.dataclass(frozen=True)
class PrixConcurrent:
    carte_slug: str
    moins_cher_cents: int | None  # None = aucun concurrent comparable
    n_concurrents: int


def _requete(player_slugs: list[str]) -> str:
    """`tokens` n'est appelé qu'une fois : Sorare rejette un root field répété,
    même sous des alias distincts (`Duplicated root field: tokens`, mesuré en
    conditions réelles le 2026-09-20). L'alias porte donc sur `liveSingleSaleOffers`,
    à l'intérieur — la forme standard pour sélectionner un même champ plusieurs
    fois avec des arguments différents."""
    blocs = []
    for i, slug in enumerate(player_slugs):
        if not _SLUG_VALIDE.match(slug):
            # Les slugs viennent de nos propres cartes (API Sorare), donc a
            # priori sûrs — refus par prudence plutôt que d'interpoler une
            # chaîne non validée dans le texte de la requête GraphQL.
            raise ValueError(f"playerSlug suspect, refusé par prudence : {slug!r}")
        blocs.append(
            f'j{i}: liveSingleSaleOffers(playerSlug: "{slug}", '
            f"first: {PREMIERES_OFFRES}) {{ "
            "pageInfo { hasNextPage endCursor } "
            "nodes { "
            "sender { ... on User { slug } } "
            "receiverSide { amounts { eurCents } } "
            "senderSide { anyCards { assetId rarityTyped inSeasonEligible } } "
            "} }"
        )
    return "query Marche { tokens { " + " ".join(blocs) + " } }"


def _offres_comparables(
    noeuds: list[dict[str, Any]], *, mon_slug: str, rarete: str, in_season: bool | None
) -> list[int]:
    """Prix (cents) des annonces d'autres vendeurs, à rareté et statut
    classic/in-season égaux (cf. docstring du module — pas `seasonYear`)."""
    prix: list[int] = []
    for noeud in noeuds:
        vendeur = (noeud.get("sender") or {}).get("slug")
        if vendeur == mon_slug:
            continue  # le piège Matte Smets : ne jamais se comparer à soi-même
        cartes = (noeud.get("senderSide") or {}).get("anyCards") or []
        if len(cartes) != 1:
            continue
        carte = cartes[0]
        if carte.get("rarityTyped") != rarete or carte.get("inSeasonEligible") != in_season:
            continue
        montant = ((noeud.get("receiverSide") or {}).get("amounts") or {}).get("eurCents")
        if montant is not None:
            prix.append(montant)
    return prix


def _pages_suivantes(
    client: ClientSorare, player_slug: str, premiere_page_info: dict[str, Any]
) -> list[dict[str, Any]]:
    """Continue la pagination d'un joueur au-delà de la première page, tant que
    `hasNextPage` le demande. Rend uniquement les nœuds des pages *suivantes* —
    la première page a déjà été lue par l'appelant.

    Indispensable : `liveSingleSaleOffers` trie par date de mise à jour, pas
    par prix (cf. docstring du module), donc s'arrêter à la première page
    tronquée peut faire manquer l'annonce la moins chère.
    """
    noeuds: list[dict[str, Any]] = []
    page = premiere_page_info
    cursor = page.get("endCursor")
    essais = 0
    while page.get("hasNextPage") and essais < MAX_PAGES_SUPPLEMENTAIRES:
        donnees = client.executer(
            REQUETE_PAGE, {"slug": player_slug, "cursor": cursor, "first": PREMIERES_OFFRES}
        )
        connexion = ((donnees.get("tokens") or {}).get("liveSingleSaleOffers")) or {}
        noeuds.extend(connexion.get("nodes") or [])
        page = connexion.get("pageInfo") or {}
        cursor = page.get("endCursor")
        essais += 1
    return noeuds


def plus_bas_prix_concurrent(
    client: ClientSorare, cartes: list[Carte], *, mon_slug: str
) -> dict[str, PrixConcurrent]:
    """Un `PrixConcurrent` par carte de `cartes`, mes propres annonces exclues."""
    par_joueur: dict[str, list[Carte]] = {}
    for carte in cartes:
        par_joueur.setdefault(carte.player_slug, []).append(carte)
    joueurs = list(par_joueur)

    resultats: dict[str, PrixConcurrent] = {}
    for i in range(0, len(joueurs), JOUEURS_PAR_APPEL):
        lot = joueurs[i : i + JOUEURS_PAR_APPEL]
        donnees = client.executer(_requete(lot))
        tokens = donnees.get("tokens") or {}
        for j, joueur in enumerate(lot):
            connexion = tokens.get(f"j{j}") or {}
            noeuds = list(connexion.get("nodes") or [])
            noeuds.extend(_pages_suivantes(client, joueur, connexion.get("pageInfo") or {}))
            for carte in par_joueur[joueur]:
                prix = _offres_comparables(
                    noeuds, mon_slug=mon_slug, rarete=carte.rarete, in_season=carte.in_season
                )
                resultats[carte.slug] = PrixConcurrent(
                    carte_slug=carte.slug,
                    moins_cher_cents=min(prix) if prix else None,
                    n_concurrents=len(prix),
                )
    return resultats
