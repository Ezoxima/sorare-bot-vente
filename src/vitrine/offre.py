"""Les trois mutations de marché. C'est le seul module qui écrit chez Sorare.

## Ce que la mesure a trouvé

Sondé le 2026-09-19 sur une carte réelle du rail historique
(`daniel-jimenez-lopez-1990-03-05-2023-limited-122`, 0,50 €) :

    prepareOffer -> { authorizations: [], errors: [] }

Zéro autorisation, dans les quatre configurations de `settlementCurrencies`
essayées (`[EUR]`, `[WEI]`, `[WEI, EUR]`, argument absent). Témoin joint : un
`assetId` inexistant est rejeté en `NOT_FOUND`, donc l'API lit bien l'entrée et
le vide n'est pas un « je n'ai rien compris ».

Pour une carte du rail Solana, `prepareOffer` réclame en revanche une
`SolanaTokenTransferAuthorizationRequest` (sondé le 2026-09-22 sur une carte
réelle, `Laurin Curda`). Signée par `vitrine.solana_signature`, dont le format
exact (message, dérivation de clé) vient de la doc officielle
https://github.com/sorare/api#examples — voir ce module pour le détail.

## Le garde-fou qui va avec

Un constat sur une carte n'est pas une loi. Si `prepareOffer` réclame un type
d'autorisation que ce module ne sait pas construire — autre rail, autre
devise, autre statut de compte — `preparer` s'arrête net et nomme les types
demandés. L'alternative aurait été d'envoyer `approvals: []` quand même : la
mutation serait refusée, ou pire, accepterait une annonce bancale. Un outil qui
déplace des cartes et de l'argent réel échoue bruyamment.

## Détail mesuré sur la durée

Les 29 annonces en cours du compte affichent toutes `endDate - startDate =
604 680 s`, soit 604 800 (7 jours) moins 120. `startDate` vaut « création
+ 2 minutes » alors que `endDate` se calcule depuis la **création** : la durée
demandée n'est donc pas la durée de visibilité publique. Ces deux minutes sont
une fenêtre d'annulation avant que l'annonce ne devienne visible.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from vitrine import solana_signature as sol
from vitrine.client import ClientSorare

# 7 jours : la durée constatée sur les 29 annonces en cours du compte.
DUREE_DEFAUT_S = 604_800

# Relevé le 2026-09-19 sur les 29 annonces en cours : toutes portent exactement
# `["WEI", "EUR"]`. On reprend ce que le compte fait déjà plutôt que de choisir —
# un réglage à conséquence financière ne s'invente pas. Le prix, lui, reste
# libellé en EUR : `referenceCurrency` valait EUR sur 41 annonces sur 41.
DEVISES_DEFAUT = ["WEI", "EUR"]

MUTATION_PREPARER = """
mutation Preparer($input: prepareOfferInput!) {
  prepareOffer(input: $input) {
    authorizations {
      fingerprint
      request {
        __typename
        ... on SolanaTokenTransferAuthorizationRequest {
          expirationTimestamp
          leafIndex
          merkleTreeAddress
          nonce
          originator
          receiverAddress
          senderAddress
          transferProxyProgramAddress
        }
      }
    }
    errors { message }
  }
}
"""

MUTATION_CREER = """
mutation Creer($input: createSingleSaleOfferInput!) {
  createSingleSaleOffer(input: $input) {
    tokenOffer { id blockchainId startDate endDate status }
    errors { message }
  }
}
"""

MUTATION_ANNULER = """
mutation Annuler($input: cancelOfferInput!) {
  cancelOffer(input: $input) {
    tokenOffer { id status }
    errors { message }
  }
}
"""


class SignatureRequise(RuntimeError):
    """`prepareOffer` réclame une signature que cet outil ne sait pas produire."""


@dataclasses.dataclass(frozen=True)
class AnnonceCreee:
    id: str
    blockchain_id: str | None
    debut: str
    fin: str
    statut: str


def _entree_offre(asset_id: str, prix_cents: int, devises: list[str]) -> dict[str, Any]:
    """Entrée commune à `prepareOffer` et `createSingleSaleOffer`.

    `receiveAssetIds: []` + `receiveAmount` : c'est ce couple qui dit « vente
    simple ». Le champ `type: SINGLE_SALE_OFFER` que montrent les exemples
    officiels **n'existe plus** dans le schéma — le recopier fait rejeter
    l'appel (vérifié contre le schéma servi le 2026-09-19).
    """
    return {
        "sendAssetIds": [asset_id],
        "receiveAssetIds": [],
        "receiveAmount": {"amount": str(prix_cents), "currency": "EUR"},
        "settlementCurrencies": devises,
    }


def preparer(
    client: ClientSorare,
    asset_id: str,
    prix_cents: int,
    *,
    devises: list[str] | None = None,
    cle_privee_eth: bytes | None = None,
) -> list[dict[str, Any]]:
    """Demande les autorisations à signer, et les signe quand ce module sait le
    faire. Ne crée rien, ne déplace rien : seule `creer_annonce` publie.

    `cle_privee_eth` n'est utile que pour le rail Solana (dérive la clé de
    signature — voir `vitrine.solana_signature`) ; les autres rails n'en ont
    jamais eu besoin jusqu'ici. Lève :class:`SignatureRequise` dès qu'un type
    d'autorisation non géré apparaît, ou que la clé manque pour un type qui en
    a besoin.
    """
    entree = _entree_offre(asset_id, prix_cents, devises or DEVISES_DEFAUT)
    entree["clientMutationId"] = f"vitrine-prep-{uuid.uuid4().hex[:12]}"
    payload = client.executer_mutation(MUTATION_PREPARER, {"input": entree}, "prepareOffer")
    autorisations = payload.get("authorizations") or []
    if not autorisations:
        return []

    approbations: list[dict[str, Any]] = []
    non_gerees: list[str] = []
    for autorisation in autorisations:
        requete = autorisation.get("request") or {}
        type_nom = requete.get("__typename", "?")
        if type_nom != "SolanaTokenTransferAuthorizationRequest":
            non_gerees.append(type_nom)
        elif cle_privee_eth is None:
            non_gerees.append(f"{type_nom} (aucune clé Ethereum enregistrée)")
        else:
            approbations.append(
                {
                    "fingerprint": autorisation["fingerprint"],
                    "solanaTokenTransferApproval": sol.signer_autorisation(
                        requete, cle_privee_eth
                    ),
                }
            )

    if non_gerees:
        raise SignatureRequise(
            f"Sorare réclame {len(non_gerees)} autorisation(s) que ce module ne sait pas "
            f"signer pour {asset_id} (type(s) : {', '.join(sorted(set(non_gerees)))}). "
            "Arrêt : ne pas publier une annonce sans les approbations attendues."
        )
    return approbations


def creer_annonce(
    client: ClientSorare,
    asset_id: str,
    prix_cents: int,
    *,
    approbations: list[dict[str, Any]] | None = None,
    duree_s: int = DUREE_DEFAUT_S,
    devises: list[str] | None = None,
    deal_id: str | None = None,
) -> AnnonceCreee:
    """Publie l'annonce. **Écriture réelle** : une carte part en vitrine.

    `deal_id` n'existe que pour les tests — en usage réel il est tiré au hasard,
    comme l'exige l'API (identifiant unique par opération).
    """
    # `createSingleSaleOffer` n'a pas la même forme d'entrée que `prepareOffer` :
    # une seule carte en `assetId` (et non une liste `sendAssetIds`), plus
    # `dealId`, `duration` et `approvals`.
    entree = {
        "assetId": asset_id,
        "receiveAmount": {"amount": str(prix_cents), "currency": "EUR"},
        "settlementCurrencies": devises or DEVISES_DEFAUT,
        "dealId": deal_id or uuid.uuid4().hex,
        "duration": duree_s,
        "approvals": approbations or [],
        "clientMutationId": f"vitrine-creer-{uuid.uuid4().hex[:12]}",
    }

    payload = client.executer_mutation(
        MUTATION_CREER, {"input": entree}, "createSingleSaleOffer"
    )
    offre = payload.get("tokenOffer")
    if not offre:
        raise RuntimeError(
            f"createSingleSaleOffer n'a rendu aucune annonce pour {asset_id} "
            "alors qu'il n'a signalé aucune erreur — état ambigu, vérifier sur sorare.com."
        )
    return AnnonceCreee(
        id=offre["id"],
        blockchain_id=offre.get("blockchainId"),
        debut=offre.get("startDate") or "",
        fin=offre.get("endDate") or "",
        statut=offre.get("status") or "?",
    )


def annuler(client: ClientSorare, blockchain_id: str) -> str:
    """Annule une annonce en cours. `blockchainId`, pas `id` — les deux existent.

    Même garde-fou que `creer_annonce` : un payload sans `errors` mais aussi
    sans statut est un état ambigu, pas un succès muet. On lève plutôt que de
    rendre un `"?"` qui se lirait comme une réponse.
    """
    payload = client.executer_mutation(
        MUTATION_ANNULER,
        {"input": {"blockchainId": blockchain_id,
                   "clientMutationId": f"vitrine-annul-{uuid.uuid4().hex[:12]}"}},
        "cancelOffer",
    )
    statut = (payload.get("tokenOffer") or {}).get("status")
    if not statut:
        raise RuntimeError(
            f"cancelOffer n'a rendu aucun statut pour {blockchain_id} alors qu'il n'a "
            "signalé aucune erreur — état ambigu, vérifier sur sorare.com."
        )
    return statut
