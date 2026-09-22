"""Lecture des annonces de l'utilisateur. Aucune écriture.

Deux champs de l'API font tout le travail, et ils évitent de balayer la
collection pour reconstituer quoi que ce soit :

- `endedWithNoBuyerSingleSaleTokenOffers` — les annonces closes sans acheteur,
  c'est-à-dire littéralement la file des candidates à la remise en vente ;
- `liveSingleSaleTokenOffers` — les annonces en cours, utiles pour deux raisons :
  repérer celles qui expirent, et surtout **ne pas reproposer une carte déjà
  remise en vente à la main**.

⚠️ Le prix est sur `receiverSide`, pas sur `senderSide`. Mesuré le 2026-09-19 sur
41 annonces réelles : `senderSide.amounts` vaut **zéro** partout — c'est le côté
qui envoie la carte, donc qui n'envoie pas d'argent. Lire le mauvais côté donne
un prix de 0,00 € parfaitement crédible et faux.

Mesuré aussi, même jour, sur les 41 annonces : `referenceCurrency` vaut **EUR**
partout et `wei` est `null`. La conversion depuis les wei n'est donc pas un
chemin nominal ici ; si `eurCents` venait à manquer, on préfère s'arrêter plutôt
que d'inventer un taux de change.
"""

from __future__ import annotations

from typing import Any

from vitrine.client import ClientSorare

CHAMPS_OFFRE = """
  id
  blockchainId
  startDate
  endDate
  status
  settlementCurrencies
  receiverSide { amounts { referenceCurrency eurCents } }
  senderSide {
    anyCards {
      assetId slug name rarityTyped tradeableStatus solanaAddress
      liveSingleSaleOffer { id endDate }
    }
  }
"""

REQUETE_INVENTAIRE = f"""
query Inventaire($sport: [Sport!], $n: Int!) {{
  currentUser {{
    nickname
    slug
    liveSingleSaleTokenOffers(first: $n, sport: $sport, sortByEndDate: ASC) {{
      totalCount
      nodes {{ {CHAMPS_OFFRE} }}
    }}
    endedWithNoBuyerSingleSaleTokenOffers(first: $n, sport: $sport, sortByEndDate: DESC) {{
      totalCount
      nodes {{ {CHAMPS_OFFRE} }}
    }}
  }}
}}
"""


class InventaireVide(RuntimeError):
    """`currentUser` est `null` : l'API ne sait pas qui nous sommes."""


class InventaireTronque(RuntimeError):
    """Plus d'annonces que la page demandée : la liste lue est incomplète."""


def lire(client: ClientSorare, *, sport: str = "FOOTBALL", n: int = 50) -> dict[str, Any]:
    """Rend `{nickname, en_cours, terminees_sans_acheteur, total_*}`.

    `n` plafonne la pagination. On ne pagine pas au-delà volontairement : au
    rythme mesuré (~5 annonces qui expirent par jour), 50 couvre largement.

    Mais une liste tronquée est **dangereuse et non pas seulement incomplète** :
    les annonces en cours servent à repérer les cartes déjà remises en vente à la
    main. Si la troncature en cache une, cette carte redevient « candidate » et
    l'outil publierait un doublon d'annonce sur une carte déjà en vitrine. D'où
    l'arrêt net plutôt qu'un avertissement : le compte observé avait 33 annonces
    en cours le 2026-09-19 et la file grossit.
    """
    donnees = client.executer(REQUETE_INVENTAIRE, {"sport": [sport], "n": n})
    utilisateur = donnees.get("currentUser")
    if utilisateur is None:
        raise InventaireVide(
            "currentUser est null : la clé API seule n'identifie personne. "
            "Il faut un JWT valide (et non expiré) dans .env."
        )
    en_cours = utilisateur["liveSingleSaleTokenOffers"]
    terminees = utilisateur["endedWithNoBuyerSingleSaleTokenOffers"]
    for libelle, connexion in (("en cours", en_cours), ("closes sans acheteur", terminees)):
        if connexion["totalCount"] > len(connexion["nodes"]):
            raise InventaireTronque(
                f"{connexion['totalCount']} annonces {libelle} mais seulement "
                f"{len(connexion['nodes'])} lues (plafond n={n}). Une carte déjà remise en "
                "vente pourrait passer inaperçue et recevoir une seconde annonce. "
                "Relance avec --n plus grand."
            )
    return {
        "nickname": utilisateur["nickname"],
        # `slug` (et non `nickname`) est ce que rend `sender.slug` sur une
        # offre de marché — c'est lui qu'il faut comparer pour reconnaître ses
        # propres annonces (cf. `marche.py`).
        "slug": utilisateur.get("slug", ""),
        "en_cours": en_cours["nodes"],
        "total_en_cours": en_cours["totalCount"],
        "terminees_sans_acheteur": terminees["nodes"],
        "total_terminees": terminees["totalCount"],
    }
