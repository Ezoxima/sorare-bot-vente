"""Tests du mail récapitulatif — sans jamais toucher un vrai serveur SMTP.

`smtp_cls` est une fausse classe SMTP injectée, dans le même esprit que le
transport simulé de `ClientSorare` : on vérifie la séquence `starttls` →
`login` → `send_message`, pas qu'un vrai mail part.
"""

from __future__ import annotations

from vitrine import alerte as al
from vitrine.cout import Cout
from vitrine.marche import PrixConcurrent
from vitrine.politique import Verdict, decider


class FauxSMTP:
    """Enregistre les appels ; `instances` garde une trace de chaque connexion."""

    instances: list[FauxSMTP] = []

    def __init__(self, hote, port):
        self.hote = hote
        self.port = port
        self.appels: list[str] = []
        FauxSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.appels.append("starttls")

    def login(self, utilisateur, mot_de_passe):
        self.appels.append(f"login:{utilisateur}:{mot_de_passe}")

    def send_message(self, message):
        self.appels.append(f"send:{message['To']}:{message['Subject']}")
        self.dernier_message = message


def setup_function(_):
    FauxSMTP.instances.clear()


def decision(
    verdict=Verdict.LAISSER, nom="Carte Test", prix=250, detail="rien à faire",
    deja_moins_cher=False,
):
    cout = Cout(slug="s", cents=100, provenance="api")
    marche = PrixConcurrent(carte_slug="s", moins_cher_cents=None, n_concurrents=0)
    d = decider(
        asset_id="0xAAA", slug="s", nom=nom, prix_actuel_cents=prix, cout=cout, marche=marche
    )
    # `decider` seul ne produit pas tous les verdicts : on les force pour le test du mail.
    import dataclasses

    return dataclasses.replace(
        d, verdict=verdict, detail=detail, deja_moins_cher=deja_moins_cher
    )


# --- `composer_rapport` ------------------------------------------------------


def test_rapport_donne_les_effectifs_de_chaque_verdict():
    decisions = [
        decision(Verdict.REPOSITIONNER),
        decision(Verdict.REPOSITIONNER),
        decision(Verdict.ALERTER),
        decision(Verdict.BLOQUER),
    ]
    rapport = al.composer_rapport(decisions)
    assert "REPOSITIONNER — n=2" in rapport
    assert "ALERTER — n=1" in rapport
    assert "BLOQUER — n=1" in rapport
    assert "LAISSER — n=0" in rapport
    assert "FREINER — n=0" in rapport


def test_rapport_liste_le_detail_de_chaque_carte():
    rapport = al.composer_rapport([decision(Verdict.ALERTER, nom="Kylian", detail="motif X")])
    assert "Kylian" in rapport
    assert "motif X" in rapport


def test_rapport_gere_un_prix_actuel_absent():
    rapport = al.composer_rapport([decision(Verdict.REPOSITIONNER, prix=None)])
    assert "(?)" in rapport


def test_rapport_vide_ne_leve_pas():
    rapport = al.composer_rapport([])
    assert "0 carte(s)" in rapport


# --- `envoyer` / `envoyer_rapport` -------------------------------------------


def test_envoyer_suit_la_sequence_starttls_login_send():
    al.envoyer(
        expediteur="moi@gmail.com", mot_de_passe_application="motdepasse",
        destinataire="moi@gmail.com", sujet="Sujet", corps="Corps", smtp_cls=FauxSMTP,
    )
    (instance,) = FauxSMTP.instances
    assert instance.appels == [
        "starttls", "login:moi@gmail.com:motdepasse", "send:moi@gmail.com:Sujet",
    ]


def test_envoyer_utilise_bien_smtp_gmail_com_port_587():
    al.envoyer(
        expediteur="a@gmail.com", mot_de_passe_application="x",
        destinataire="a@gmail.com", sujet="s", corps="c", smtp_cls=FauxSMTP,
    )
    instance = FauxSMTP.instances[0]
    assert instance.hote == "smtp.gmail.com"
    assert instance.port == 587


