"""Lecture des secrets depuis `.env`, et rien d'autre.

Pourquoi un module dédié plutôt qu'un `os.environ` dispersé : les trois secrets
(clé API, JWT, audience du JWT) n'ont pas le même rôle et n'échouent pas de la
même façon. La clé API relève les plafonds mais **n'identifie personne** — les
annonces de l'utilisateur sont invisibles sans JWT. Une absence de JWT doit donc
échouer bruyamment au démarrage, pas produire un inventaire vide qu'on lirait
comme « rien à relister ».
"""

from __future__ import annotations

import dataclasses
import pathlib

RACINE = pathlib.Path(__file__).resolve().parents[2]
CHEMIN_ENV = RACINE / ".env"


@dataclasses.dataclass(frozen=True)
class Config:
    cle_api: str
    jwt: str
    jwt_aud: str
    # Alertes (`vitrine quotidien`) — absents = pas de mail, un avertissement
    # s'affiche à la place. Le mot de passe est un mot de passe d'application
    # Google, pas le mot de passe du compte (cf. README).
    gmail_expediteur: str = ""
    gmail_mot_de_passe_application: str = ""
    gmail_destinataire: str = ""

    def exiger_identite(self) -> None:
        """Échoue si l'on ne peut pas savoir QUI l'on est.

        Sans JWT, `currentUser` revient `null` **sans erreur GraphQL** : la
        requête a l'air d'avoir réussi. C'est le silence le plus coûteux de
        cette API, donc on le transforme en arrêt net.
        """
        if not self.jwt or not self.jwt_aud:
            raise SystemExit(
                "SORARE_JWT et SORARE_JWT_AUD sont requis : sans eux l'API ne renvoie "
                "aucune de tes annonces (et ne le signale pas). "
                "Génère un token avec `python -m vitrine.outils.jwt --email <email>`."
            )


def _lire_env(chemin: pathlib.Path) -> dict[str, str]:
    if not chemin.exists():
        return {}
    valeurs: dict[str, str] = {}
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        valeurs[cle.strip()] = valeur.strip()
    return valeurs


def charger(chemin: pathlib.Path | None = None) -> Config:
    """Charge la config. `chemin` n'existe que pour les tests."""
    env = _lire_env(chemin or CHEMIN_ENV)
    return Config(
        cle_api=env.get("SORARE_API_KEY", ""),
        jwt=env.get("SORARE_JWT", ""),
        jwt_aud=env.get("SORARE_JWT_AUD", ""),
        gmail_expediteur=env.get("GMAIL_EXPEDITEUR", ""),
        gmail_mot_de_passe_application=env.get("GMAIL_MOT_DE_PASSE_APPLICATION", ""),
        gmail_destinataire=env.get("GMAIL_DESTINATAIRE", ""),
    )
