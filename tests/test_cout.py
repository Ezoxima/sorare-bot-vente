"""Tests du coût d'acquisition — la brique dont dépend le plancher.

Une erreur ici coûte de l'argent réel : soit un plancher trop bas (carte vendue
à perte), soit une carte bloquée à tort. Chaque test vise un étage précis de la
cascade, dans l'ordre où `resoudre` les essaie.
"""

from __future__ import annotations

import json

import httpx
import pytest

from vitrine import cout as co
from vitrine.client import ClientSorare
from vitrine.config import Config

CONFIG = Config(cle_api="k", jwt="j", jwt_aud="a")


def client_qui_repond(reponses, appels=None):
    file = list(reponses)

    def gerer(requete: httpx.Request) -> httpx.Response:
        if appels is not None:
            appels.append(json.loads(requete.content))
        return httpx.Response(200, json=file.pop(0))

    return ClientSorare(
        CONFIG, transport=httpx.MockTransport(gerer), intervalle_min=0, sleep=lambda _: None
    )


# --- le cœur pur : `resoudre` -----------------------------------------------


def test_prix_api_prime_sur_tout_le_reste():
    c = co.resoudre(
        slug="s", transfer_type="DIRECT_OFFER", eur_cents_api=500, transaction=(200, ["s"])
    )
    assert c.cents == 500
    assert c.provenance == co.PROVENANCE_API


@pytest.mark.parametrize("transfer_type", ["SHARDS", "REWARD"])
def test_essence_et_gain_sont_un_cout_cash_nul(transfer_type):
    c = co.resoudre(slug="s", transfer_type=transfer_type, eur_cents_api=None)
    assert c.cents == 0
    assert c.provenance == co.PROVENANCE_SANS_COUT
    assert c.fiable_pour_publication


def test_transaction_groupee_est_estimee_a_parts_egales():
    c = co.resoudre(
        slug="s", transfer_type="DIRECT_OFFER", eur_cents_api=None,
        transaction=(900, ["s", "autre-1", "autre-2"]),
    )
    assert c.cents == 300
    assert c.provenance == co.PROVENANCE_ESTIME


def test_une_carte_estimee_n_est_jamais_fiable_pour_publication():
    c = co.resoudre(
        slug="s", transfer_type="DIRECT_OFFER", eur_cents_api=None,
        transaction=(900, ["s", "autre"]),
    )
    assert not c.fiable_pour_publication


def test_transaction_a_une_seule_carte_est_le_prix_exact_pas_une_estimation():
    """Le point demandé : une carte sans prix API mais dont la transaction
    d'origine ne portait qu'elle seule n'a rien à estimer — c'est le prix payé."""
    c = co.resoudre(
        slug="s", transfer_type="TOKEN_AUCTION", eur_cents_api=None, transaction=(900, ["s"])
    )
    assert c.cents == 900
    assert c.provenance == co.PROVENANCE_TRANSACTION
    assert c.fiable_pour_publication


def test_estimation_ne_depend_pas_du_transfer_type():
    """Un achat groupé peut passer par n'importe quel type de transaction, pas
    seulement `DIRECT_OFFER` — la répartition s'applique dans tous les cas."""
    c = co.resoudre(
        slug="s", transfer_type="TOKEN_PRIMARY_OFFER", eur_cents_api=None,
        transaction=(1000, ["s", "autre"]),
    )
    assert c.cents == 500
    assert c.provenance == co.PROVENANCE_ESTIME


def test_sans_aucune_source_le_cout_est_inconnu_jamais_zero_par_defaut():
    c = co.resoudre(slug="s", transfer_type="DIRECT_OFFER", eur_cents_api=None)
    assert c.cents is None
    assert c.provenance == co.PROVENANCE_INCONNU
    assert not c.fiable_pour_publication


def test_transfer_type_inconnu_sans_transaction_retrouvee_est_bloque():
    c = co.resoudre(slug="s", transfer_type="TOKEN_AUCTION", eur_cents_api=None)
    assert c.provenance == co.PROVENANCE_INCONNU


