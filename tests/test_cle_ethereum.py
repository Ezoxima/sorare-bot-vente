"""Stockage de la clé privée Ethereum — keyring mocké, aucun vrai secret,
aucun vrai coffre Windows touché (même technique que le projet `acheteur`)."""

from __future__ import annotations

import keyring
import pytest
from keyring.backend import KeyringBackend

from vitrine.cle_ethereum import (
    ClePriveeAbsenteError,
    ClePriveeInvalideError,
    effacer_cle_privee,
    enregistrer_cle_privee,
    lire_cle_privee,
    obtenir_cle_privee_valide,
)

# Purement inventée pour le test — 32 octets hex, aucune valeur réelle.
_CLE_EXEMPLE_HEX = "11" * 32
_CLE_EXEMPLE_OCTETS = bytes.fromhex(_CLE_EXEMPLE_HEX)


class _KeyringMemoire(KeyringBackend):
    """Backend keyring en mémoire, pour ne jamais toucher le vrai coffre Windows en test."""

    priority = 1

    def __init__(self) -> None:
        self._stockage: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self._stockage[(service, username)] = password

    def get_password(self, service, username):
        return self._stockage.get((service, username))

    def delete_password(self, service, username):
        try:
            del self._stockage[(service, username)]
        except KeyError as exc:
            raise keyring.errors.PasswordDeleteError("absent") from exc


@pytest.fixture(autouse=True)
def _keyring_isole():
    ancien = keyring.get_keyring()
    keyring.set_keyring(_KeyringMemoire())
    yield
    keyring.set_keyring(ancien)


def test_enregistrer_puis_lire_round_trip() -> None:
    enregistrer_cle_privee(_CLE_EXEMPLE_HEX)
    assert lire_cle_privee() == _CLE_EXEMPLE_OCTETS


def test_enregistrer_accepte_le_prefixe_0x() -> None:
    enregistrer_cle_privee("0x" + _CLE_EXEMPLE_HEX)
    assert lire_cle_privee() == _CLE_EXEMPLE_OCTETS


def test_lire_sans_avoir_enregistre_renvoie_none() -> None:
    assert lire_cle_privee() is None


def test_obtenir_cle_privee_valide_leve_si_absente() -> None:
    with pytest.raises(ClePriveeAbsenteError):
        obtenir_cle_privee_valide()


def test_obtenir_cle_privee_valide_renvoie_la_cle() -> None:
    enregistrer_cle_privee(_CLE_EXEMPLE_HEX)
    assert obtenir_cle_privee_valide() == _CLE_EXEMPLE_OCTETS


def test_cle_trop_courte_rejetee_et_non_enregistree() -> None:
    with pytest.raises(ClePriveeInvalideError):
        enregistrer_cle_privee("1234")
    assert lire_cle_privee() is None


def test_cle_non_hexadecimale_rejetee() -> None:
    with pytest.raises(ClePriveeInvalideError):
        enregistrer_cle_privee("pas-une-cle-" * 5)


def test_effacer_cle_absente_ne_leve_pas() -> None:
    effacer_cle_privee()  # ne doit pas lever même si rien n'est enregistré


def test_effacer_cle_presente() -> None:
    enregistrer_cle_privee(_CLE_EXEMPLE_HEX)
    effacer_cle_privee()
    assert lire_cle_privee() is None
