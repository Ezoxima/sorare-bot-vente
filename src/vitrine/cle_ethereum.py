"""Stockage de la clé privée Ethereum du wallet Sorare — gestionnaire
d'identifiants Windows via `keyring`, jamais un fichier, jamais journalisée.

Même mécanisme que le projet `acheteur` (`paiement/cle_ethereum.py`), coffre
séparé (nom de service propre à `vitrine`) : les deux projets restent
indépendants, même s'ils finissent par utiliser la même clé de compte.

Cette clé ne sert ici qu'à une chose : dériver la paire de clés Solana
(`vitrine.solana_signature`) qui signe les autorisations de transfert de
carte sur ce rail. Elle ne paie rien, ne signe aucun virement — seulement le
transfert on-chain de la carte elle-même.

Usage interactif : `python -m vitrine.outils.enregistrer_cle_ethereum`
"""

from __future__ import annotations

import keyring

NOM_SERVICE = "vitrine-eth-cle-privee"
_NOM_UTILISATEUR = "cle-privee"

TAILLE_CLE_OCTETS = 32


class ClePriveeAbsenteError(RuntimeError):
    """Aucune clé privée Ethereum enregistrée — signature Solana impossible."""


class ClePriveeInvalideError(ValueError):
    """La valeur fournie n'est pas 32 octets hexadécimaux."""


def _valider(cle_hex: str) -> bytes:
    nettoyee = cle_hex.strip().removeprefix("0x").removeprefix("0X")
    try:
        brute = bytes.fromhex(nettoyee)
    except ValueError as exc:
        raise ClePriveeInvalideError(f"Clé privée Ethereum invalide : {exc}") from exc
    if len(brute) != TAILLE_CLE_OCTETS:
        raise ClePriveeInvalideError(
            f"Clé privée Ethereum invalide : {len(brute)} octets, {TAILLE_CLE_OCTETS} attendus."
        )
    return brute


def enregistrer_cle_privee(cle_hex: str) -> None:
    """Valide (32 octets hex) puis écrit dans le coffre. Écrase toute valeur précédente."""
    _valider(cle_hex)
    keyring.set_password(NOM_SERVICE, _NOM_UTILISATEUR, cle_hex.strip())


def lire_cle_privee() -> bytes | None:
    """Lit la clé stockée sous forme d'octets bruts, ou None si absente."""
    brute = keyring.get_password(NOM_SERVICE, _NOM_UTILISATEUR)
    return None if brute is None else _valider(brute)


def effacer_cle_privee() -> None:
    try:
        keyring.delete_password(NOM_SERVICE, _NOM_UTILISATEUR)
    except keyring.errors.PasswordDeleteError:
        pass  # déjà absente — pas une erreur


def obtenir_cle_privee_valide() -> bytes:
    """Rend la clé courante en octets, ou lève si elle est absente.

    Ne redemande jamais de saisie interactive : une automatisation doit
    s'arrêter et prévenir, pas mendier un secret (même logique que
    `acheteur.auth.jeton.obtenir_jeton_valide`).
    """
    cle = lire_cle_privee()
    if cle is None:
        raise ClePriveeAbsenteError(
            "Aucune clé privée Ethereum enregistrée. Lancer : "
            "python -m vitrine.outils.enregistrer_cle_ethereum"
        )
    return cle
