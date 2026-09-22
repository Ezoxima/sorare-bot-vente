"""Tests du prix concurrent — deux pièges visés : se sous-coter soi-même, et
comparer par le mauvais critère de saison.

Cas mesuré le 2026-09-20 sur Matte Smets : la carte la moins chère renvoyée par
l'API pour ce joueur est ma propre annonce. Chaque test qui compte vérifie que
ma propre annonce n'entre jamais dans le calcul, et que « pas de concurrent »
rend `None`, pas 0.

Cas mesuré le 2026-09-21 sur Willi Orbán : comparer par `seasonYear` exact
ratait le vrai concurrent le moins cher (1,12 €, saison 2025) pour une carte
« classic » (saison 2023, hors saison en cours) — le bon critère est
`inSeasonEligible`, qui regroupe toutes les années classic entre elles.
"""

from __future__ import annotations

import httpx
import pytest

from vitrine import marche as mar
from vitrine.client import ClientSorare
from vitrine.config import Config

CONFIG = Config(cle_api="k", jwt="j", jwt_aud="a")


def client_qui_repond(reponses, appels=None):
    file = list(reponses)

    def gerer(requete: httpx.Request) -> httpx.Response:
        if appels is not None:
            appels.append(requete.content.decode())
        return httpx.Response(200, json=file.pop(0))

    return ClientSorare(
        CONFIG, transport=httpx.MockTransport(gerer), intervalle_min=0, sleep=lambda _: None
    )


def noeud(*, vendeur="quelqu-un-d-autre", prix=500, rarete="limited", in_season=False, n_cartes=1):
    cartes = [{"assetId": f"c{i}", "rarityTyped": rarete, "inSeasonEligible": in_season}
              for i in range(n_cartes)]
    return {
        "sender": {"slug": vendeur},
        "receiverSide": {"amounts": {"eurCents": prix}},
        "senderSide": {"anyCards": cartes},
    }


# --- `_offres_comparables` : le cœur pur ------------------------------------


def test_ma_propre_annonce_est_exclue_meme_si_moins_chere():
    """Le cas Matte Smets : ma propre offre est la moins chère et doit disparaître."""
    noeuds = [noeud(vendeur="ezox", prix=849), noeud(vendeur="autre", prix=1200)]
    prix = mar._offres_comparables(noeuds, mon_slug="ezox", rarete="limited", in_season=False)
    assert prix == [1200]


def test_deux_cartes_classic_d_annees_differentes_sont_comparables():
    """Le cœur du correctif du 2026-09-21 : deux cartes « classic » (hors
    saison en cours), même d'années d'origine différentes, sont un même
    marché. C'est le contraire du comportement d'avant la mesure."""
    noeuds = [noeud(in_season=False, prix=112)]  # peu importe l'année exacte
    prix = mar._offres_comparables(noeuds, mon_slug="ezox", rarete="limited", in_season=False)
    assert prix == [112]


def test_une_carte_in_season_ne_se_compare_pas_a_une_carte_classic():
    """Les deux régimes ne se mélangent pas : une carte encore in season
    coexiste avec un marché différent d'une carte devenue classic."""
    noeuds = [noeud(in_season=True, prix=100)]
    prix = mar._offres_comparables(noeuds, mon_slug="ezox", rarete="limited", in_season=False)
    assert prix == []


def test_rarete_differente_n_est_pas_un_concurrent():
    noeuds = [noeud(rarete="rare", prix=100)]
    prix = mar._offres_comparables(noeuds, mon_slug="ezox", rarete="limited", in_season=False)
    assert prix == []


def test_offre_multi_cartes_est_ignoree():
    noeuds = [noeud(n_cartes=2)]
    prix = mar._offres_comparables(noeuds, mon_slug="ezox", rarete="limited", in_season=False)
    assert prix == []


def test_aucun_concurrent_rend_liste_vide_pas_une_erreur():
    assert mar._offres_comparables([], mon_slug="ezox", rarete="limited", in_season=False) == []


def test_statut_in_season_inconnu_ne_matche_jamais():
    """`in_season=None` (statut non résolu) ne doit jamais se comparer à quoi
    que ce soit — prudent par construction, pas de faux concurrent."""
    noeuds = [noeud(in_season=False, prix=100), noeud(in_season=True, prix=100)]
    prix = mar._offres_comparables(noeuds, mon_slug="ezox", rarete="limited", in_season=None)
    assert prix == []


# --- `plus_bas_prix_concurrent` : orchestration -----------------------------


def test_plus_bas_prix_prend_le_minimum_des_concurrents_valides():
    reponse = {
        "data": {
            "tokens": {
                "j0": {
                    "nodes": [
                        noeud(vendeur="ezox", prix=100),  # exclue
                        noeud(vendeur="a", prix=500),
                        noeud(vendeur="b", prix=300),
                    ]
                }
            }
        }
    }
    c = client_qui_repond([reponse])
    cartes = [mar.Carte(slug="s1", player_slug="kylian-mbappe", rarete="limited", in_season=False)]
    resultats = mar.plus_bas_prix_concurrent(c, cartes, mon_slug="ezox")
    assert resultats["s1"].moins_cher_cents == 300
    assert resultats["s1"].n_concurrents == 2


