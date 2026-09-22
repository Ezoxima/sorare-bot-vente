"""Coût d'acquisition d'une carte, avec sa provenance. N'invente jamais de prix.

Cascade, dans cet ordre — chaque étage ne s'applique que si le précédent n'a
rien trouvé :

1. **API Sorare** — `anyCards(slugs:) { tokenOwner { transferType amounts { eurCents } } }`.
   Le montant payé est directement connu de Sorare pour un achat marché
   primaire ou secondaire.
2. `transferType` dans `{SHARDS, REWARD}` → coût cash nul. Doctrine déjà actée
   côté Pickdeck : l'essence forgée et les gains ne comptent pas comme du cash.
3. Sinon, on cherche la transaction d'origine dans l'historique
   (`user(slug:).trades`, la même source que Pickdeck) :
   - elle ne portait qu'**une seule carte** → c'est le prix exact payé pour
     cette carte, provenance `transaction` ;
   - elle en portait **plusieurs** (achat groupé) → **estimation** : le
     montant total réparti à parts égales entre les cartes reçues. Étiquetée
     `estime` : une carte à ce stade ne se publie **jamais** automatiquement
     (cf. `politique.py`), elle alerte.
4. Transaction introuvable → coût **inconnu**. Jamais de prix par défaut :
   une carte au coût inconnu est bloquée et signalée.

(Une deuxième source, la base Pickdeck, avait été mesurée : elle couvrait des
cartes que l'API ne connaît pas, mais attribue le montant total d'un achat
groupé à chacune des cartes qu'il contient — un piège qu'il fallait revérifier
avant chaque usage. Écartée : le gain de couverture ne justifiait pas la
dépendance à une base externe.)

`inSeasonEligible` voyage avec le coût (pas seulement `seasonYear`) parce que
`marche.py` en a besoin pour comparer correctement : une carte hors saison
(« classic ») se compare à toutes les autres cartes classic de la même
rareté, peu importe leur année exacte — seule une carte encore *in season* se
compare par année précise (expliqué par l'utilisateur le 2026-09-21, confirmé
contre l'API : comparer par `seasonYear` exact sur une carte classic ratait le
vrai concurrent le moins cher).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable
from typing import Any

from vitrine.client import ClientSorare

TRANSFERTS_SANS_COUT = frozenset({"SHARDS", "REWARD"})

PROVENANCE_API = "api"
PROVENANCE_TRANSACTION = "transaction"
PROVENANCE_SANS_COUT = "sans_cout"
PROVENANCE_ESTIME = "estime"
PROVENANCE_INCONNU = "inconnu"

PROVENANCES_FIABLES = frozenset({PROVENANCE_API, PROVENANCE_TRANSACTION, PROVENANCE_SANS_COUT})

REQUETE_COUTS = """
query Couts($slugs: [String!]!) {
  anyCards(slugs: $slugs) {
    slug
    rarityTyped
    seasonYear
    inSeasonEligible
    anyPlayer { slug }
    tokenOwner { transferType amounts { eurCents } }
  }
}
"""

# Reprise réduite de la requête `trades` de Pickdeck
# (sorare_app_v2/backend/app/sync/trades.py) — une lecture du même schéma
# public, pas un import : les deux dépôts ne partagent aucun code (cf. README).
REQUETE_TRANSACTIONS = """
query Transactions($slug: String!, $cursor: String) {
  user(slug: $slug) {
    trades(after: $cursor, sortByEndDate: DESC) {
      pageInfo { hasNextPage endCursor }
      nodes {
        __typename
        ... on TokenPrimaryOffer { anyCards { slug } price { eurCents } }
        ... on TokenAuction {
          anyCards { slug }
          userBuyer { slug }
          bestBid { amounts { eurCents } }
        }
        ... on TokenOffer {
          sender { ... on User { slug } }
          senderSide { anyCards { slug } amounts { eurCents } }
          receiverSide { anyCards { slug } amounts { eurCents } }
        }
      }
    }
  }
}
"""


@dataclasses.dataclass(frozen=True)
class Cout:
    """Le coût résolu d'une carte, jamais séparé de sa provenance ni du reste
    de ce qu'a coûté un aller-retour API — pour que `quotidien.py` n'ait pas à
    interroger l'API une seconde fois pour comparer au marché."""

    slug: str
    cents: int | None
    provenance: str  # api | transaction | sans_cout | estime | inconnu
    transfer_type: str | None = None
    rarete: str | None = None
    saison: str | None = None
    in_season: bool | None = None
    player_slug: str | None = None

    @property
    def fiable_pour_publication(self) -> bool:
        """Une carte au coût estimé ou inconnu ne se publie jamais automatiquement."""
        return self.cents is not None and self.provenance in PROVENANCES_FIABLES


