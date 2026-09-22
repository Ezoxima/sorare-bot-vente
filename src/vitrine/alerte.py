"""Un mail récapitulatif par passage, via Gmail SMTP (bibliothèque standard).

Un seul mail, pas un par carte : le rapport se lit d'un coup, avec les
effectifs de chaque verdict — comme le reste de l'outil affiche toujours un
`n` à côté d'un motif (cf. `plan.resume`).

Le mail part en HTML (tableau) avec un repli texte brut : Gmail affiche le
tableau et en préserve les colonnes au copier-coller dans Excel/Sheets — ce
qu'un texte à puces ne permet pas. Les lignes `REPOSITIONNER` (prix à fixer à
la main si tu ne veux pas de `--executer`) passent en premier et en gras :
c'est l'information qu'on vient chercher en priorité dans ce mail. Une ligne
`LAISSER` où le prix actuel est déjà au moins aussi bon que la cible
(`Decision.deja_moins_cher`, décidé le 2026-09-21) est surlignée en vert :
rien à faire, et ça se voit d'un coup d'œil sans lire le détail.

`smtp_cls` est injectable, dans le même esprit que `transport`/`sleep` sur
`ClientSorare` : les tests envoient un mail « pour de vrai » sans jamais
toucher le réseau.
"""

from __future__ import annotations

import html
import smtplib
from email.message import EmailMessage

from vitrine.politique import Decision, Verdict

SERVEUR_SMTP = "smtp.gmail.com"
PORT_SMTP = 587

# REPOSITIONNER et ALERTER d'abord (les deux qui contiennent un prix visé ou
# un besoin de décision) ; l'ordre des autres n'a pas d'importance pratique.
ORDRE_AFFICHAGE = {
    Verdict.REPOSITIONNER: 0,
    Verdict.ALERTER: 1,
    Verdict.LAISSER: 2,
    Verdict.FREINER: 3,
    Verdict.BLOQUER: 4,
}


def composer_rapport(decisions: list[Decision]) -> str:
    """Repli texte brut (clients sans HTML) — et ce qu'affiche `quotidien` en
    mode à blanc dans le terminal."""
    par_verdict: dict[Verdict, list[Decision]] = {}
    for d in decisions:
        par_verdict.setdefault(d.verdict, []).append(d)

    lignes = [f"Vitrine — passage quotidien, {len(decisions)} carte(s) examinée(s)."]
    for verdict in Verdict:
        groupe = par_verdict.get(verdict, [])
        lignes.append(f"\n{verdict.value.upper()} — n={len(groupe)}")
        for d in groupe:
            prix = f"{d.prix_actuel_cents / 100:.2f} €" if d.prix_actuel_cents is not None else "?"
            lignes.append(f"  - {d.nom} ({prix}) : {d.detail}")
    return "\n".join(lignes)


def _fmt_prix(cents: int | None) -> str:
    return f"{cents / 100:.2f}" if cents is not None else "—"


def _cellule(contenu: str, *, gras: bool = False, droite: bool = False, vert: bool = False) -> str:
    style = "padding:6px 10px;border:1px solid #ccc;"
    if droite:
        style += "text-align:right;"
    if gras:
        style += "font-weight:bold;"
    if vert:
        style += "background:#d9f2d9;"
    return f'<td style="{style}">{contenu}</td>'


def composer_rapport_html(decisions: list[Decision]) -> str:
    """Le tableau HTML envoyé par mail. Une ligne par carte, triée pour que le
    prix à fixer (`REPOSITIONNER`) saute aux yeux en premier."""
    triees = sorted(decisions, key=lambda d: ORDRE_AFFICHAGE[d.verdict])

    entete = "".join(
        f'<th style="padding:6px 10px;border:1px solid #ccc;background:#eee;'
        f'text-align:{"right" if col in ("Prix actuel (€)", "Prix à fixer (€)") else "left"};">'
        f"{col}</th>"
        for col in ("Carte", "Verdict", "Prix actuel (€)", "Prix à fixer (€)", "Détail")
    )

    lignes = []
    for d in triees:
        actionnable = d.verdict is Verdict.REPOSITIONNER
        vert = d.deja_moins_cher
        lignes.append(
            "<tr>"
            + _cellule(html.escape(d.nom), gras=actionnable, vert=vert)
            + _cellule(d.verdict.value, vert=vert)
            + _cellule(_fmt_prix(d.prix_actuel_cents), droite=True, vert=vert)
            + _cellule(_fmt_prix(d.prix_vise_cents), droite=True, gras=actionnable, vert=vert)
            + _cellule(html.escape(d.detail), vert=vert)
            + "</tr>"
        )

    return (
        f"<p>Vitrine — passage quotidien, {len(decisions)} carte(s) examinée(s).</p>"
        '<table style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;">'
        f"<tr>{entete}</tr>{''.join(lignes)}"
        "</table>"
    )


def envoyer(
    *,
    expediteur: str,
    mot_de_passe_application: str,
    destinataire: str,
    sujet: str,
    corps: str,
    corps_html: str | None = None,
    smtp_cls: type = smtplib.SMTP,
) -> None:
    """Envoie le mail. Lève si Gmail refuse — un échec d'alerte doit se voir,
    pas disparaître dans un `except` silencieux."""
    message = EmailMessage()
    message["From"] = expediteur
    message["To"] = destinataire
    message["Subject"] = sujet
    message.set_content(corps)
    if corps_html is not None:
        message.add_alternative(corps_html, subtype="html")

    with smtp_cls(SERVEUR_SMTP, PORT_SMTP) as serveur:
        serveur.starttls()
        serveur.login(expediteur, mot_de_passe_application)
        serveur.send_message(message)


def envoyer_rapport(
    *,
    expediteur: str,
    mot_de_passe_application: str,
    destinataire: str,
    decisions: list[Decision],
    smtp_cls: type = smtplib.SMTP,
) -> None:
    n_alertes = sum(1 for d in decisions if d.verdict is Verdict.ALERTER)
    n_bloquees = sum(1 for d in decisions if d.verdict is Verdict.BLOQUER)
    sujet = f"Vitrine — {n_alertes} alerte(s), {n_bloquees} bloquée(s)"
    envoyer(
        expediteur=expediteur,
        mot_de_passe_application=mot_de_passe_application,
        destinataire=destinataire,
        sujet=sujet,
        corps=composer_rapport(decisions),
        corps_html=composer_rapport_html(decisions),
        smtp_cls=smtp_cls,
    )