def test_aucune_offre_pour_le_joueur_rend_none_pas_zero():
    reponse = {"data": {"tokens": {"j0": {"nodes": []}}}}
    c = client_qui_repond([reponse])
    cartes = [mar.Carte(slug="s1", player_slug="kylian-mbappe", rarete="limited", in_season=False)]
    resultats = mar.plus_bas_prix_concurrent(c, cartes, mon_slug="ezox")
    assert resultats["s1"].moins_cher_cents is None
    assert resultats["s1"].n_concurrents == 0


def test_uniquement_ma_propre_annonce_rend_none_pas_mon_propre_prix():
    reponse = {"data": {"tokens": {"j0": {"nodes": [noeud(vendeur="ezox")]}}}}
    c = client_qui_repond([reponse])
    cartes = [mar.Carte(slug="s1", player_slug="kylian-mbappe", rarete="limited", in_season=False)]
    resultats = mar.plus_bas_prix_concurrent(c, cartes, mon_slug="ezox")
    assert resultats["s1"].moins_cher_cents is None


def test_deux_cartes_du_meme_joueur_partagent_un_seul_appel():
    reponse = {"data": {"tokens": {"j0": {"nodes": [noeud(vendeur="a", prix=200)]}}}}
    appels = []
    c = client_qui_repond([reponse], appels)
    cartes = [
        mar.Carte(slug="s1", player_slug="kylian-mbappe", rarete="limited", in_season=False),
        mar.Carte(slug="s2", player_slug="kylian-mbappe", rarete="rare", in_season=False),
    ]
    resultats = mar.plus_bas_prix_concurrent(c, cartes, mon_slug="ezox")
    assert len(appels) == 1
    assert resultats["s1"].moins_cher_cents == 200
    assert resultats["s2"].moins_cher_cents is None  # rareté différente


def test_plus_de_15_joueurs_declenche_un_second_appel():
    reponse_vide = {"data": {"tokens": {f"j{i}": {"nodes": []} for i in range(15)}}}
    reponse_2 = {"data": {"tokens": {"j0": {"nodes": []}}}}
    appels = []
    c = client_qui_repond([reponse_vide, reponse_2], appels)
    cartes = [
        mar.Carte(slug=f"s{i}", player_slug=f"joueur-{i}", rarete="limited", in_season=False)
        for i in range(16)
    ]
    mar.plus_bas_prix_concurrent(c, cartes, mon_slug="ezox")
    assert len(appels) == 2


# --- pagination : une page tronquée ne peut jamais être prise pour le minimum


def test_pagine_jusqu_a_epuisement_avant_de_chercher_le_minimum():
    """Cas réel du 2026-09-21 (Willi Orbán, 40 annonces, plafond 30/page) : la
    première page ne contenait pas la moins chère. Sans pagination, le
    résultat serait 1.92€ au lieu du vrai minimum 1.50€."""
    page_1 = {
        "data": {
            "tokens": {
                "j0": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                    "nodes": [noeud(vendeur="satonio", prix=192)],
                }
            }
        }
    }
    page_2 = {
        "data": {
            "tokens": {
                "liveSingleSaleOffers": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [noeud(vendeur="ni-bo13", prix=150)],
                }
            }
        }
    }
    appels = []
    c = client_qui_repond([page_1, page_2], appels)
    cartes = [mar.Carte(slug="s1", player_slug="willi-orban", rarete="limited", in_season=False)]
    resultats = mar.plus_bas_prix_concurrent(c, cartes, mon_slug="ezox")
    assert resultats["s1"].moins_cher_cents == 150
    assert resultats["s1"].n_concurrents == 2
    assert len(appels) == 2  # la page 1 (batch) + une page supplémentaire


def test_sans_hasnextpage_aucun_appel_supplementaire():
    """Le cas courant (joueur avec peu d'annonces) ne doit rien coûter de plus."""
    reponse = {
        "data": {
            "tokens": {
                "j0": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [noeud(vendeur="a", prix=200)],
                }
            }
        }
    }
    appels = []
    c = client_qui_repond([reponse], appels)
    cartes = [mar.Carte(slug="s1", player_slug="kylian-mbappe", rarete="limited", in_season=False)]
    resultats = mar.plus_bas_prix_concurrent(c, cartes, mon_slug="ezox")
    assert resultats["s1"].moins_cher_cents == 200
    assert len(appels) == 1


def test_pagination_s_arrete_au_plafond_de_pages():
    page_avec_suite = {
        "data": {
            "tokens": {
                "liveSingleSaleOffers": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "encore"},
                    "nodes": [],
                }
            }
        }
    }
    page_1 = {
        "data": {
            "tokens": {
                "j0": {
                    "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                    "nodes": [noeud(vendeur="a", prix=999)],
                }
            }
        }
    }
    appels = []
    c = client_qui_repond(
        [page_1] + [page_avec_suite] * mar.MAX_PAGES_SUPPLEMENTAIRES, appels
    )
    cartes = [mar.Carte(slug="s1", player_slug="kylian-mbappe", rarete="limited", in_season=False)]
    mar.plus_bas_prix_concurrent(c, cartes, mon_slug="ezox")
    assert len(appels) == 1 + mar.MAX_PAGES_SUPPLEMENTAIRES  # s'arrête, ne boucle pas indéfiniment


def test_player_slug_suspect_est_refuse():
    with pytest.raises(ValueError, match="suspect"):
        mar.plus_bas_prix_concurrent(
            client_qui_repond([]),
            [mar.Carte(
                slug="s1", player_slug='x") { __typename', rarete="limited", in_season=False
            )],
            mon_slug="ezox",
        )