def _acquisition(node: dict[str, Any], user_slug: str) -> tuple[list[str], int | None]:
    """Cartes acquises par `user_slug` dans ce trade + montant total payé (eurCents).

    Miroir réduit de `_acquired_slugs_and_price` (Pickdeck, `trades.py`) : les
    trois mêmes types de transaction, sans le prix USD qui ne sert à rien ici.
    """
    typename = node.get("__typename")
    if typename == "TokenPrimaryOffer":
        slugs = [c["slug"] for c in node.get("anyCards") or [] if c.get("slug")]
        return slugs, (node.get("price") or {}).get("eurCents")
    if typename == "TokenAuction":
        if (node.get("userBuyer") or {}).get("slug") != user_slug:
            return [], None
        slugs = [c["slug"] for c in node.get("anyCards") or [] if c.get("slug")]
        return slugs, ((node.get("bestBid") or {}).get("amounts") or {}).get("eurCents")
    if typename == "TokenOffer":
        sender = (node.get("sender") or {}).get("slug")
        cote_cartes = "receiverSide" if sender == user_slug else "senderSide"
        cote_montant = "senderSide" if sender == user_slug else "receiverSide"
        slugs = [
            c["slug"] for c in (node.get(cote_cartes) or {}).get("anyCards") or [] if c.get("slug")
        ]
        montant = ((node.get(cote_montant) or {}).get("amounts") or {}).get("eurCents")
        return slugs, montant
    return [], None


def trouver_transaction(
    client: ClientSorare, user_slug: str, card_slug: str, *, max_pages: int = 20
) -> tuple[int, list[str]] | None:
    """Cherche la transaction qui a fait acquérir `card_slug` par `user_slug`.

    Rend `(montant_total_cents, cartes_recues)`, ou `None` si la carte n'a
    jamais été acquise par trade (gagnée, forgée) ou n'apparaît dans aucune des
    `max_pages` premières pages (triées `DESC`, donc les plus récentes
    d'abord). Au-delà, on préfère rendre `None` — donc coût inconnu — que de
    paginer indéfiniment pour une seule carte.
    """
    cursor: str | None = None
    for _ in range(max_pages):
        donnees = client.executer(REQUETE_TRANSACTIONS, {"slug": user_slug, "cursor": cursor})
        connexion = (donnees.get("user") or {}).get("trades") or {}
        for node in connexion.get("nodes") or []:
            slugs, montant = _acquisition(node, user_slug)
            if card_slug in slugs and montant is not None:
                return montant, slugs
        page = connexion.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
    return None


def resoudre(
    *,
    slug: str,
    transfer_type: str | None,
    eur_cents_api: int | None,
    rarete: str | None = None,
    saison: str | None = None,
    in_season: bool | None = None,
    player_slug: str | None = None,
    transaction: tuple[int, list[str]] | None = None,
) -> Cout:
    """Cœur pur de la cascade : aucune entrée/sortie, entièrement testable."""

    def _cout(cents: int | None, provenance: str) -> Cout:
        return Cout(slug, cents, provenance, transfer_type, rarete, saison, in_season, player_slug)

    if eur_cents_api is not None:
        return _cout(eur_cents_api, PROVENANCE_API)

    if transfer_type in TRANSFERTS_SANS_COUT:
        return _cout(0, PROVENANCE_SANS_COUT)

    if transaction is not None:
        montant, cartes = transaction
        if len(cartes) == 1:
            # Une seule carte dans la transaction : le montant retrouvé EST le
            # prix payé pour cette carte précise, pas une estimation.
            return _cout(montant, PROVENANCE_TRANSACTION)
        if cartes:
            # Achat groupé : on ne sait pas répartir autrement qu'à parts
            # égales — d'où l'étiquette `estime`, jamais fiable pour publier.
            return _cout(montant // len(cartes), PROVENANCE_ESTIME)

    return _cout(None, PROVENANCE_INCONNU)


def resoudre_couts(
    client: ClientSorare,
    user_slug: str,
    slugs: Iterable[str],
    *,
    taille_lot: int = 40,
) -> dict[str, Cout]:
    """Résout le coût de chaque carte de `slugs`.

    Un seul appel API par lot de `taille_lot` cartes pour `anyCards`. Un appel
    `trades` supplémentaire, par carte, chaque fois que l'API ne donne pas déjà
    le montant directement (peu importe `transferType` : un achat groupé peut
    passer par n'importe quel type de transaction, pas seulement
    `DIRECT_OFFER`) — sauf pour l'essence et les gains, qui n'ont par nature
    aucune transaction à chercher.
    """
    slugs = list(dict.fromkeys(slugs))  # dédoublonne, garde l'ordre
    par_carte: dict[str, dict[str, Any]] = {}
    for i in range(0, len(slugs), taille_lot):
        lot = slugs[i : i + taille_lot]
        if not lot:
            continue
        donnees = client.executer(REQUETE_COUTS, {"slugs": lot})
        for carte in donnees.get("anyCards") or []:
            par_carte[carte["slug"]] = carte

    resultats: dict[str, Cout] = {}
    for slug in slugs:
        carte = par_carte.get(slug) or {}
        proprietaire = carte.get("tokenOwner") or {}
        transfer_type = proprietaire.get("transferType")
        eur_cents_api = (proprietaire.get("amounts") or {}).get("eurCents")

        transaction: tuple[int, list[str]] | None = None
        if eur_cents_api is None and transfer_type not in TRANSFERTS_SANS_COUT:
            transaction = trouver_transaction(client, user_slug, slug)

        resultats[slug] = resoudre(
            slug=slug,
            transfer_type=transfer_type,
            eur_cents_api=eur_cents_api,
            rarete=carte.get("rarityTyped"),
            saison=carte.get("seasonYear"),
            in_season=carte.get("inSeasonEligible"),
            player_slug=(carte.get("anyPlayer") or {}).get("slug"),
            transaction=transaction,
        )
    return resultats
