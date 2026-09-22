"""Tests du retrait d'annonce.

Le danger propre à cette commande n'est pas de ne rien faire, c'est de retirer
**la mauvaise carte**. Une recherche qui rendrait « la première trouvée » sur une
référence ambiguë dépublierait une annonce au hasard, sans que rien ne le
signale. D'où l'échec sur zéro comme sur plusieurs résultats.
"""

from __future__ import annotations

import argparse

import pytest

from vitrine import cli
from vitrine import plan as pl


def offre_en_cours(asset="0xAAA", slug="dani-2023-limited-122", bc="bc-1", nom="Dani"):
    return {
        "id": f"o-{asset}",
        "blockchainId": bc,
        "startDate": "2026-09-19T10:02:00Z",
        "endDate": "2026-09-26T10:00:00Z",
        "status": "opened",
        "receiverSide": {"amounts": {"referenceCurrency": "EUR", "eurCents": 100}},
        "senderSide": {"anyCards": [{"assetId": asset, "slug": slug, "name": nom}]},
    }


# --- la recherche (cœur pur) -----------------------------------------------


def test_recherche_par_identifiant_technique():
    trouvee = pl.trouver_annonce_en_cours([offre_en_cours()], "0xAAA")
    assert trouvee["blockchainId"] == "bc-1"


def test_recherche_par_slug_celui_de_l_url_sorare():
    trouvee = pl.trouver_annonce_en_cours([offre_en_cours()], "dani-2023-limited-122")
    assert trouvee["blockchainId"] == "bc-1"


def test_reference_inconnue_echoue_au_lieu_de_rendre_n_importe_quoi():
    with pytest.raises(LookupError, match="Aucune annonce en cours"):
        pl.trouver_annonce_en_cours([offre_en_cours()], "0xZZZ")


def test_reference_ambigue_echoue_plutot_que_de_choisir():
    """Deux annonces pour la même référence : en retirer une serait tirer au sort."""
    doublon = [offre_en_cours(bc="bc-1"), offre_en_cours(bc="bc-2")]
    with pytest.raises(LookupError, match="2 annonces en cours"):
        pl.trouver_annonce_en_cours(doublon, "0xAAA")


def test_annonce_sans_blockchain_id_echoue():
    """C'est `blockchainId` qui annule, pas `id` : sans lui, rien n'est possible."""
    with pytest.raises(LookupError, match="blockchainId"):
        pl.trouver_annonce_en_cours([offre_en_cours(bc=None)], "0xAAA")


# --- la commande -----------------------------------------------------------


@pytest.fixture
def espion(monkeypatch):
    """Compte les retraits réellement envoyés."""
    retires: list[str] = []

    class FauxClient:
        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

    monkeypatch.setattr(cli, "_client", lambda: FauxClient())
    monkeypatch.setattr(
        cli.inv,
        "lire",
        lambda client, n=50: {"nickname": "Ezox", "en_cours": [offre_en_cours()],
                              "total_en_cours": 1, "terminees_sans_acheteur": [],
                              "total_terminees": 0},
    )
    monkeypatch.setattr(
        cli.off, "annuler", lambda c, bc: retires.append(bc) or "cancelled"
    )
    return retires


def args(**kw):
    return argparse.Namespace(**{"carte": "0xAAA", "executer": False, "n": 50, **kw})


def test_sans_executer_rien_n_est_retire(espion, capsys):
    assert cli.cmd_annuler(args()) == 0
    assert espion == []
    assert "rien n'est annulé" in capsys.readouterr().out


def test_confirmation_incorrecte_laisse_l_annonce_en_ligne(espion, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "oui")  # minuscules : refusé
    assert cli.cmd_annuler(args(executer=True)) == 1
    assert espion == []


def test_confirmation_exacte_retire_la_bonne_annonce(espion, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "OUI")
    assert cli.cmd_annuler(args(executer=True)) == 0
    assert espion == ["bc-1"]


def test_carte_inconnue_echoue_proprement_sans_rien_retirer(espion):
    """`LookupError` ne doit pas remonter brute : le reste du programme sort
    par `SystemExit`, avec un message, pas une traceback."""
    with pytest.raises(SystemExit, match="Aucune annonce en cours"):
        cli.cmd_annuler(args(carte="0xZZZ", executer=True))
    assert espion == []
