"""Tests de l'orchestration quotidienne — mode à blanc uniquement.

Aucun test ici n'exécute une publication réelle : le compteur de mutations
espionnées reste la seule assertion qui compte, comme dans `test_cli.py`.
Chaque garde-fou propre à `quotidien` (fichier STOP, quota, règle d'arrêt en
masse, mode à blanc par défaut) a son test dédié.
"""

from __future__ import annotations

import pytest

from vitrine import quotidien as quo
from vitrine.config import Config
from vitrine.cout import Cout
from vitrine.marche import PrixConcurrent
from vitrine.politique import Verdict

CONFIG = Config(cle_api="k", jwt="j", jwt_aud="a")


def carte_offre(asset="0xAAA", slug="dani-2023-limited-1", nom="Dani", prix=250, bc="bc-1",
                 solana=None):
    return {
        "blockchainId": bc,
        "receiverSide": {"amounts": {"referenceCurrency": "EUR", "eurCents": prix}},
        "senderSide": {
            "anyCards": [
                {"assetId": asset, "slug": slug, "name": nom, "rarityTyped": "limited",
                 "solanaAddress": solana}
            ]
        },
    }


def brut(en_cours=None, terminees=None, slug="ezox"):
    return {
        "nickname": "Ezox",
        "slug": slug,
        "en_cours": en_cours or [],
        "terminees_sans_acheteur": terminees or [],
    }


# --- `cartes_perimetre` / `appliquer_exclusions` -----------------------------


def test_perimetre_est_l_union_en_cours_et_closes_sans_acheteur():
    b = brut(en_cours=[carte_offre(asset="1")], terminees=[carte_offre(asset="2")])
    cartes = quo.cartes_perimetre(b)
    assert set(cartes) == {"1", "2"}


def test_carte_relistee_n_apparait_qu_une_fois_via_en_cours():
    b = brut(
        en_cours=[carte_offre(asset="1", prix=999)],
        terminees=[carte_offre(asset="1", prix=1)],
    )
    cartes = quo.cartes_perimetre(b)
    assert len(cartes) == 1
    assert cartes["1"].prix_actuel_cents == 999  # la version « en cours » prime


def test_carte_close_sans_annonce_en_cours_n_a_pas_de_blockchain_id():
    b = brut(terminees=[carte_offre(asset="1")])
    assert quo.cartes_perimetre(b)["1"].blockchain_id_actuel is None


def test_rail_solana_detecte_via_solana_address():
    b = brut(en_cours=[carte_offre(asset="1", solana="Gzbu2FUmYcmXGT9SXq")])
    assert quo.cartes_perimetre(b)["1"].rail == quo.RAIL_SOLANA


def test_rail_historique_sans_solana_address():
    b = brut(en_cours=[carte_offre(asset="1", solana=None)])
    assert quo.cartes_perimetre(b)["1"].rail == quo.RAIL_HISTORIQUE


def test_exclusion_par_slug_ou_asset_id():
    cartes = quo.cartes_perimetre(brut(en_cours=[carte_offre(asset="1", slug="s1")]))
    restant = quo.appliquer_exclusions(cartes, {"s1"}, set())
    assert restant == {}


def test_inclusion_explicite_ignore_le_reste():
    cartes = quo.cartes_perimetre(
        brut(en_cours=[carte_offre(asset="1", slug="s1"), carte_offre(asset="2", slug="s2")])
    )
    restant = quo.appliquer_exclusions(cartes, set(), {"s1"})
    assert set(restant) == {"1"}


def test_perimetre_csv_absent_ne_restreint_rien(tmp_path):
    exclusions, inclusions = quo.charger_perimetre(tmp_path / "absent.csv")
    assert exclusions == set()
    assert inclusions == set()


