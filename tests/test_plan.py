"""Tests du cœur pur : qui est remettable en vente, et à quel prix.

Chaque test vise une façon précise de se tromper qui a été observée sur les
données réelles du 2026-09-19, ou qui produirait un dégât silencieux. Un test qui
passerait aussi avec la version fautive ne servirait à rien : ici, lire le
mauvais côté du prix, oublier l'un des deux signaux « déjà en vente » ou ignorer
un doublon font chacun tomber au moins un test.
"""

from __future__ import annotations

import pytest

from vitrine import plan as pl


def offre(
    *,
    asset="0xAAA",
    nom="Carte Test",
    prix_cents=250,
    cessible="YES",
    fin="2026-09-10T12:00:00Z",
    solana=None,
    live_offer=None,
    cartes=None,
):
    """Une annonce close, à la forme exacte servie par l'API.

    Note le `senderSide.amounts` à zéro : ce n'est pas un détail de fixture,
    c'est ce que renvoie réellement l'API — le côté qui envoie la carte n'envoie
    pas d'argent.
    """
    carte = {
        "assetId": asset,
        "slug": "carte-test-2025-limited-1",
        "name": nom,
        "rarityTyped": "limited",
        "tradeableStatus": cessible,
        "solanaAddress": solana,
        "liveSingleSaleOffer": live_offer,
    }
    return {
        "id": f"offre-{asset}",
        "blockchainId": f"bc-{asset}",
        "endDate": fin,
        "receiverSide": {"amounts": {"referenceCurrency": "EUR", "eurCents": prix_cents}},
        "senderSide": {
            "amounts": {"referenceCurrency": "EUR", "eurCents": 0},
            "anyCards": cartes if cartes is not None else [carte],
        },
    }


def test_le_prix_se_lit_sur_receiver_side_pas_sender_side():
    # senderSide vaut 0 sur les 41 annonces réelles mesurées : une lecture du
    # mauvais côté rendrait 0,00 € sans rien casser d'autre.
    (ligne,) = pl.analyser([offre(prix_cents=250)], [])
    assert ligne.prix_origine_cents == 250
    assert ligne.prix_origine_eur == 2.50


def test_carte_non_cessible_ecartee_avec_son_motif():
    (ligne,) = pl.analyser([offre(cessible="NO")], [])
    assert not ligne.retenue
    assert "non cessible" in ligne.motif


def test_deja_en_vente_detecte_par_le_champ_de_la_carte():
    (ligne,) = pl.analyser([offre(live_offer={"id": "x", "endDate": "2026-09-25T00:00:00Z"})], [])
    assert not ligne.retenue
    assert ligne.motif == "déjà remise en vente"


def test_deja_en_vente_detecte_par_la_presence_dans_les_annonces_en_cours():
    """Second signal, indépendant du premier.

    Sur les données réelles, une carte déjà relistée à la main peut apparaître
    dans les annonces en cours sans que `liveSingleSaleOffer` soit rempli sur
    l'exemplaire rendu par la file close. Ne vérifier qu'un seul des deux
    signaux publierait un doublon d'annonce.
    """
    close = offre(asset="0xBBB", live_offer=None)
    en_cours = [offre(asset="0xBBB")]
    (ligne,) = pl.analyser([close], en_cours)
    assert not ligne.retenue
    assert ligne.motif == "déjà remise en vente"


def test_carte_ayant_echoue_deux_fois_n_apparait_qu_une_fois_et_au_dernier_prix():
    vieille = offre(asset="0xCCC", prix_cents=500, fin="2026-07-01T00:00:00Z")
    recente = offre(asset="0xCCC", prix_cents=300, fin="2026-09-01T00:00:00Z")
    lignes = pl.analyser([vieille, recente], [])
    assert len(lignes) == 1
    assert lignes[0].prix_origine_cents == 300


def test_offre_portant_plusieurs_cartes_est_ignoree():
    lot = offre(cartes=[{"assetId": "1"}, {"assetId": "2"}])
    assert pl.analyser([lot], []) == []


def test_offre_sans_carte_est_ignoree():
    assert pl.analyser([offre(cartes=[])], []) == []


def test_prix_illisible_ne_devient_jamais_zero():
    muette = offre()
    muette["receiverSide"]["amounts"] = {"referenceCurrency": "EUR", "eurCents": None}
    (ligne,) = pl.analyser([muette], [])
    assert not ligne.retenue
    assert "illisible" in ligne.motif
    assert ligne.prix_origine_cents is None  # et surtout pas 0


def test_rail_deduit_de_la_presence_d_une_adresse_solana():
    (sol,) = pl.analyser([offre(solana="jRK5VNDUvDMf")], [])
    (vieux,) = pl.analyser([offre(solana=None)], [])
    assert sol.rail == pl.RAIL_SOLANA
    assert vieux.rail == pl.RAIL_HISTORIQUE


def test_appliquer_prix_greffe_le_prix_sur_les_lignes_retenues():
    lignes = pl.analyser([offre(asset="0xDDD")], [])
    (ligne,) = pl.appliquer_prix(lignes, {"0xDDD": 199})
    assert ligne.retenue
    assert ligne.prix_propose_cents == 199


def test_ligne_sans_prix_fourni_sort_du_lot():
    lignes = pl.analyser([offre(asset="0xEEE")], [])
    (ligne,) = pl.appliquer_prix(lignes, {})
    assert not ligne.retenue
    assert ligne.motif == "aucun prix fourni"


@pytest.mark.parametrize("mauvais", [0, -50, 1.5, "0.45", None])
def test_appliquer_prix_refuse_tout_prix_qui_n_est_pas_un_entier_positif(mauvais):
    lignes = pl.analyser([offre(asset="0xFFF")], [])
    if mauvais is None:
        # `None` = prix absent, déjà couvert : la ligne sort du lot sans lever.
        (ligne,) = pl.appliquer_prix(lignes, {"0xFFF": None})
        assert not ligne.retenue
        return
    with pytest.raises(pl.PrixInvalide):
        pl.appliquer_prix(lignes, {"0xFFF": mauvais})


def test_appliquer_prix_ne_ressuscite_pas_une_ligne_ecartee():
    """Un prix saisi à la main sur une carte non cessible ne doit rien forcer."""
    lignes = pl.analyser([offre(asset="0xGGG", cessible="NO")], [])
    (ligne,) = pl.appliquer_prix(lignes, {"0xGGG": 100})
    assert not ligne.retenue
    assert ligne.prix_propose_cents is None


def test_resume_donne_les_effectifs_par_motif():
    lignes = pl.analyser(
        [
            offre(asset="1"),
            offre(asset="2", cessible="NO"),
            offre(asset="3", live_offer={"id": "x"}),
        ],
        [],
    )
    r = pl.resume(lignes)
    assert r["total"] == 3
    assert r["retenues"] == 1
    assert r["déjà remise en vente"] == 1
