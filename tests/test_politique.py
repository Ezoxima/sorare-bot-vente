"""Tests du cœur de décision — le plus gros lot de tests du dépôt.

Chaque garde-fou du plan a son test dédié, écrit pour tomber si on le casse :
ne pas exclure ses propres annonces (`marche.py`), ignorer le plancher,
publier malgré un coût inconnu, comparer des saisons différentes, dépasser le
quota du jour. Ici, ce sont les quatre derniers qui se testent directement sur
`decider`/`appliquer_quota` ; le premier est couvert dans `test_marche.py`.
"""

from __future__ import annotations

import pytest

from vitrine.cout import Cout
from vitrine.marche import PrixConcurrent
from vitrine.politique import Verdict, appliquer_quota, decider


def cout(
    cents=1000, provenance="api", transfer_type="TOKEN_AUCTION",
    rarete="limited", saison="2023", player_slug="kylian-mbappe",
):
    return Cout(
        slug="s1", cents=cents, provenance=provenance, transfer_type=transfer_type,
        rarete=rarete, saison=saison, player_slug=player_slug,
    )


def marche(moins_cher=None, n=0):
    return PrixConcurrent(carte_slug="s1", moins_cher_cents=moins_cher, n_concurrents=n)


def decision(**kw):
    base = {
        "asset_id": "0xAAA", "slug": "s1", "nom": "Carte Test",
        "prix_actuel_cents": None, "cout": cout(), "marche": marche(moins_cher=2000),
    }
    return decider(**{**base, **kw})


# --- coût non fiable → BLOQUER, sans exception -------------------------------


@pytest.mark.parametrize("provenance", ["inconnu", "estime"])
def test_cout_non_fiable_bloque_toujours_meme_avec_un_concurrent_moins_cher(provenance):
    """Publier malgré un coût inconnu/estimé serait le pire des cinq bugs possibles."""
    d = decision(cout=cout(provenance=provenance), marche=marche(moins_cher=1))
    assert d.verdict is Verdict.BLOQUER
    assert d.prix_vise_cents is None  # jamais de prix visé sans coût fiable


def test_cout_inconnu_avec_cents_none_bloque():
    d = decision(cout=cout(cents=None, provenance="inconnu"))
    assert d.verdict is Verdict.BLOQUER


# --- le rail n'est plus un paramètre de décision (revu le 2026-09-22) -------
#
# `decider` ne connaît plus le rail Solana/historique : `offre.py` sait
# maintenant signer les autorisations Solana (`solana_signature.py`), donc une
# carte sur ce rail est décidée exactement comme les autres. Voir
# `test_offre.py` pour la signature, `test_quotidien.py` pour le seuil de
# blocage en masse (qui, lui, ne fait plus d'exception pour ce rail).


def test_essence_a_cout_nul_fiable_peut_se_repositionner():
    d = decision(cout=cout(cents=0, provenance="sans_cout"), marche=marche(moins_cher=100))
    assert d.verdict is Verdict.REPOSITIONNER
    assert d.prix_vise_cents == 99


# --- aucun concurrent comparable → LAISSER, mais un prix quand même ---------


def test_aucun_concurrent_laisse_mais_propose_le_plancher():
    """Décidé le 2026-09-21 : un prix à fixer dans tous les cas, même sans
    marché à comparer — le plancher reste une cible exploitable à la main."""
    d = decision(cout=cout(cents=1000), marche=marche(moins_cher=None))
    assert d.verdict is Verdict.LAISSER
    assert d.prix_vise_cents == 1050
    assert d.plancher_cents == 1050
    assert "aucun concurrent" in d.detail


# --- le plancher : jamais ignoré ---------------------------------------------


def test_viser_sous_le_plancher_alerte_au_lieu_de_publier():
    # coût 10,00 € × 1,05 = plancher 10,50 € ; concurrent à 10,40 € → viser 10,39 €.
    d = decision(cout=cout(cents=1000), marche=marche(moins_cher=1040))
    assert d.verdict is Verdict.ALERTER
    assert d.prix_vise_cents == 1039
    assert d.plancher_cents == 1050


def test_viser_juste_au_dessus_du_plancher_repositionne():
    # concurrent à 10,51 € - 1 ct = 10,50 €, exactement le plancher : autorisé.
    d = decision(cout=cout(cents=1000), marche=marche(moins_cher=1051))
    assert d.verdict is Verdict.REPOSITIONNER
    assert d.prix_vise_cents == 1050


def test_aucun_plancher_absolu_un_cout_quasi_nul_peut_viser_tres_bas():
    """Décidé le 2026-09-21 : pas de butée à 0,50 € — seul le coût réel compte.
    Coût 1 centime × 1,05 = 1 centime arrondi : viser 9 centimes reste au-dessus."""
    d = decision(cout=cout(cents=1, provenance="api"), marche=marche(moins_cher=10))
    assert d.verdict is Verdict.REPOSITIONNER
    assert d.plancher_cents == 1
    assert d.prix_vise_cents == 9


def test_aucun_plancher_absolu_un_cout_nul_ne_bloque_pas_a_zero():
    """Carte forgée (coût nul) : le plancher tombe à 0, rien ne force 0,50 €."""
    d = decision(cout=cout(cents=0, provenance="sans_cout"), marche=marche(moins_cher=5))
    assert d.verdict is Verdict.REPOSITIONNER
    assert d.plancher_cents == 0
    assert d.prix_vise_cents == 4