def test_perimetre_csv_lit_exclusions_et_inclusions(tmp_path):
    chemin = tmp_path / "perimetre.csv"
    chemin.write_text(
        "carte;exclure;inclure\ns1;oui;\ns2;;oui\n", encoding="utf-8-sig"
    )
    exclusions, inclusions = quo.charger_perimetre(chemin)
    assert exclusions == {"s1"}
    assert inclusions == {"s2"}


# --- `executer_passage` : les garde-fous -------------------------------------


class FauxClient:
    def executer(self, *a, **kw):
        raise AssertionError("aucun appel réseau ne devrait être nécessaire ici")


@pytest.fixture
def espion(monkeypatch):
    """Compte les mutations réellement envoyées et fige `preparer_passage`."""
    publiees: list[tuple[str, int]] = []
    monkeypatch.setattr(quo.off, "preparer", lambda c, a, p, **kw: [])
    # Aucun test d'orchestration ne doit toucher le vrai coffre Windows —
    # la signature elle-même est testée dans `test_solana_signature.py` /
    # `test_offre.py`, pas ici.
    monkeypatch.setattr(quo.cle_ethereum, "lire_cle_privee", lambda: None)
    monkeypatch.setattr(
        quo.off,
        "creer_annonce",
        lambda c, a, p, **kw: publiees.append((a, p))
        or quo.off.AnnonceCreee(id="x", blockchain_id="b", debut="", fin="", statut="opened"),
    )
    monkeypatch.setattr(quo.off, "annuler", lambda c, bc: "cancelled")
    return publiees


def _decisions_factices(verdicts, rails=None):
    rails = rails or [quo.RAIL_HISTORIQUE] * len(verdicts)
    return [
        (
            f"0x{i}",
            quo.CarteSuivie(
                asset_id=f"0x{i}", slug=f"s{i}", nom=f"Carte {i}", rarete="limited",
                rail=rail,
                prix_actuel_cents=500, blockchain_id_actuel=f"bc-{i}" if i % 2 else None,
            ),
            verdict,
        )
        for i, (verdict, rail) in enumerate(zip(verdicts, rails, strict=True))
    ]


def _figer_preparer_passage(monkeypatch, verdicts, rails=None):
    """Remplace `preparer_perimetre`/`decider_toutes` par des données fixes —
    teste `executer_passage` isolément, sans dépendre de `cout`/`marche`.
    `executer_passage` appelle ces deux fonctions séparément (pas
    `preparer_passage`) pour ne recalculer le marché qu'au dernier moment."""
    from vitrine.politique import Decision

    fixe = _decisions_factices(verdicts, rails)
    decisions = [
        Decision(
            asset_id=aid, slug=carte.slug, nom=carte.nom, verdict=v,
            detail="test", prix_actuel_cents=carte.prix_actuel_cents,
            prix_vise_cents=400 if v is Verdict.REPOSITIONNER else None,
        )
        for aid, carte, v in fixe
    ]
    cartes = {aid: carte for aid, carte, _ in fixe}
    monkeypatch.setattr(quo, "preparer_perimetre", lambda *a, **kw: (cartes, {}))
    monkeypatch.setattr(quo, "decider_toutes", lambda *a, **kw: decisions)
    return decisions, cartes


def test_fichier_stop_interrompt_avant_toute_lecture(espion, tmp_path, monkeypatch):
    (tmp_path / quo.FICHIER_STOP).write_text("")
    monkeypatch.chdir(tmp_path)
    code = quo.executer_passage(
        FauxClient(), CONFIG, inventaire_brut=brut(), perimetre_chemin=tmp_path / "perimetre.csv",
        executer=True, max_cartes=3, baisse_max_pct=10.0,
    )
    assert code == 1
    assert espion == []


