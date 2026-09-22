"""La décision : un verdict par carte, sans jamais toucher réseau ni fichier.

Aucune entrée/sortie ici, dans le même esprit que `plan.py` : c'est ce qui
permet de rejouer la logique sur des cas figés et d'écrire des tests qui
mordent.

Cinq verdicts, jamais un sixième :

- `REPOSITIONNER` — un concurrent comparable existe, et viser en dessous de
  lui reste au-dessus du plancher.
- `LAISSER` — déjà au meilleur prix, ou aucun concurrent comparable. « Pas de
  concurrence » est un résultat à part entière, jamais un zéro qui pousserait
  à baisser quand même.
- `ALERTER` — être le moins cher exigerait de passer sous le plancher : mail,
  pas de publication.
- `BLOQUER` — coût inconnu, estimé, ou donnée incohérente. Une carte au coût
  estimé (`cout.PROVENANCE_ESTIME`) est bloquée ici, pas seulement « pas
  fiable » : mieux vaut prévenir que reposter sur une hypothèse.

  Le rail (`plan.RAIL_SOLANA` / `plan.RAIL_HISTORIQUE`) n'influence plus la
  décision (revu le 2026-09-22, doc officielle
  https://github.com/sorare/api#examples à l'appui) : `offre.py` sait
  maintenant signer les autorisations Solana, donc une carte Solana est décidée
  exactement comme une carte du rail historique. Avant cette date, ce module
  forçait `BLOQUER` sur ce rail faute de savoir signer.
- `FREINER` — le mouvement dépasse une bride : baisse trop forte en un jour,
  ou quota quotidien de publications déjà atteint.

Le verdict porte toujours le détail du calcul, pour que le rapport et le mail
(`alerte.py`) restent lisibles sans relire le code.
"""

from __future__ import annotations

import dataclasses
import enum

from vitrine.cout import Cout
from vitrine.marche import PrixConcurrent

UN_CENTIME = 1


class Verdict(enum.Enum):
    REPOSITIONNER = "repositionner"
    LAISSER = "laisser"
    ALERTER = "alerter"
    BLOQUER = "bloquer"
    FREINER = "freiner"


@dataclasses.dataclass(frozen=True)
class Decision:
    asset_id: str
    slug: str
    nom: str
    verdict: Verdict
    detail: str
    prix_actuel_cents: int | None
    prix_vise_cents: int | None = None
    plancher_cents: int | None = None
    # Vrai seulement pour un `LAISSER` où le prix actuel est déjà au moins
    # aussi bon que la cible calculée : distinct du `LAISSER` « pas de marché
    # à battre », qui ne doit pas se voir mis en avant de la même façon
    # (cf. `alerte.py`, surlignage vert).
    deja_moins_cher: bool = False


def decider(
    *,
    asset_id: str,
    slug: str,
    nom: str,
    prix_actuel_cents: int | None,
    cout: Cout,
    marche: PrixConcurrent,
    marge_pct: float = 5.0,
    baisse_max_pct: float | None = None,
) -> Decision:
    """Rend la décision pour une carte. Pas d'effet de bord, pas d'horloge."""

    def _decision(verdict: Verdict, detail: str, **kw: int | None) -> Decision:
        return Decision(asset_id, slug, nom, verdict, detail, prix_actuel_cents, **kw)

    if not cout.fiable_pour_publication:
        return _decision(
            Verdict.BLOQUER,
            f"coût d'achat « {cout.provenance} » — pas de publication automatique "
            "sans coût fiable (api, transaction à une carte, ou essence/gain)",
        )

    # Pas de plancher absolu : décidé le 2026-09-21, une carte à coût nul
    # (essence, gain) doit pouvoir se repositionner jusqu'à 1 centime sous le
    # marché sans butée artificielle. Le seul plancher est le coût lui-même.
    plancher = round(cout.cents * (1 + marge_pct / 100))

    # Un prix à fixer dans tous les cas (décidé le 2026-09-21) : même sans
    # concurrent comparable, le plancher reste une cible exploitable à la main.
    if marche.moins_cher_cents is None:
        decision = _decision(
            Verdict.LAISSER,
            "aucun concurrent comparable (même rareté, même statut classic/in-season) : "
            "pas de marché à battre, prix planché proposé",
            prix_vise_cents=plancher,
            plancher_cents=plancher,
        )
    else:
        vise = marche.moins_cher_cents - UN_CENTIME

        if vise < plancher:
            decision = _decision(
                Verdict.ALERTER,
                f"être le moins cher viserait {vise / 100:.2f} €, sous le plancher "
                f"{plancher / 100:.2f} € (coût {cout.cents / 100:.2f} € × "
                f"{1 + marge_pct / 100:.2f}, provenance {cout.provenance})",
                prix_vise_cents=vise,
                plancher_cents=plancher,
            )
        elif prix_actuel_cents is not None and vise >= prix_actuel_cents:
            decision = _decision(
                Verdict.LAISSER,
                f"déjà à {prix_actuel_cents / 100:.2f} €, au moins aussi bon que la cible "
                f"{vise / 100:.2f} €",
                prix_vise_cents=vise,
                plancher_cents=plancher,
                deja_moins_cher=True,
            )
        elif (
            baisse_max_pct is not None
            and prix_actuel_cents
            and (baisse_pct := (prix_actuel_cents - vise) / prix_actuel_cents * 100)
            > baisse_max_pct
        ):
            decision = _decision(
                Verdict.FREINER,
                f"baisse de {baisse_pct:.0f} % ({prix_actuel_cents / 100:.2f} € → "
                f"{vise / 100:.2f} €) > bride {baisse_max_pct:.0f} %",
                prix_vise_cents=vise,
                plancher_cents=plancher,
            )
        else:
            decision = _decision(
                Verdict.REPOSITIONNER,
                f"vise {vise / 100:.2f} € (concurrent le plus bas "
                f"{marche.moins_cher_cents / 100:.2f} € − 1 ct), plancher {plancher / 100:.2f} €",
                prix_vise_cents=vise,
                plancher_cents=plancher,
            )

    return decision


def appliquer_quota(decisions: list[Decision], max_cartes: int) -> list[Decision]:
    """Au-delà de `max_cartes` `REPOSITIONNER`, les suivantes basculent en `FREINER`.

    Rien ici ne priorise une carte sur une autre : l'ordre reçu fait foi, et
    c'est à l'appelant (`quotidien.py`) de trier ses décisions avant d'appeler
    cette fonction s'il veut privilégier certaines cartes.
    """
    sorties: list[Decision] = []
    n_retenues = 0
    for decision in decisions:
        if decision.verdict is not Verdict.REPOSITIONNER:
            sorties.append(decision)
            continue
        n_retenues += 1
        if n_retenues > max_cartes:
            sorties.append(
                dataclasses.replace(
                    decision,
                    verdict=Verdict.FREINER,
                    detail=f"quota du jour atteint ({max_cartes}) — {decision.detail}",
                )
            )
        else:
            sorties.append(decision)
    return sorties