def test_envoyer_rapport_resume_les_effectifs_dans_le_sujet():
    decisions = [decision(Verdict.ALERTER), decision(Verdict.ALERTER), decision(Verdict.BLOQUER)]
    al.envoyer_rapport(
        expediteur="a@gmail.com", mot_de_passe_application="x", destinataire="a@gmail.com",
        decisions=decisions, smtp_cls=FauxSMTP,
    )
    instance = FauxSMTP.instances[0]
    assert "2 alerte(s), 1 bloquée(s)" in instance.dernier_message["Subject"]


def test_envoyer_rapport_met_le_rapport_complet_dans_le_corps_texte():
    decisions = [decision(Verdict.ALERTER, nom="Kylian", detail="motif unique")]
    al.envoyer_rapport(
        expediteur="a@gmail.com", mot_de_passe_application="x", destinataire="a@gmail.com",
        decisions=decisions, smtp_cls=FauxSMTP,
    )
    message = FauxSMTP.instances[0].dernier_message
    corps = message.get_body(preferencelist=("plain",)).get_content()
    assert "Kylian" in corps
    assert "motif unique" in corps


def test_envoyer_rapport_met_aussi_un_tableau_html():
    """Le repli texte ne suffit pas : Gmail doit recevoir une alternative HTML,
    seule forme qui se colle proprement en colonnes dans un tableur."""
    decisions = [decision(Verdict.REPOSITIONNER, nom="Kylian", detail="motif unique")]
    al.envoyer_rapport(
        expediteur="a@gmail.com", mot_de_passe_application="x", destinataire="a@gmail.com",
        decisions=decisions, smtp_cls=FauxSMTP,
    )
    message = FauxSMTP.instances[0].dernier_message
    assert message.get_content_type() == "multipart/alternative"
    html_body = message.get_body(preferencelist=("html",)).get_content()
    assert "<table" in html_body
    assert "Kylian" in html_body
    assert "motif unique" in html_body


# --- `composer_rapport_html` -------------------------------------------------


def test_rapport_html_contient_un_tableau_avec_en_tete():
    html_body = al.composer_rapport_html([decision(Verdict.REPOSITIONNER, nom="Kylian")])
    assert "<table" in html_body
    assert "<th" in html_body
    assert "Prix à fixer" in html_body


def test_rapport_html_repositionner_passe_en_premier_et_porte_le_prix_vise():
    import dataclasses

    bloquee = decision(Verdict.BLOQUER, nom="Bloquee")
    repositionner = dataclasses.replace(
        decision(Verdict.REPOSITIONNER, nom="AVendre"), prix_vise_cents=199
    )
    html_body = al.composer_rapport_html([bloquee, repositionner])
    # La carte REPOSITIONNER apparaît avant la BLOQUER, malgré l'ordre d'entrée inverse.
    assert html_body.index("AVendre") < html_body.index("Bloquee")
    assert "1.99" in html_body


def test_rapport_html_echappe_les_caracteres_speciaux_du_nom():
    html_body = al.composer_rapport_html([decision(Verdict.LAISSER, nom="A & B <test>")])
    assert "&amp;" in html_body
    assert "<test>" not in html_body


def test_rapport_html_prix_absent_rend_un_tiret_pas_un_point_d_interrogation():
    html_body = al.composer_rapport_html([decision(Verdict.REPOSITIONNER, prix=None)])
    assert "—" in html_body


def test_rapport_html_surligne_en_vert_une_carte_deja_moins_cher():
    """Demandé le 2026-09-21 : repérer d'un coup d'œil les cartes où il n'y a
    rien à faire parce qu'on est déjà le moins cher."""
    verte = decision(Verdict.LAISSER, nom="DejaMoinsCher", deja_moins_cher=True)
    neutre = decision(Verdict.LAISSER, nom="RienAComparer", deja_moins_cher=False)
    html_body = al.composer_rapport_html([verte, neutre])
    lignes = html_body.split("<tr>")
    (ligne_verte,) = [ligne for ligne in lignes if "DejaMoinsCher" in ligne]
    (ligne_neutre,) = [ligne for ligne in lignes if "RienAComparer" in ligne]
    assert "#d9f2d9" in ligne_verte
    assert "#d9f2d9" not in ligne_neutre
