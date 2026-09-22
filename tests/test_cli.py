"""Tests de la commande qui décide de publier — la pièce qui mord le plus fort.

Tout le reste du dépôt peut être juste : si `cmd_relister` publie alors qu'on ne
lui a rien demandé, des cartes partent en vitrine au mauvais prix. D'où quatre
verrous, et un test par verrou qui vérifie qu'**aucune annonce n'est créée** —
pas seulement que le code d'erreur est le bon.

Les tests n'atteignent jamais le réseau : `_lire_inventaire` et `_client` sont
remplacés, et chaque appel à `creer_annonce` est compté. Un compteur à zéro est
l'assertion qui compte.
"""

from __future__ import annotations

import argparse
import pathlib

import pytest

from vitrine import cli
from vitrine import plan as pl


@pytest.fixture
def espion(monkeypatch):
    """Remplace tout ce qui touche au réseau et compte les publications."""
    publiees: list[tuple[str, int]] = []

    carte = {
        "assetId": "0xAAA",
        "slug": "carte-test-2025-limited-1",
        "name": "Carte Test",
        "rarityTyped": "limited",
        "tradeableStatus": "YES",
        "solanaAddress": None,
        "liveSingleSaleOffer": None,
    }
    offre_close = {
        "id": "o1",
        "endDate": "2026-09-10T12:00:00Z",
        "receiverSide": {"amounts": {"referenceCurrency": "EUR", "eurCents": 250}},
        "senderSide": {"anyCards": [carte]},
    }

    def faux_inventaire(n: int = 50):
        lignes = pl.analyser([offre_close], [])
        return "Ezox", lignes, {"total_en_cours": 0, "total_terminees": 1}

    class FauxClient:
        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

    monkeypatch.setattr(cli, "_lire_inventaire", faux_inventaire)
    monkeypatch.setattr(cli, "_client", lambda: FauxClient())
    monkeypatch.setattr(cli.off, "preparer", lambda c, a, p, **kw: [])
    # Aucun test de la CLI ne doit toucher le vrai coffre Windows — la
    # signature elle-même est testée dans test_solana_signature.py / test_offre.py.
    monkeypatch.setattr(cli.cle_ethereum, "lire_cle_privee", lambda: None)
    monkeypatch.setattr(
        cli.off,
        "creer_annonce",
        lambda c, a, p, **kw: publiees.append((a, p))
        or cli.off.AnnonceCreee(id="x", blockchain_id="b", debut="", fin="", statut="opened"),
    )
    return publiees


def plan_csv(tmp_path: pathlib.Path, prix: str = "1,50") -> pathlib.Path:
    chemin = tmp_path / "plan.csv"
    chemin.write_text(
        "asset_id;nom;rarete;rail;fin_annonce;prix_origine_eur;prix_propose_eur;motif\n"
        f"0xAAA;Carte Test;limited;historique;2026-09-10;2.50;{prix};\n",
        encoding="utf-8-sig",
    )
    return chemin


def args(**kw) -> argparse.Namespace:
    base = {"plan": "plan.csv", "executer": False, "max": 1, "duree": 604_800, "n": 50}
    return argparse.Namespace(**{**base, **kw})


# --- verrou 1 : le mode à blanc est le défaut ------------------------------


def test_sans_executer_rien_n_est_publie(espion, tmp_path, capsys):
    chemin = plan_csv(tmp_path)
    assert cli.cmd_relister(args(plan=str(chemin))) == 0
    assert espion == []
    assert "aucune annonce créée" in capsys.readouterr().out


# --- verrou 2 : le plafond --max -------------------------------------------


def test_depasser_max_arrete_avant_toute_publication(espion, tmp_path):
    chemin = tmp_path / "plan.csv"
    chemin.write_text(
        "asset_id;nom;rarete;rail;fin_annonce;prix_origine_eur;prix_propose_eur;motif\n"
        "0xAAA;Carte Test;limited;historique;2026-09-10;2.50;1,50;\n",
        encoding="utf-8-sig",
    )
    with pytest.raises(SystemExit, match="--max vaut 0"):
        cli.cmd_relister(args(plan=str(chemin), executer=True, max=0))
    assert espion == []