# --- `trouver_transaction` ---------------------------------------------------


def _page_trades(nodes, has_next=False, cursor=None):
    return {
        "data": {
            "user": {
                "trades": {
                    "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                    "nodes": nodes,
                }
            }
        }
    }


def test_trouver_transaction_lit_un_achat_marche_primaire():
    reponse = _page_trades(
        [{"__typename": "TokenPrimaryOffer", "anyCards": [{"slug": "s"}],
          "price": {"eurCents": 500}}]
    )
    c = client_qui_repond([reponse])
    assert co.trouver_transaction(c, "ezox", "s") == (500, ["s"])


def test_trouver_transaction_ignore_une_enchere_gagnee_par_quelqu_un_d_autre():
    reponse = _page_trades(
        [{"__typename": "TokenAuction", "anyCards": [{"slug": "s"}],
          "userBuyer": {"slug": "quelqu-un-d-autre"}, "bestBid": {"amounts": {"eurCents": 500}}}]
    )
    c = client_qui_repond([reponse])
    assert co.trouver_transaction(c, "ezox", "s") is None


def test_trouver_transaction_lit_un_direct_offer_ou_je_suis_receveur():
    """Émetteur = « vendeur » : j'acquiers `senderSide.anyCards`, je paie via
    `receiverSide.amounts` — c'est ce que je donne en échange (D24, bidirectionnel)."""
    reponse = _page_trades(
        [{"__typename": "TokenOffer", "sender": {"slug": "vendeur"},
          "senderSide": {"anyCards": [{"slug": "s"}], "amounts": {"eurCents": 0}},
          "receiverSide": {"anyCards": [], "amounts": {"eurCents": 400}}}]
    )
    c = client_qui_repond([reponse])
    assert co.trouver_transaction(c, "ezox", "s") == (400, ["s"])


def test_trouver_transaction_pagine_jusqu_a_trouver():
    p1 = _page_trades([{"__typename": "TokenPrimaryOffer", "anyCards": [{"slug": "autre"}],
                        "price": {"eurCents": 1}}], has_next=True, cursor="c2")
    p2 = _page_trades([{"__typename": "TokenPrimaryOffer", "anyCards": [{"slug": "s"}],
                        "price": {"eurCents": 700}}])
    c = client_qui_repond([p1, p2])
    assert co.trouver_transaction(c, "ezox", "s") == (700, ["s"])


def test_trouver_transaction_s_arrete_au_plafond_de_pages():
    page = _page_trades([{"__typename": "TokenPrimaryOffer", "anyCards": [{"slug": "autre"}],
                          "price": {"eurCents": 1}}], has_next=True, cursor="c")
    c = client_qui_repond([page, page])
    assert co.trouver_transaction(c, "ezox", "s", max_pages=2) is None


def test_carte_gagnee_ou_forgee_n_a_aucune_transaction():
    c = client_qui_repond([_page_trades([])])
    assert co.trouver_transaction(c, "ezox", "s") is None


# --- `resoudre_couts` : orchestration ---------------------------------------


def test_resoudre_couts_lit_le_prix_api_sans_appel_supplementaire():
    reponse = {
        "data": {
            "anyCards": [
                {"slug": "s1", "rarityTyped": "limited", "seasonYear": "2023",
                 "anyPlayer": {"slug": "kylian-mbappe"},
                 "tokenOwner": {"transferType": "TOKEN_AUCTION", "amounts": {"eurCents": 250}}}
            ]
        }
    }
    c = client_qui_repond([reponse])
    couts = co.resoudre_couts(c, "ezox", ["s1"])
    assert couts["s1"].cents == 250
    assert couts["s1"].provenance == co.PROVENANCE_API
    assert couts["s1"].player_slug == "kylian-mbappe"
    assert couts["s1"].rarete == "limited"
    assert couts["s1"].saison == "2023"


