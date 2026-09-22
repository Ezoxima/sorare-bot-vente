"""Enregistre la clé privée Ethereum du wallet Sorare dans le gestionnaire
d'identifiants Windows (`vitrine.cle_ethereum`). À lancer une fois, à la main.

Cette clé n'entre nulle part ailleurs dans le projet en clair : `getpass` la
lit sans l'afficher, elle ne transite par aucun fichier ni aucun log. Ce
script n'affiche que l'adresse Solana dérivée (publique, pas un secret) pour
que tu confirmes que c'est le bon compte, en la comparant au `senderAddress`
que `vitrine quotidien` affichera pour une carte Solana réelle.

Usage :
    python -m vitrine.outils.enregistrer_cle_ethereum
"""

from __future__ import annotations

import getpass
import sys

from vitrine import cle_ethereum
from vitrine.solana_signature import adresse_solana, cle_solana_depuis_cle_ethereum


def main(argv: list[str] | None = None) -> int:
    del argv  # aucun argument : la clé se saisit au clavier, jamais en ligne de commande
    cle_hex = getpass.getpass("Clé privée Ethereum du wallet Sorare (non affichée) : ")
    if not cle_hex.strip():
        raise SystemExit("Clé vide — abandon.")

    try:
        cle_ethereum.enregistrer_cle_privee(cle_hex)
    except cle_ethereum.ClePriveeInvalideError as exc:
        raise SystemExit(str(exc)) from exc

    cle_solana = cle_solana_depuis_cle_ethereum(cle_ethereum.obtenir_cle_privee_valide())
    adresse = adresse_solana(cle_solana)
    print("OK — clé enregistrée dans le gestionnaire d'identifiants Windows.")
    print(f"     Adresse Solana dérivée : {adresse}")
    print("     Vérifie qu'elle correspond au « senderAddress » d'une carte Solana réelle")
    print("     (visible dans une sonde `prepareOffer`, ou signalé par une erreur explicite")
    print("     si elle ne correspond pas au moment de publier).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