def test_sans_decisions_ne_publie_rien(espion, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code = quo.executer_passage(
        FauxClient(), CONFIG, inventaire_brut=brut(), perimetre_chemin=tmp_path / "perimetre.csv",
        executer=True, max_cartes=3, baisse_max_pct=10.0,
    )
    assert code == 0
    assert espion == []


def test_mode_a_blanc_par_defaut_ne_publie_rien_meme_avec_du_repositionnement(
    espion, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    _figer_preparer_passage(monkeypatch, [Verdict.REPOSITIONNER])
    code = quo.executer_passage(
        FauxClient(), CONFIG, inventaire_brut=brut(), perimetre_chemin=tmp_path / "perimetre.csv",
        executer=False, max_cartes=3, baisse_max_pct=10.0,
    )
    assert code == 0
    assert espion == []


def test_regle_d_arret_en_masse_interrompt_sans_rien_publier(espion, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 2 BLOQUER sur 3 cartes > 1/3 : interruption.
    _figer_preparer_passage(monkeypatch, [Verdict.BLOQUER, Verdict.BLOQUER, Verdict.REPOSITIONNER])
    code = quo.executer_passage(
        FauxClient(), CONFIG, inventaire_brut=brut(), perimetre_chemin=tmp_path / "perimetre.csv",
        executer=True, max_cartes=3, baisse_max_pct=10.0,
        confirmer=lambda _: "1",
    )
    assert code == 1
    assert espion == []


def test_le_rail_ne_beneficie_plus_d_aucune_exception_dans_la_regle_de_masse(
    espion, tmp_path, monkeypatch
):
    """Depuis le 2026-09-22 (signature Solana implémentée), un `BLOQUER` sur ce
    rail compte normalement dans le seuil : ce n'est plus un artefact
    structurel à exclure (contrairement à l'ancien comportement du
    2026-09-20-21). 3 BLOQUER sur 4 cartes > 1/3 : interruption, quel que soit
    le rail."""
    monkeypatch.chdir(tmp_path)
    _figer_preparer_passage(
        monkeypatch,
        [Verdict.BLOQUER, Verdict.BLOQUER, Verdict.BLOQUER, Verdict.REPOSITIONNER],
        rails=[quo.RAIL_SOLANA, quo.RAIL_SOLANA, quo.RAIL_SOLANA, quo.RAIL_HISTORIQUE],
    )
    code = quo.executer_passage(
        FauxClient(), CONFIG, inventaire_brut=brut(), perimetre_chemin=tmp_path / "perimetre.csv",
        executer=True, max_cartes=3, baisse_max_pct=10.0,
        confirmer=lambda _: "1",
    )
    assert code == 1
    assert espion == []


def test_sous_le_seuil_de_masse_la_publication_continue(espion, tmp_path, monkeypatch):
    # 1 BLOQUER sur 3 cartes = 1/3, pas strictement au-dessus du seuil.
    _figer_preparer_passage(monkeypatch, [Verdict.BLOQUER, Verdict.REPOSITIONNER, Verdict.LAISSER])
    monkeypatch.chdir(tmp_path)
    code = quo.executer_passage(
        FauxClient(), CONFIG, inventaire_brut=brut(), perimetre_chemin=tmp_path / "perimetre.csv",
        executer=True, max_cartes=3, baisse_max_pct=10.0,
        confirmer=lambda n: "1",
    )
    assert code == 0
    assert espion == [("0x1", 400)]


def test_confirmation_incorrecte_n_envoie_rien(espion, tmp_path, monkeypatch):
    _figer_preparer_passage(monkeypatch, [Verdict.REPOSITIONNER])
    monkeypatch.chdir(tmp_path)
    code = quo.executer_passage(
        FauxClient(), CONFIG, inventaire_brut=brut(), perimetre_chemin=tmp_path / "perimetre.csv",
        executer=True, max_cartes=3, baisse_max_pct=10.0,
        confirmer=lambda _: "non",
    )
    assert code == 1
    assert espion == []


def test_confirmation_exacte_publie_et_annule_l_annonce_en_cours_d_abord(
    espion, tmp_path, monkeypatch
):
    annulations = []
    monkeypatch.setattr(quo.off, "annuler", lambda c, bc: annulations.append(bc) or "cancelled")
    # index 1 a un blockchain_id_actuel (i % 2 == 1 → non-None) selon `_decisions_factices`.
    _figer_preparer_passage(monkeypatch, [Verdict.LAISSER, Verdict.REPOSITIONNER])
    monkeypatch.chdir(tmp_path)
    code = quo.executer_passage(
        FauxClient(), CONFIG, inventaire_brut=brut(), perimetre_chemin=tmp_path / "perimetre.csv",
        executer=True, max_cartes=3, baisse_max_pct=10.0,
        confirmer=lambda _: "1",
    )
    assert code == 0
    assert espion == [("0x1", 400)]
    assert annulations == ["bc-1"]


def test_signature_requise_n_annule_pas_l_annonce_en_cours(espion, tmp_path, monkeypatch):
    """Régression du 2026-09-20 : une carte réelle a fini délistée parce que
    `annuler` tournait avant `preparer` — quand `preparer` levait `SignatureRequise`
    juste après, l'ancienne annonce était déjà perdue et la nouvelle jamais créée.
    `preparer` ne fait aucune écriture : il doit toujours passer en premier."""
    annulations = []
    monkeypatch.setattr(quo.off, "annuler", lambda c, bc: annulations.append(bc) or "cancelled")

    def preparer_qui_refuse(c, a, p, **kw):
        raise quo.off.SignatureRequise("signature demandée")

    monkeypatch.setattr(quo.off, "preparer", preparer_qui_refuse)
    # index 1 a un blockchain_id_actuel selon `_decisions_factices`.
    _figer_preparer_passage(monkeypatch, [Verdict.LAISSER, Verdict.REPOSITIONNER])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(quo.off.SignatureRequise):
        quo.executer_passage(
            FauxClient(), CONFIG, inventaire_brut=brut(),
            perimetre_chemin=tmp_path / "perimetre.csv",
            executer=True, max_cartes=3, baisse_max_pct=10.0,
            confirmer=lambda _: "1",
        )
    assert annulations == []
    assert espion == []


def test_quota_limite_les_publications_meme_avec_confirmation(espion, tmp_path, monkeypatch):
    _figer_preparer_passage(
        monkeypatch, [Verdict.REPOSITIONNER, Verdict.REPOSITIONNER, Verdict.REPOSITIONNER]
    )
    monkeypatch.chdir(tmp_path)
    code = quo.executer_passage(
        FauxClient(), CONFIG, inventaire_brut=brut(), perimetre_chemin=tmp_path / "perimetre.csv",
        executer=True, max_cartes=1, baisse_max_pct=10.0,
        confirmer=lambda _: "1",
    )
    assert code == 0
    assert len(espion) == 1


def test_gmail_non_configure_previent_sans_lever(espion, tmp_path, monkeypatch, capsys):
    _figer_preparer_passage(monkeypatch, [Verdict.ALERTER])
    monkeypatch.chdir(tmp_path)
    code = quo.executer_passage(
        FauxClient(), CONFIG, inventaire_brut=brut(), perimetre_chemin=tmp_path / "perimetre.csv",
        executer=False, max_cartes=3, baisse_max_pct=10.0,
    )
    assert code == 0
    assert "Gmail non configuré" in capsys.readouterr().out


def test_gmail_configure_envoie_un_mail_sur_alerte(espion, tmp_path, monkeypatch):
    from vitrine import alerte

    envois = []
    monkeypatch.setattr(
        alerte, "envoyer_rapport", lambda **kw: envois.append(kw["destinataire"])
    )
    _figer_preparer_passage(monkeypatch, [Verdict.ALERTER])
    monkeypatch.chdir(tmp_path)
    config = Config(
        cle_api="k", jwt="j", jwt_aud="a", gmail_expediteur="a@gmail.com",
        gmail_mot_de_passe_application="x", gmail_destinataire="a@gmail.com",
    )
    quo.executer_passage(
        FauxClient(), config, inventaire_brut=brut(), perimetre_chemin=tmp_path / "perimetre.csv",
        executer=False, max_cartes=3, baisse_max_pct=10.0,
    )
    assert envois == ["a@gmail.com"]


def test_gmail_envoie_aussi_sur_repositionner_sans_alerte(espion, tmp_path, monkeypatch):
    """Un passage à blanc régulier (sans --executer) doit pouvoir servir de
    « voici les prix à fixer à la main » — le mail ne doit pas dépendre d'une
    ALERTER pour partir : un REPOSITIONNER seul suffit."""
    from vitrine import alerte

    envois = []
    monkeypatch.setattr(
        alerte, "envoyer_rapport", lambda **kw: envois.append(kw["destinataire"])
    )
    _figer_preparer_passage(monkeypatch, [Verdict.REPOSITIONNER, Verdict.LAISSER])
    monkeypatch.chdir(tmp_path)
    config = Config(
        cle_api="k", jwt="j", jwt_aud="a", gmail_expediteur="a@gmail.com",
        gmail_mot_de_passe_application="x", gmail_destinataire="a@gmail.com",
    )
    quo.executer_passage(
        FauxClient(), config, inventaire_brut=brut(), perimetre_chemin=tmp_path / "perimetre.csv",
        executer=False, max_cartes=3, baisse_max_pct=10.0,
    )
    assert envois == ["a@gmail.com"]


def test_gmail_n_envoie_rien_si_rien_a_faire_ni_a_signaler(espion, tmp_path, monkeypatch):
    from vitrine import alerte

    envois = []
    monkeypatch.setattr(
        alerte, "envoyer_rapport", lambda **kw: envois.append(kw["destinataire"])
    )
    _figer_preparer_passage(monkeypatch, [Verdict.LAISSER, Verdict.FREINER])
    monkeypatch.chdir(tmp_path)
    config = Config(
        cle_api="k", jwt="j", jwt_aud="a", gmail_expediteur="a@gmail.com",
        gmail_mot_de_passe_application="x", gmail_destinataire="a@gmail.com",
    )
    quo.executer_passage(
        FauxClient(), config, inventaire_brut=brut(), perimetre_chemin=tmp_path / "perimetre.csv",
        executer=False, max_cartes=3, baisse_max_pct=10.0,
    )
    assert envois == []


# --- `preparer_passage` : intégration coût + marché + politique -------------


def test_preparer_passage_relie_cout_marche_et_politique(monkeypatch):
    """Sans mocker `decider` : la carte est vraiment moins chère que le plancher."""

    def faux_resoudre_couts(client, user_slug, slugs):
        return {
            s: Cout(
                slug=s, cents=1000, provenance="api", transfer_type="TOKEN_AUCTION",
                rarete="limited", saison="2023", player_slug="kylian-mbappe",
            )
            for s in slugs
        }

    def faux_plus_bas_prix(client, cartes, *, mon_slug):
        return {
            c.slug: PrixConcurrent(c.slug, moins_cher_cents=2000, n_concurrents=1) for c in cartes
        }

    monkeypatch.setattr(quo.co, "resoudre_couts", faux_resoudre_couts)
    monkeypatch.setattr(quo.mar, "plus_bas_prix_concurrent", faux_plus_bas_prix)

    # Close sans acheteur : pas de prix actuel, donc rien n'empêche de repositionner.
    b = brut(terminees=[carte_offre(asset="1", slug="s1")])
    decisions, cartes = quo.preparer_passage(FauxClient(), "ezox", b)
    assert len(decisions) == 1
    assert decisions[0].verdict is Verdict.REPOSITIONNER
    assert decisions[0].prix_vise_cents == 1999


def test_preparer_passage_traite_une_carte_solana_comme_les_autres(monkeypatch):
    """Depuis le 2026-09-22 (signature Solana implémentée, doc officielle
    https://github.com/sorare/api#examples), une carte Solana au marché
    favorable atteint REPOSITIONNER exactement comme une carte du rail
    historique — `politique.py` ne connaît plus le rail."""

    def faux_resoudre_couts(client, user_slug, slugs):
        return {
            s: Cout(
                slug=s, cents=1000, provenance="api", transfer_type="TOKEN_AUCTION",
                rarete="limited", saison="2023", player_slug="kylian-mbappe",
            )
            for s in slugs
        }

    def faux_plus_bas_prix(client, cartes, *, mon_slug):
        return {
            c.slug: PrixConcurrent(c.slug, moins_cher_cents=2000, n_concurrents=1) for c in cartes
        }

    monkeypatch.setattr(quo.co, "resoudre_couts", faux_resoudre_couts)
    monkeypatch.setattr(quo.mar, "plus_bas_prix_concurrent", faux_plus_bas_prix)

    b = brut(terminees=[carte_offre(asset="1", slug="s1", solana="Gzbu2FUmYcmXGT9SXq")])
    decisions, _ = quo.preparer_passage(FauxClient(), "ezox", b)
    assert decisions[0].verdict is Verdict.REPOSITIONNER
    assert decisions[0].prix_vise_cents == 1999


def test_executer_passage_publie_desormais_une_carte_solana_favorable(
    espion, tmp_path, monkeypatch
):
    """Bout en bout : avec `--executer` et confirmation, une carte Solana au
    marché favorable est bien tentée comme les autres — `off.preparer` (mocké
    par `espion`) est appelé et la publication a lieu. La signature réelle,
    elle, est vérifiée dans `test_offre.py` et `test_solana_signature.py`."""

    def faux_resoudre_couts(client, user_slug, slugs):
        return {
            s: Cout(
                slug=s, cents=1000, provenance="api", transfer_type="TOKEN_AUCTION",
                rarete="limited", saison="2023", player_slug="kylian-mbappe",
            )
            for s in slugs
        }

    def faux_plus_bas_prix(client, cartes, *, mon_slug):
        return {
            c.slug: PrixConcurrent(c.slug, moins_cher_cents=2000, n_concurrents=1) for c in cartes
        }

    monkeypatch.setattr(quo.co, "resoudre_couts", faux_resoudre_couts)
    monkeypatch.setattr(quo.mar, "plus_bas_prix_concurrent", faux_plus_bas_prix)
    monkeypatch.chdir(tmp_path)

    b = brut(terminees=[carte_offre(asset="1", slug="s1", solana="Gzbu2FUmYcmXGT9SXq")])
    code = quo.executer_passage(
        FauxClient(), CONFIG, inventaire_brut=b, perimetre_chemin=tmp_path / "perimetre.csv",
        executer=True, max_cartes=3, baisse_max_pct=10.0,
        confirmer=lambda _: "1",
    )
    assert code == 0
    assert espion == [("1", 1999)]


def test_preparer_passage_ignore_les_cartes_sans_playerslug_pour_le_marche(monkeypatch):
    def faux_resoudre_couts(client, user_slug, slugs):
        return {
            s: Cout(slug=s, cents=1000, provenance="api", player_slug=None)
            for s in slugs
        }

    appele = {"n": 0}

    def faux_plus_bas_prix(client, cartes, *, mon_slug):
        appele["n"] += 1
        return {}

    monkeypatch.setattr(quo.co, "resoudre_couts", faux_resoudre_couts)
    monkeypatch.setattr(quo.mar, "plus_bas_prix_concurrent", faux_plus_bas_prix)

    b = brut(en_cours=[carte_offre(asset="1", slug="s1")])
    decisions, _ = quo.preparer_passage(FauxClient(), "ezox", b)
    assert appele["n"] == 0  # aucun appel marché : rien à comparer
    assert decisions[0].verdict is Verdict.LAISSER
