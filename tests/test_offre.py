"""Tests des mutations, sans réseau (transport httpx simulé).

Deux dangers sont visés en priorité, parce qu'ils ne ressemblent pas à des
pannes :

- une mutation qui échoue avec un HTTP 200 et un `errors` non vide **dans le
  payload** — on croirait la carte remise en vente ;
- une demande de signature à laquelle on répondrait par `approvals: []` parce
  que « d'habitude c'est vide ».
"""

from __future__ import annotations

import json

import httpx
import pytest

from vitrine import offre as off
from vitrine.client import ClientSorare, ErreurSorare
from vitrine.config import Config
from vitrine.solana_signature import (
    AdresseInattendueError,
    adresse_solana,
    cle_solana_depuis_cle_ethereum,
)

CONFIG = Config(cle_api="k", jwt="j", jwt_aud="a")

# Purement inventée pour les tests — aucune valeur réelle.
_CLE_ETH_TEST = b"\x33" * 32
_ADRESSE_SOLANA_TEST = adresse_solana(cle_solana_depuis_cle_ethereum(_CLE_ETH_TEST))


def _autorisation_solana(fingerprint="fp1", sender=None):
    return {
        "fingerprint": fingerprint,
        "request": {
            "__typename": "SolanaTokenTransferAuthorizationRequest",
            "transferProxyProgramAddress": "ProxyAddr111111111111111111111111111111111",
            "merkleTreeAddress": "TreeAddr1111111111111111111111111111111111",
            "leafIndex": 42,
            "nonce": "7",
            "expirationTimestamp": 1234567890,
            "receiverAddress": "ReceiverAddr111111111111111111111111111111",
            "senderAddress": sender or _ADRESSE_SOLANA_TEST,
            "originator": "OriginatorAddr11111111111111111111111111111",
        },
    }


def client_qui_repond(reponses, appels=None):
    """Client branché sur un transport simulé. `appels` collecte les corps envoyés."""
    file = list(reponses)

    def gerer(requete: httpx.Request) -> httpx.Response:
        if appels is not None:
            appels.append(json.loads(requete.content))
        return httpx.Response(200, json=file.pop(0))

    return ClientSorare(
        CONFIG, transport=httpx.MockTransport(gerer), intervalle_min=0, sleep=lambda _: None
    )


# --- l'échec qui ne ressemble pas à un échec -------------------------------


def test_errors_dans_le_payload_leve_malgre_un_http_200():
    c = client_qui_repond(
        [{"data": {"createSingleSaleOffer": {"tokenOffer": None,
                                             "errors": [{"message": "Price too low"}]}}}]
    )
    with pytest.raises(ErreurSorare, match="Price too low"):
        off.creer_annonce(c, "0xAAA", 50)


def test_payload_absent_leve():
    c = client_qui_repond([{"data": {}}])
    with pytest.raises(ErreurSorare, match="payload absent"):
        off.creer_annonce(c, "0xAAA", 50)


def test_annonce_absente_sans_erreur_declaree_leve_aussi():
    """Cas ambigu : ni erreur, ni annonce. On refuse de dire que c'est passé."""
    c = client_qui_repond([{"data": {"createSingleSaleOffer": {"tokenOffer": None,
                                                               "errors": []}}}])
    with pytest.raises(RuntimeError, match="aucune annonce"):
        off.creer_annonce(c, "0xAAA", 50)


# --- la signature ----------------------------------------------------------


def test_preparer_rend_une_liste_vide_quand_rien_n_est_a_signer():
    """Le cas mesuré le 2026-09-19 sur une carte réelle du rail historique."""
    c = client_qui_repond([{"data": {"prepareOffer": {"authorizations": [], "errors": []}}}])
    assert off.preparer(c, "0xAAA", 50) == []


def test_preparer_refuse_un_type_d_autorisation_non_gere():
    """Le garde-fou qui empêche de publier avec `approvals: []` par habitude —
    ici un type que ce module ne sait pas construire (rail StarkEx)."""
    c = client_qui_repond(
        [
            {
                "data": {
                    "prepareOffer": {
                        "authorizations": [
                            {
                                "fingerprint": "fp1",
                                "request": {"__typename": "StarkexTransferAuthorizationRequest"},
                            }
                        ],
                        "errors": [],
                    }
                }
            }
        ]
    )
    with pytest.raises(off.SignatureRequise, match="StarkexTransferAuthorizationRequest"):
        off.preparer(c, "0xAAA", 50)


def test_preparer_refuse_une_autorisation_solana_sans_cle_fournie():
    """Même garde-fou : le type est géré, mais aucune clé n'a été donnée."""
    c = client_qui_repond(
        [{"data": {"prepareOffer": {"authorizations": [_autorisation_solana()], "errors": []}}}]
    )
    with pytest.raises(off.SignatureRequise, match="aucune clé Ethereum"):
        off.preparer(c, "0xAAA", 50)