# --- verrou 3 : la confirmation tapée --------------------------------------


def test_confirmation_incorrecte_n_envoie_rien(espion, tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "oui")  # pas le nombre attendu
    chemin = plan_csv(tmp_path)
    assert cli.cmd_relister(args(plan=str(chemin), executer=True)) == 1
    assert espion == []


def test_confirmation_vide_n_envoie_rien(espion, tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    chemin = plan_csv(tmp_path)
    assert cli.cmd_relister(args(plan=str(chemin), executer=True)) == 1
    assert espion == []


def test_confirmation_exacte_publie_au_prix_du_plan(espion, tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "1")
    chemin = plan_csv(tmp_path, prix="1,50")
    assert cli.cmd_relister(args(plan=str(chemin), executer=True)) == 0
    # 150 centimes, pas 250 (le prix d'origine) ni 1.5 (des euros).
    assert espion == [("0xAAA", 150)]


# --- verrou 4 : aucune ligne sans prix ne part -----------------------------


def test_une_ligne_sans_prix_ne_part_pas_meme_avec_confirmation(espion, tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "1")
    chemin = plan_csv(tmp_path, prix="")
    assert cli.cmd_relister(args(plan=str(chemin), executer=True)) == 0
    assert espion == []


# --- lecture du plan -------------------------------------------------------


def test_plan_absent_arrete_avant_tout_appel(espion, tmp_path):
    with pytest.raises(SystemExit, match="Plan introuvable"):
        cli.cmd_relister(args(plan=str(tmp_path / "rien.csv")))
    assert espion == []


def test_prix_illisible_arrete_tout(espion, tmp_path):
    chemin = plan_csv(tmp_path, prix="pas un prix")
    with pytest.raises(SystemExit, match="illisible"):
        cli.cmd_relister(args(plan=str(chemin)))
    assert espion == []


def test_prix_negatif_arrete_tout(espion, tmp_path):
    chemin = plan_csv(tmp_path, prix="-1,00")
    with pytest.raises(SystemExit, match="attendu > 0"):
        cli.cmd_relister(args(plan=str(chemin)))
    assert espion == []


def test_deux_lignes_pour_la_meme_carte_arretent_tout(espion, tmp_path):
    """« Dernier gagne » choisirait un montant en silence."""
    chemin = tmp_path / "plan.csv"
    chemin.write_text(
        "asset_id;nom;rarete;rail;fin_annonce;prix_origine_eur;prix_propose_eur;motif\n"
        "0xAAA;Carte Test;limited;historique;2026-09-10;2.50;1,50;\n"
        "0xAAA;Carte Test;limited;historique;2026-09-10;2.50;9,99;\n",
        encoding="utf-8-sig",
    )
    with pytest.raises(SystemExit, match="a déjà un prix"):
        cli.cmd_relister(args(plan=str(chemin)))
    assert espion == []


def test_la_virgule_decimale_francaise_est_acceptee(espion, tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "1")
    chemin = plan_csv(tmp_path, prix="0,45")
    cli.cmd_relister(args(plan=str(chemin), executer=True))
    assert espion == [("0xAAA", 45)]


# --- le drapeau par défaut, au niveau de l'analyseur d'arguments ------------


def test_executer_est_faux_par_defaut_dans_la_ligne_de_commande():
    """Une inversion de `store_true` publierait sans qu'on le demande."""
    parseur_args = []

    def capturer(a):
        parseur_args.append(a)
        return 0

    import sys
    from unittest import mock

    with mock.patch.object(cli, "cmd_relister", capturer), mock.patch.object(sys, "argv", ["x"]):
        cli.main(["relister"])
    assert parseur_args[0].executer is False
    assert parseur_args[0].max == 1
