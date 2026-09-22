"""Signature des `SolanaTokenTransferAuthorizationRequest` (rail Solana).

Rien ici n'est deviné : le format du message signé et la dérivation de la
paire de clés Solana viennent de la documentation officielle
https://github.com/sorare/api#examples (section « Signing Solana
authorization requests », citée le 2026-09-22). Confirmé le même jour par un
`prepareOffer` réel sur une carte Solana du compte (`Laurin Curda`) : les noms
de champs correspondent exactement à ceux utilisés ici.

## La dérivation

La clé Solana n'est pas une clé indépendante : c'est une dérivation SLIP-0010
(HD, ed25519) de la clé privée **Ethereum** du wallet Sorare, chemin durci
`m/44'/501'/0'/0'`. `deriver_cle_ed25519` implémente SLIP-0010 en toute
généralité (testée contre les vecteurs officiels du standard) ; le chemin
Sorare est appliqué par `cle_solana_depuis_cle_ethereum`.

## Le message

`construire_message` doit correspondre exactement à la doc — trois pièges
qu'elle documente explicitement, et qu'on ne retrouve pas en la relisant vite :

- `assetId` n'entre PAS dans le message (la carte est identifiée on-chain par
  `merkleTreeAddress` + `leafIndex`, pas par `assetId`).
- `senderAddress` n'entre pas non plus (il est impliqué par la clé qui signe).
- Le champ `'0x'` est un littéral (une donnée vide), pas un gabarit à remplacer.

`signer_autorisation` vérifie que la clé dérivée correspond bien au
`senderAddress` de la demande avant de signer — c'est la vérification que la
doc recommande, et elle est peu coûteuse à faire systématiquement.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from typing import Any

import base58
import nacl.signing

# m/44'/501'/0'/0' — chemin Solana standard, tous les indices durcis (seul
# mode que SLIP-0010 supporte pour ed25519).
CHEMIN_SOLANA_SORARE: tuple[int, ...] = (44, 501, 0, 0)

_SEL_ED25519 = b"ed25519 seed"
_BIT_DURCI = 0x80000000


class AdresseInattendueError(RuntimeError):
    """La clé Solana dérivée ne correspond pas au `senderAddress` demandé."""


def _hmac_sha512(cle: bytes, donnees: bytes) -> bytes:
    return hmac.new(cle, donnees, hashlib.sha512).digest()


def deriver_cle_ed25519(graine: bytes, chemin: tuple[int, ...]) -> tuple[bytes, bytes]:
    """SLIP-0010, dérivation ed25519 générique (dérivation durcie uniquement —
    la seule que SLIP-0010 définit pour cette courbe). Rend (clé_privée, code_chaine),
    32 octets chacun.
    """
    i = _hmac_sha512(_SEL_ED25519, graine)
    cle, code_chaine = i[:32], i[32:]
    for indice in chemin:
        donnees = b"\x00" + cle + (indice | _BIT_DURCI).to_bytes(4, "big")
        i = _hmac_sha512(code_chaine, donnees)
        cle, code_chaine = i[:32], i[32:]
    return cle, code_chaine


def cle_solana_depuis_cle_ethereum(cle_privee_eth: bytes) -> nacl.signing.SigningKey:
    """La paire de clés Solana du compte Sorare, dérivée de la clé Ethereum."""
    cle_privee, _ = deriver_cle_ed25519(cle_privee_eth, CHEMIN_SOLANA_SORARE)
    return nacl.signing.SigningKey(cle_privee)


def adresse_solana(cle: nacl.signing.SigningKey) -> str:
    return base58.b58encode(bytes(cle.verify_key)).decode("ascii")


def construire_message(request: Mapping[str, Any]) -> bytes:
    """Le message exact attendu par Sorare pour une `SolanaTokenTransferAuthorizationRequest`."""
    champs = [
        "TRANSFER",
        request["transferProxyProgramAddress"],
        request["merkleTreeAddress"],
        str(request["leafIndex"]),
        request["nonce"],
        str(request["expirationTimestamp"]),
        request["receiverAddress"],
        "0x",
        request["originator"],
    ]
    return ":".join(champs).encode("utf-8")


def signer_autorisation(request: Mapping[str, Any], cle_privee_eth: bytes) -> dict[str, Any]:
    """Rend l'entrée `solanaTokenTransferApproval` prête pour `approvals`.

    Lève :class:`AdresseInattendueError` si la clé Ethereum enregistrée ne
    correspond pas au `senderAddress` de la demande — signer quand même
    produirait une signature qui ne passera jamais la vérification côté
    Sorare, mais autant s'arrêter avant plutôt que de laisser croire à un
    problème réseau.
    """
    cle = cle_solana_depuis_cle_ethereum(cle_privee_eth)
    adresse = adresse_solana(cle)
    if adresse != request["senderAddress"]:
        raise AdresseInattendueError(
            f"clé Solana dérivée ({adresse}) différente du senderAddress attendu "
            f"({request['senderAddress']}) — mauvaise clé Ethereum enregistrée, "
            "ou compte différent."
        )
    empreinte = hashlib.sha256(construire_message(request)).digest()
    signature = cle.sign(empreinte).signature
    return {
        "signature": base58.b58encode(signature).decode("ascii"),
        "nonce": request["nonce"],
        "expirationTimestamp": request["expirationTimestamp"],
    }