def test_resoudre_couts_essence_ne_declenche_aucun_appel_trades():
    reponse = {
        "data": {
            "anyCards": [
                {"slug": "s1", "rarityTyped": "limited", "seasonYear": "2023", "anyPlayer": None,
                 "tokenOwner": {"transferType": "SHARDS", "amounts": {"eurCents": None}}}
            ]
        }
    }
    c = client_qui_repond([reponse])  # une seule réponse : lever si un 2e appel est tenté
    couts = co.resoudre_couts(c, "ezox", ["s1"])
    assert couts["s1"].cents == 0
    assert couts["s1"].provenance == co.PROVENANCE_SANS_COUT


def test_resoudre_couts_sans_montant_api_va_chercher_la_transaction():
    reponse_cartes = {
        "data": {
            "anyCards": [
                {"slug": "s1", "rarityTyped": "limited", "seasonYear": "2023", "anyPlayer": None,
                 "tokenOwner": {"transferType": "DIRECT_OFFER", "amounts": {"eurCents": None}}}
            ]
        }
    }
    reponse_trades = _page_trades(
        [{"__typename": "TokenPrimaryOffer", "anyCards": [{"slug": "s1"}, {"slug": "s2"}],
          "price": {"eurCents": 1000}}]
    )
    c = client_qui_repond([reponse_cartes, reponse_trades])
    couts = co.resoudre_couts(c, "ezox", ["s1"])
    assert couts["s1"].cents == 500
    assert couts["s1"].provenance == co.PROVENANCE_ESTIME


def test_resoudre_couts_cherche_la_transaction_meme_hors_direct_offer():
    """Le point demandé : un achat groupé ne se signale pas forcément par
    `DIRECT_OFFER` — la recherche de transaction doit s'appliquer à tout
    `transferType` dès que l'API ne rend aucun montant, pas seulement à celui-là."""
    reponse_cartes = {
        "data": {
            "anyCards": [
                {"slug": "s1", "rarityTyped": "limited", "seasonYear": "2023", "anyPlayer": None,
                 "tokenOwner": {"transferType": "TOKEN_AUCTION", "amounts": {"eurCents": None}}}
            ]
        }
    }
    reponse_trades = _page_trades(
        [{"__typename": "TokenPrimaryOffer", "anyCards": [{"slug": "s1"}],
          "price": {"eurCents": 750}}]
    )
    c = client_qui_repond([reponse_cartes, reponse_trades])
    couts = co.resoudre_couts(c, "ezox", ["s1"])
    assert couts["s1"].cents == 750
    assert couts["s1"].provenance == co.PROVENANCE_TRANSACTION


def test_resoudre_couts_carte_absente_de_la_reponse_api_cherche_puis_est_inconnue():
    reponse = {"data": {"anyCards": []}}
    c = client_qui_repond([reponse, _page_trades([])])
    couts = co.resoudre_couts(c, "ezox", ["fantome"])
    assert couts["fantome"].provenance == co.PROVENANCE_INCONNU


def test_resoudre_couts_deduplique_les_slugs_en_entree():
    reponse = {
        "data": {
            "anyCards": [
                {"slug": "s1", "rarityTyped": "limited", "seasonYear": "2023",
                 "anyPlayer": None,
                 "tokenOwner": {"transferType": "TOKEN_AUCTION", "amounts": {"eurCents": 100}}}
            ]
        }
    }
    appels = []
    c = client_qui_repond([reponse], appels)
    co.resoudre_couts(c, "ezox", ["s1", "s1", "s1"])
    assert appels[0]["variables"]["slugs"] == ["s1"]


def test_resoudre_couts_sans_transfer_type_ni_montant_cherche_puis_est_inconnu():
    reponse = {
        "data": {
            "anyCards": [
                {"slug": "s1", "rarityTyped": "limited", "seasonYear": "2023", "anyPlayer": None,
                 "tokenOwner": {"transferType": None, "amounts": {"eurCents": None}}}
            ]
        }
    }
    c = client_qui_repond([reponse, _page_trades([])])
    couts = co.resoudre_couts(c, "ezox", ["s1"])
    assert couts["s1"].provenance == co.PROVENANCE_INCONNU
