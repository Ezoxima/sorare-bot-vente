"""Tests du transport et de la lecture d'inventaire.

Le cas le plus coûteux de cette API est testé ici : `currentUser: null` **sans
erreur**. La requête a l'air d'avoir réussi, la liste d'annonces est vide, et un
outil naïf conclut « rien à remettre en vente » alors qu'il n'est simplement pas
authentifié.
"""

from __future__ import annotations

import httpx
import pytest

from vitrine import inventaire as inv
from vitrine.client import ClientSorare, ErreurSorare
from vitrine.config import Config

CONFIG = Config(cle_api="k", jwt="j", jwt_aud="aud-test")


def _client(gerer, **kw):
    return ClientSorare(
        CONFIG,
        transport=httpx.MockTransport(gerer),
        intervalle_min=0,
        sleep=lambda _: None,
        **kw,
    )


def test_current_user_null_leve_au_lieu_de_rendre_une_file_vide():
    c = _client(lambda r: httpx.Response(200, json={"data": {"currentUser": None}}))
    with pytest.raises(inv.InventaireVide, match="n'identifie personne"):
        inv.lire(c)


def test_lecture_nominale_rend_les_deux_files_et_leurs_effectifs():
    reponse = {
        "data": {
            "currentUser": {
                "nickname": "Ezox",
                "liveSingleSaleTokenOffers": {"totalCount": 0, "nodes": []},
                "endedWithNoBuyerSingleSaleTokenOffers": {"totalCount": 0, "nodes": []},
            }
        }
    }
    c = _client(lambda r: httpx.Response(200, json=reponse))
    lu = inv.lire(c)
    assert lu["nickname"] == "Ezox"
    assert lu["total_en_cours"] == 0
    assert lu["total_terminees"] == 0


def test_une_liste_tronquee_arrete_tout_au_lieu_de_passer_pour_complete():
    """Le danger n'est pas de rater une candidate, c'est de publier un doublon.

    Les annonces en cours servent à repérer les cartes déjà remises en vente.
    Si la troncature en cache une, cette carte redevient candidate.
    """
    reponse = {
        "data": {
            "currentUser": {
                "nickname": "Ezox",
                "liveSingleSaleTokenOffers": {"totalCount": 80, "nodes": [{"x": 1}]},
                "endedWithNoBuyerSingleSaleTokenOffers": {"totalCount": 0, "nodes": []},
            }
        }
    }
    c = _client(lambda r: httpx.Response(200, json=reponse))
    with pytest.raises(inv.InventaireTronque, match="80 annonces en cours"):
        inv.lire(c, n=50)


def test_les_deux_entetes_d_authentification_partent_ensemble():
    vues = {}

    def gerer(requete: httpx.Request) -> httpx.Response:
        vues.update(requete.headers)
        return httpx.Response(200, json={"data": {}})

    _client(gerer).executer("query { __typename }")
    assert vues["apikey"] == "k"
    assert vues["authorization"] == "Bearer j"
    assert vues["jwt-aud"] == "aud-test"


def test_sans_jwt_aucun_entete_d_authentification_n_est_envoye():
    """`Authorization` sans `JWT-AUD` est rejeté : les deux vont ensemble ou aucun."""
    vues = {}

    def gerer(requete: httpx.Request) -> httpx.Response:
        vues.update(requete.headers)
        return httpx.Response(200, json={"data": {}})

    ClientSorare(
        Config(cle_api="k", jwt="j", jwt_aud=""),
        transport=httpx.MockTransport(gerer),
        intervalle_min=0,
    ).executer("query { __typename }")
    assert "authorization" not in vues


def test_un_429_est_reessaye_en_respectant_retry_after():
    attentes: list[float] = []
    essais = {"n": 0}

    def gerer(_: httpx.Request) -> httpx.Response:
        essais["n"] += 1
        if essais["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={})
        return httpx.Response(200, json={"data": {"ok": True}})

    c = ClientSorare(
        CONFIG,
        transport=httpx.MockTransport(gerer),
        intervalle_min=0,
        sleep=attentes.append,
    )
    assert c.executer("query { __typename }") == {"ok": True}
    # 7 s, pas le backoff maison : c'est l'API qui sait quand elle est prête.
    assert attentes == [7.0]


def test_erreurs_de_niveau_requete_levent():
    c = _client(
        lambda r: httpx.Response(200, json={"errors": [{"message": "(=) not found"}]})
    )
    with pytest.raises(ErreurSorare, match="not found"):
        c.executer("query { __typename }")


def test_un_4xx_definitif_ne_est_pas_reessaye():
    essais = {"n": 0}

    def gerer(_: httpx.Request) -> httpx.Response:
        essais["n"] += 1
        return httpx.Response(422, text="requête invalide")

    with pytest.raises(ErreurSorare, match="422"):
        _client(gerer).executer("query { __typename }")
    assert essais["n"] == 1


def test_exiger_identite_arrete_le_programme_sans_jwt():
    with pytest.raises(SystemExit, match="SORARE_JWT"):
        Config(cle_api="k", jwt="", jwt_aud="").exiger_identite()