def test_une_baisse_ignorant_le_plancher_alerte_meme_tres_proche():
    """Mutation à surveiller : un `<=` au lieu de `<` laisserait passer le plancher pile."""
    d = decision(cout=cout(cents=1000), marche=marche(moins_cher=1050))  # vise = plancher - 1
    assert d.verdict is Verdict.ALERTER


# --- déjà au meilleur prix → LAISSER -----------------------------------------


def test_deja_moins_cher_que_la_cible_laisse_sans_republier():
    d = decision(prix_actuel_cents=500, cout=cout(cents=100), marche=marche(moins_cher=1000))
    assert d.verdict is Verdict.LAISSER
    assert d.deja_moins_cher is True


def test_prix_actuel_egal_a_la_cible_laisse():
    d = decision(prix_actuel_cents=999, cout=cout(cents=100), marche=marche(moins_cher=1000))
    assert d.verdict is Verdict.LAISSER
    assert d.deja_moins_cher is True


def test_laisser_sans_marche_ne_porte_pas_le_marqueur_deja_moins_cher():
    """Les deux `LAISSER` ne se ressemblent pas : celui sans concurrent n'a
    rien à comparer, il ne doit pas se voir surligné comme « déjà le moins
    cher » (cf. `alerte.py`)."""
    d = decision(cout=cout(cents=1000), marche=marche(moins_cher=None))
    assert d.verdict is Verdict.LAISSER
    assert d.deja_moins_cher is False


# --- la bride de baisse quotidienne ------------------------------------------


def test_baisse_trop_forte_freine_au_lieu_de_publier():
    # 10,00 € → 1,00 € = -90 %, bride à 10 % : doit freiner.
    d = decision(
        prix_actuel_cents=1000, cout=cout(cents=50), marche=marche(moins_cher=101),
        baisse_max_pct=10,
    )
    assert d.verdict is Verdict.FREINER
    assert "bride" in d.detail


def test_baisse_sous_la_bride_repositionne():
    d = decision(
        prix_actuel_cents=1000, cout=cout(cents=50), marche=marche(moins_cher=960),
        baisse_max_pct=10,
    )
    assert d.verdict is Verdict.REPOSITIONNER


def test_sans_bride_configuree_aucune_baisse_ne_freine():
    d = decision(
        prix_actuel_cents=1000, cout=cout(cents=1), marche=marche(moins_cher=101),
        baisse_max_pct=None,
    )
    assert d.verdict is Verdict.REPOSITIONNER


def test_carte_jamais_encore_listee_n_est_jamais_freinee_par_la_baisse():
    """`prix_actuel_cents=None` (close sans acheteur) : rien à comparer, pas de baisse."""
    d = decision(
        prix_actuel_cents=None, cout=cout(cents=50), marche=marche(moins_cher=101),
        baisse_max_pct=10,
    )
    assert d.verdict is Verdict.REPOSITIONNER


# --- le nominal ---------------------------------------------------------------


def test_cas_nominal_repositionne_un_centime_sous_le_concurrent():
    d = decision(cout=cout(cents=500), marche=marche(moins_cher=2000, n=3))
    assert d.verdict is Verdict.REPOSITIONNER
    assert d.prix_vise_cents == 1999
    assert d.plancher_cents == 525


# --- le détail du calcul est toujours présent --------------------------------


@pytest.mark.parametrize(
    "kw",
    [
        {"cout": cout(provenance="inconnu")},
        {"marche": marche(moins_cher=None)},
        {"cout": cout(cents=1000), "marche": marche(moins_cher=1040)},
        {"prix_actuel_cents": 500, "cout": cout(cents=100), "marche": marche(moins_cher=1000)},
    ],
)
def test_chaque_verdict_porte_un_detail_non_vide(kw):
    d = decision(**kw)
    assert d.detail


# --- `appliquer_quota` -------------------------------------------------------


def _repositionner(n=1):
    return [
        decision(asset_id=f"0x{i}", slug=f"s{i}", cout=cout(cents=100), marche=marche(2000))
        for i in range(n)
    ]


def test_quota_laisse_passer_jusqu_au_plafond():
    decisions = _repositionner(3)
    sorties = appliquer_quota(decisions, max_cartes=3)
    assert all(d.verdict is Verdict.REPOSITIONNER for d in sorties)


def test_au_dela_du_quota_les_suivantes_freinent():
    decisions = _repositionner(4)
    sorties = appliquer_quota(decisions, max_cartes=3)
    verdicts = [d.verdict for d in sorties]
    assert verdicts == [Verdict.REPOSITIONNER] * 3 + [Verdict.FREINER]
    assert "quota du jour" in sorties[-1].detail


def test_quota_ne_touche_pas_aux_autres_verdicts():
    bloquee = decision(cout=cout(provenance="inconnu"))
    alertee = decision(cout=cout(cents=1000), marche=marche(moins_cher=1010))
    sorties = appliquer_quota([bloquee, alertee], max_cartes=0)
    assert [d.verdict for d in sorties] == [Verdict.BLOQUER, Verdict.ALERTER]


def test_quota_zero_freine_toute_repositionnement():
    sorties = appliquer_quota(_repositionner(1), max_cartes=0)
    assert sorties[0].verdict is Verdict.FREINER