def test_preparer_signe_une_autorisation_solana_avec_la_cle_fournie():
    c = client_qui_repond(
        [
            {
                "data": {
                    "prepareOffer": {
                        "authorizations": [_autorisation_solana(fingerprint="fp-xyz")],
                        "errors": [],
                    }
                }
            }
        ]
    )
    approbations = off.preparer(c, "0xAAA", 50, cle_privee_eth=_CLE_ETH_TEST)
    assert len(approbations) == 1
    assert approbations[0]["fingerprint"] == "fp-xyz"
    approbation_solana = approbations[0]["solanaTokenTransferApproval"]
    assert approbation_solana["nonce"] == "7"
    assert approbation_solana["expirationTimestamp"] == 1234567890
    assert approbation_solana["signature"]


def test_preparer_leve_si_la_cle_fournie_ne_correspond_pas_au_sender():
    """La clé Ethereum fournie dérive une autre adresse Solana que celle
    attendue — mauvais compte, ou mauvaise clé. `preparer` laisse remonter
    `AdresseInattendueError` telle quelle plutôt que de signer quand même une
    approbation qui de toute façon serait rejetée par Sorare."""
    c = client_qui_repond(
        [
            {
                "data": {
                    "prepareOffer": {
                        "authorizations": [
                            _autorisation_solana(sender="UneAutreAdresseQuiNeCorrespondPas1111111")
                        ],
                        "errors": [],
                    }
                }
            }
        ]
    )
    with pytest.raises(AdresseInattendueError):
        off.preparer(c, "0xAAA", 50, cle_privee_eth=b"\x99" * 32)


# --- la forme des entrées --------------------------------------------------


def test_preparer_envoie_bien_une_vente_simple_et_pas_de_champ_type():
    """`receiveAssetIds: []` + `receiveAmount` = vente simple.

    Le champ `type: SINGLE_SALE_OFFER` des exemples officiels n'existe plus dans
    le schéma : l'envoyer ferait rejeter l'appel.
    """
    appels = []
    c = client_qui_repond(
        [{"data": {"prepareOffer": {"authorizations": [], "errors": []}}}], appels
    )
    off.preparer(c, "0xAAA", 250)
    entree = appels[0]["variables"]["input"]
    assert entree["sendAssetIds"] == ["0xAAA"]
    assert entree["receiveAssetIds"] == []
    assert entree["receiveAmount"] == {"amount": "250", "currency": "EUR"}
    assert "type" not in entree


def test_creer_annonce_envoie_assetId_et_jamais_sendAssetIds():
    """Les deux mutations n'ont pas la même forme d'entrée — piège facile."""
    appels = []
    c = client_qui_repond(
        [{"data": {"createSingleSaleOffer": {
            "tokenOffer": {"id": "o1", "blockchainId": "b1",
                           "startDate": "2026-09-19T10:02:00Z",
                           "endDate": "2026-09-26T10:00:00Z", "status": "opened"},
            "errors": []}}}],
        appels,
    )
    annonce = off.creer_annonce(c, "0xAAA", 250, duree_s=604_800, deal_id="deal-fixe")
    entree = appels[0]["variables"]["input"]
    assert entree["assetId"] == "0xAAA"
    assert "sendAssetIds" not in entree
    assert entree["dealId"] == "deal-fixe"
    assert entree["duration"] == 604_800
    assert entree["approvals"] == []
    assert annonce.id == "o1"
    assert annonce.statut == "opened"


def test_chaque_appel_porte_un_dealId_different():
    """`dealId` doit être unique : deux annonces avec le même seraient un doublon."""
    appels = []
    reponse = {"data": {"createSingleSaleOffer": {
        "tokenOffer": {"id": "o", "blockchainId": "b", "startDate": "", "endDate": "",
                       "status": "opened"}, "errors": []}}}
    c = client_qui_repond([reponse, reponse], appels)
    off.creer_annonce(c, "0xAAA", 250)
    off.creer_annonce(c, "0xBBB", 250)
    assert appels[0]["variables"]["input"]["dealId"] != appels[1]["variables"]["input"]["dealId"]


def test_annuler_utilise_blockchain_id_et_pas_id():
    appels = []
    c = client_qui_repond(
        [{"data": {"cancelOffer": {"tokenOffer": {"id": "o1", "status": "cancelled"},
                                   "errors": []}}}],
        appels,
    )
    assert off.annuler(c, "bc-123") == "cancelled"
    assert appels[0]["variables"]["input"]["blockchainId"] == "bc-123"


def test_annuler_sans_statut_leve_au_lieu_de_rendre_un_point_d_interrogation():
    """Même garde-fou que `creer_annonce` : un état ambigu ne se maquille pas en `"?"`."""
    c = client_qui_repond([{"data": {"cancelOffer": {"tokenOffer": None, "errors": []}}}])
    with pytest.raises(RuntimeError, match="aucun statut"):
        off.annuler(c, "bc-123")
