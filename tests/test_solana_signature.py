"""SLIP-0010 (vecteurs officiels du standard) + format du message Solana
(doc officielle https://github.com/sorare/api#examples)."""

from __future__ import annotations

import hashlib

import base58
import nacl.signing
import pytest

from vitrine.solana_signature import (
    AdresseInattendueError,
    adresse_solana,
    cle_solana_depuis_cle_ethereum,
    construire_message,
    deriver_cle_ed25519,
    signer_autorisation,
)

# Vecteur officiel SLIP-0010, courbe ed25519, vecteur de test 1 — récupéré
# verbatim (texte brut, pas résumé) depuis
# https://raw.githubusercontent.com/satoshilabs/slips/master/slip-0010.md
# le 2026-09-22.
GRAINE_VECTEUR_1 = bytes.fromhex("000102030405060708090a0b0c0d0e0f")


def test_deriver_cle_ed25519_vecteur_officiel_maitre() -> None:
    cle, code_chaine = deriver_cle_ed25519(GRAINE_VECTEUR_1, chemin=())
    assert cle.hex() == "2b4be7f19ee27bbf30c667b642d5f4aa69fd169872f8fc3059c08ebae2eb19e7"
    assert code_chaine.hex() == "90046a93de5380a72b5e45010748567d5ea02bbf6522f979e05c0d8d8ca9fffb"


def test_deriver_cle_ed25519_vecteur_officiel_m_0h() -> None:
    # Chemin m/0H : SLIP-0010 ed25519 ne dérive qu'en durci, donc l'indice 0
    # passé ici correspond bien à « 0H » du vecteur officiel.
    cle, code_chaine = deriver_cle_ed25519(GRAINE_VECTEUR_1, chemin=(0,))
    assert cle.hex() == "68e0fe46dfb67e368c75379acec591dad19df3cde26e63b93a8e704f1dade7a3"
    assert code_chaine.hex() == "8b59aa11380b624e81507a27fedda59fea6d0b779a778918a2fd3590e16e9c69"


def test_construire_message_suit_exactement_le_format_documente() -> None:
    # assetId et senderAddress sont volontairement absents du message (cf.
    # docstring du module) même s'ils sont présents dans la demande reçue.
    demande = {
        "assetId": "0xCECI_NE_DOIT_PAS_APPARAITRE",
        "transferProxyProgramAddress": "Gz9o1yxV5kVfyC53fFu7StTVeetPZWa2sohzvxJiLxMP",
        "merkleTreeAddress": "DrZsJbXzdk1NmxUE8Z5rGr5VaXH7MthLHMMb9eb8bf48",
        "leafIndex": 115503,
        "nonce": "208034",
        "expirationTimestamp": 1791308853,
        "receiverAddress": "HHYSHiAhevYkSUgwhoAQwVk9nybKzpT1VMMdo5yo6PQC",
        "senderAddress": "3RdBUsxm9HBn4yJEQT95rZbue75N1SjgerUTZPWYTudm",
        "originator": "DHV45VXX4pEjTRzXQz2SVHC3EScM9j7u2ncvDwdZLB4J",
    }
    message = construire_message(demande)
    assert message == (
        b"TRANSFER:Gz9o1yxV5kVfyC53fFu7StTVeetPZWa2sohzvxJiLxMP:"
        b"DrZsJbXzdk1NmxUE8Z5rGr5VaXH7MthLHMMb9eb8bf48:115503:208034:"
        b"1791308853:HHYSHiAhevYkSUgwhoAQwVk9nybKzpT1VMMdo5yo6PQC:0x:"
        b"DHV45VXX4pEjTRzXQz2SVHC3EScM9j7u2ncvDwdZLB4J"
    )
    assert b"CECI_NE_DOIT_PAS_APPARAITRE" not in message


def _demande(senderAddress: str, **surcharge: object) -> dict[str, object]:
    base = {
        "assetId": "0xasset",
        "transferProxyProgramAddress": "ProxyAddr111111111111111111111111111111111",
        "merkleTreeAddress": "TreeAddr1111111111111111111111111111111111",
        "leafIndex": 42,
        "nonce": "7",
        "expirationTimestamp": 1234567890,
        "receiverAddress": "ReceiverAddr111111111111111111111111111111",
        "senderAddress": senderAddress,
        "originator": "OriginatorAddr11111111111111111111111111111",
    }
    base.update(surcharge)
    return base


def test_signer_autorisation_produit_une_signature_ed25519_verifiable() -> None:
    cle_eth = b"\x11" * 32
    cle_signature = cle_solana_depuis_cle_ethereum(cle_eth)
    adresse = adresse_solana(cle_signature)
    demande = _demande(senderAddress=adresse)

    approbation = signer_autorisation(demande, cle_eth)

    assert approbation["nonce"] == demande["nonce"]
    assert approbation["expirationTimestamp"] == demande["expirationTimestamp"]

    signature = base58.b58decode(approbation["signature"])
    empreinte = hashlib.sha256(construire_message(demande)).digest()
    # Ne lève pas si la signature est valide pour ce message et cette clé.
    nacl.signing.VerifyKey(bytes(cle_signature.verify_key)).verify(empreinte, signature)


def test_signer_autorisation_leve_si_adresse_derivee_differente() -> None:
    cle_eth = b"\x22" * 32
    demande = _demande(senderAddress="UneAdresseQuiNeCorrespondPasDuTout1111111111")
    with pytest.raises(AdresseInattendueError):
        signer_autorisation(demande, cle_eth)
