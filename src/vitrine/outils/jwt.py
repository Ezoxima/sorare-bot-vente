"""Génère un JWT Sorare et l'écrit dans `.env`. À lancer par l'utilisateur.

Pourquoi un outil séparé, et pas une étape du programme : le flux `signIn` exige
le **mot de passe en clair** pour en calculer le hash bcrypt. Il est lu par
`getpass` — jamais affiché, jamais journalisé, jamais stocké — et il ne transite
par aucune autre partie du code.

Sans JWT, la clé API seule laisse `currentUser` à `null` : elle relève les
plafonds d'appel, elle n'identifie personne.

Le token vaut 30 jours. Passé ce délai il faut repasser par le mot de passe ;
avant, `createJwtToken(input: { aud })` suffit. Donc : renouveler avant
l'échéance, pas après.

⚠️ Un token demandé depuis une adresse IP inhabituelle déclenche la double
authentification d'office — c'est normal, le code est demandé ci-dessous.

Usage :
    python -m vitrine.outils.jwt --email <email> --aud vitrine
"""

from __future__ import annotations

import argparse
import getpass
import sys

import bcrypt
import httpx

from vitrine import config as conf
from vitrine.client import ClientSorare

URL_SEL = "https://api.sorare.com/api/v1/users/{email}"

MUTATION_CONNEXION = """
mutation Connexion($input: signInInput!, $aud: String!) {
  signIn(input: $input) {
    currentUser { slug }
    jwtToken(aud: $aud) { token expiredAt }
    otpSessionChallenge
    errors { message }
  }
}
"""


def _sel(email: str) -> str:
    reponse = httpx.get(URL_SEL.format(email=email), timeout=30.0)
    reponse.raise_for_status()
    sel = reponse.json().get("salt")
    if not sel:
        raise SystemExit(f"Sel introuvable pour {email} (réponse : {reponse.text[:200]}).")
    return sel


def _connexion(client: ClientSorare, email: str, hash_mdp: str, aud: str) -> dict:
    """Appelle `signIn`, et gère le rebond double authentification.

    Sur un compte protégé, `currentUser` revient `null` **sans erreur**, avec un
    `otpSessionChallenge` : ce n'est pas un échec, c'est une demande de code.
    """
    variables = {"input": {"email": email, "password": hash_mdp}, "aud": aud}
    resultat = client.executer(MUTATION_CONNEXION, variables)["signIn"]

    if resultat.get("otpSessionChallenge"):
        code = getpass.getpass("Code de double authentification : ").strip()
        variables["input"]["otpAttempt"] = code
        variables["input"]["otpSessionChallenge"] = resultat["otpSessionChallenge"]
        resultat = client.executer(MUTATION_CONNEXION, variables)["signIn"]

    erreurs = resultat.get("errors") or []
    if erreurs:
        details = "; ".join(e.get("message", "?") for e in erreurs)
        raise SystemExit(f"Échec de connexion : {details}")
    if not (resultat.get("jwtToken") or {}).get("token"):
        raise SystemExit(f"Aucun token dans la réponse : {resultat}")
    return resultat


def _ecrire_env(token: str, aud: str) -> None:
    """Remplace SORARE_JWT / SORARE_JWT_AUD dans `.env`, crée les lignes au besoin."""
    chemin = conf.CHEMIN_ENV
    lignes = chemin.read_text(encoding="utf-8").splitlines() if chemin.exists() else []
    a_ecrire = {"SORARE_JWT": token, "SORARE_JWT_AUD": aud}
    vues: set[str] = set()
    sortie: list[str] = []
    for ligne in lignes:
        cle = ligne.split("=", 1)[0].strip() if "=" in ligne else None
        if cle in a_ecrire:
            sortie.append(f"{cle}={a_ecrire[cle]}")
            vues.add(cle)
        else:
            sortie.append(ligne)
    for cle, valeur in a_ecrire.items():
        if cle not in vues:
            sortie.append(f"{cle}={valeur}")
    chemin.write_text("\n".join(sortie) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(description="Génère un JWT Sorare dans .env.")
    parseur.add_argument("--email", required=True)
    parseur.add_argument("--aud", default="vitrine", help="Audience du JWT (défaut : vitrine).")
    args = parseur.parse_args(argv)

    mot_de_passe = getpass.getpass("Mot de passe Sorare (non affiché) : ")
    if not mot_de_passe:
        raise SystemExit("Mot de passe vide — abandon.")
    hash_mdp = bcrypt.hashpw(mot_de_passe.encode(), _sel(args.email).encode()).decode()

    # `jwt=""` : sans ça, le client rattacherait le JWT courant — potentiellement
    # expiré — à cette requête, qui n'en a pas besoin. Un token périmé ferait
    # rejeter l'appel avant même d'atteindre le mot de passe, c'est-à-dire
    # justement l'appel qu'on fait pour le renouveler.
    config = conf.charger()
    vierge = type(config)(cle_api=config.cle_api, jwt="", jwt_aud="")
    with ClientSorare(vierge) as client:
        resultat = _connexion(client, args.email, hash_mdp, args.aud)

    token = resultat["jwtToken"]["token"]
    _ecrire_env(token, args.aud)
    print(f"OK — JWT écrit dans {conf.CHEMIN_ENV.name} (aud={args.aud}, "
          f"compte={(resultat.get('currentUser') or {}).get('slug')}).")
    print(f"     Expire : {resultat['jwtToken'].get('expiredAt')}.")
    print(f"     Token (tronqué) : {token[:12]}…{token[-6:]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
