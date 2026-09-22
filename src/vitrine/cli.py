"""Ligne de commande. Cinq verbes ; trois écrivent (`relister`, `annuler`,
`quotidien`), deux ne font que lire (`inventaire`, `plan`).

    vitrine inventaire                      # lecture : où j'en suis
    vitrine plan --sortie plan.csv          # génère le plan, prix à remplir à la main
    vitrine relister --plan plan.csv        # MODE À BLANC (défaut) : montre, n'envoie rien
    vitrine relister --plan plan.csv --executer --max 1
    vitrine annuler --carte <slug|assetId> --executer
    vitrine quotidien                       # MODE À BLANC (défaut) : décisions, rien publié
    vitrine quotidien --executer --max-cartes 3

Le mode à blanc est le défaut, pas une option : l'oubli d'un drapeau ne doit
jamais publier une annonce. Et `--executer` reste plafonné (`--max` pour
`relister`, `--max-cartes` pour `quotidien`) parce que l'accident redouté
n'est pas « une annonce de travers », c'est « tout le stock reposté d'un coup ».

Le CSV sort en `;` et UTF-8 avec BOM : c'est ce qu'Excel francophone ouvre sans
mutiler les accents ni recoller les colonnes.
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import sys

from vitrine import cle_ethereum
from vitrine import config as conf
from vitrine import inventaire as inv
from vitrine import offre as off
from vitrine import plan as pl
from vitrine import quotidien as quo
from vitrine.client import ClientSorare

COLONNES = [
    "asset_id",
    "nom",
    "rarete",
    "rail",
    "fin_annonce",
    "prix_origine_eur",
    "prix_propose_eur",
    "motif",
]


def _client() -> ClientSorare:
    config = conf.charger()
    config.exiger_identite()
    return ClientSorare(config)


def _eur(centimes: int | None) -> str:
    return "" if centimes is None else f"{centimes / 100:.2f}"


def _afficher(lignes: list[pl.Ligne], titre: str) -> None:
    r = pl.resume(lignes)
    print(f"\n{titre} — n={r['total']}, retenues={r['retenues']}")
    for motif, n in sorted(r.items()):
        if motif not in ("total", "retenues"):
            print(f"    écartées — {motif} : n={n}")
    print(f"\n  {'carte':40} {'rail':11} {'ancien':>8} {'proposé':>8}  état")
    print("  " + "-" * 78)
    for ligne in lignes:
        etat = "à remettre en vente" if ligne.retenue else f"écartée : {ligne.motif}"
        print(
            f"  {ligne.nom[:40]:40} {ligne.rail:11} {_eur(ligne.prix_origine_cents):>8} "
            f"{_eur(ligne.prix_propose_cents):>8}  {etat}"
        )


def _lire_inventaire(n: int = 50) -> tuple[str, list[pl.Ligne], dict]:
    with _client() as client:
        brut = inv.lire(client, n=n)
    lignes = pl.analyser(brut["terminees_sans_acheteur"], brut["en_cours"])
    return brut["nickname"], lignes, brut


def cmd_inventaire(args: argparse.Namespace) -> int:
    pseudo, lignes, brut = _lire_inventaire(args.n)
    print(f"compte : {pseudo}")
    print(f"annonces en cours              : {brut['total_en_cours']}")
    print(f"annonces closes sans acheteur  : {brut['total_terminees']}")
    _afficher(lignes, "File de remise en vente")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    _, lignes, _ = _lire_inventaire(args.n)
    chemin = pathlib.Path(args.sortie)
    with chemin.open("w", encoding="utf-8-sig", newline="") as f:
        redacteur = csv.DictWriter(f, fieldnames=COLONNES, delimiter=";")
        redacteur.writeheader()
        for ligne in lignes:
            redacteur.writerow(
                {
                    "asset_id": ligne.asset_id,
                    "nom": ligne.nom,
                    "rarete": ligne.rarete,
                    "rail": ligne.rail,
                    "fin_annonce": ligne.fin_annonce[:10],
                    "prix_origine_eur": _eur(ligne.prix_origine_cents),
                    # Laissé vide exprès : aucune règle ne décide d'un prix à ta place.
                    "prix_propose_eur": "",
                    "motif": ligne.motif or "",
                }
            )
    retenues = sum(1 for x in lignes if x.retenue)
    print(f"Plan écrit dans {chemin} — {len(lignes)} lignes, {retenues} à tarifer.")
    print("Remplis la colonne `prix_propose_eur` (en euros, ex. 0,45), puis :")
    print(f"    vitrine relister --plan {chemin}")
    return 0


def _charger_prix(chemin: pathlib.Path) -> dict[str, int]:
    """Lit les prix décidés. Une valeur illisible arrête tout — pas de ligne ignorée."""
    prix: dict[str, int] = {}
    with chemin.open(encoding="utf-8-sig", newline="") as f:
        for i, rang in enumerate(csv.DictReader(f, delimiter=";"), start=2):
            brut = (rang.get("prix_propose_eur") or "").strip().replace(",", ".")
            if not brut:
                continue
            try:
                centimes = round(float(brut) * 100)
            except ValueError as exc:
                raise SystemExit(
                    f"{chemin}, ligne {i} : prix « {brut} » illisible. Corrige et relance."
                ) from exc
            if centimes <= 0:
                raise SystemExit(f"{chemin}, ligne {i} : prix {brut} € — attendu > 0.")
            asset = rang["asset_id"]
            if asset in prix:
                # Deux prix pour la même carte : le « dernier gagne » choisirait
                # un montant à ta place, en silence.
                raise SystemExit(
                    f"{chemin}, ligne {i} : la carte {asset} a déjà un prix "
                    f"({prix[asset] / 100:.2f} €). Garde une seule ligne par carte."
                )
            prix[asset] = centimes
    return prix


def cmd_relister(args: argparse.Namespace) -> int:
    chemin = pathlib.Path(args.plan)
    if not chemin.exists():
        raise SystemExit(f"Plan introuvable : {chemin}. Lance d'abord `vitrine plan`.")
    prix = _charger_prix(chemin)
    _, lignes, _ = _lire_inventaire(args.n)
    lignes = pl.appliquer_prix(lignes, prix)
    a_faire = [x for x in lignes if x.retenue]

    _afficher(lignes, "À EXÉCUTER" if args.executer else "MODE À BLANC — rien n'est envoyé")
    if not a_faire:
        print("\nRien à faire.")
        return 0

    total = sum(x.prix_propose_cents or 0 for x in a_faire)
    print(
        f"\n  n={len(a_faire)} annonces, total demandé {total / 100:.2f} €, "
        f"durée {args.duree // 86400} j."
    )

    if not args.executer:
        print("\nMode à blanc : aucune annonce créée. Ajoute --executer pour publier.")
        return 0

    if len(a_faire) > args.max:
        raise SystemExit(
            f"\n{len(a_faire)} annonces à publier mais --max vaut {args.max}. "
            "Relève --max sciemment, ou vide des prix dans le plan."
        )
    reponse = input(f"\nPublier ces {len(a_faire)} annonces ? Tape le nombre pour confirmer : ")
    if reponse.strip() != str(len(a_faire)):
        print("Annulé — rien n'a été envoyé.")
        return 1

    cle_privee_eth = cle_ethereum.lire_cle_privee()
    with _client() as client:
        for ligne in a_faire:
            assert ligne.prix_propose_cents is not None
            approbations = off.preparer(
                client, ligne.asset_id, ligne.prix_propose_cents, cle_privee_eth=cle_privee_eth
            )
            annonce = off.creer_annonce(
                client,
                ligne.asset_id,
                ligne.prix_propose_cents,
                approbations=approbations,
                duree_s=args.duree,
            )
            print(
                f"  publiée — {ligne.nom[:40]:40} {ligne.prix_propose_cents / 100:.2f} € "
                f"→ {annonce.statut}, visible {annonce.debut} → {annonce.fin}"
            )
    return 0


def cmd_annuler(args: argparse.Namespace) -> int:
    """Retire une annonce en cours. Même modèle que la publication : confirmation tapée."""
    with _client() as client:
        brut = inv.lire(client, n=args.n)
        try:
            offre = pl.trouver_annonce_en_cours(brut["en_cours"], args.carte)
        except LookupError as exc:
            # Comme partout ailleurs dans ce programme : une entrée invalide
            # sort proprement (message + code de sortie), pas en traceback brut.
            raise SystemExit(str(exc)) from exc
        carte = offre["senderSide"]["anyCards"][0]
        montants = (offre.get("receiverSide") or {}).get("amounts") or {}
        print(f"\nAnnonce trouvée : {carte['name']}")
        print(f"  prix     : {_eur(montants.get('eurCents'))} €")
        print(f"  visible  : {offre.get('startDate')} → {offre.get('endDate')}")
        print(f"  statut   : {offre.get('status')}")
        if not args.executer:
            print("\nMode à blanc : rien n'est annulé. Ajoute --executer pour retirer.")
            return 0
        if input("\nRetirer cette annonce ? Tape OUI pour confirmer : ").strip() != "OUI":
            print("Annulé — l'annonce reste en ligne.")
            return 1
        statut = off.annuler(client, offre["blockchainId"])
        print(f"Annonce retirée — statut {statut}.")
    return 0


def cmd_quotidien(args: argparse.Namespace) -> int:
    """Le passage quotidien : périmètre → coûts → marché → décisions → mail →
    publications sous quota. Voir `quotidien.executer_passage` pour le détail."""
    config = conf.charger()
    config.exiger_identite()
    with ClientSorare(config) as client:
        brut = inv.lire(client, n=args.n)
        return quo.executer_passage(
            client,
            config,
            inventaire_brut=brut,
            perimetre_chemin=pathlib.Path(args.perimetre),
            executer=args.executer,
            max_cartes=args.max_cartes,
            baisse_max_pct=args.baisse_max_pct,
        )


def main(argv: list[str] | None = None) -> int:
    # La console Windows par défaut (cp1252, parfois cp850) ne sait pas encoder
    # les caractères déjà utilisés partout dans ce fichier (→, €, accents) : un
    # simple `print` peut planter avec `UnicodeEncodeError` selon la console.
    # `errors="replace"` : jamais de plantage pour un caractère d'affichage, au
    # pire un `?` — le même choix que le CSV en UTF-8 avec BOM fait pour Excel.
    for flux in (sys.stdout, sys.stderr):
        if hasattr(flux, "reconfigure"):
            flux.reconfigure(encoding="utf-8", errors="replace")

    parseur = argparse.ArgumentParser(prog="vitrine", description=__doc__)
    sous = parseur.add_subparsers(dest="commande", required=True)

    p_inv = sous.add_parser("inventaire", help="Lire les annonces en cours et closes.")
    p_inv.add_argument("--n", type=int, default=50, help="Annonces lues par file (défaut 50).")
    p_inv.set_defaults(fonction=cmd_inventaire)

    p_plan = sous.add_parser("plan", help="Écrire le plan CSV à tarifer.")
    p_plan.add_argument("--sortie", default="plan.csv")
    p_plan.add_argument("--n", type=int, default=50)
    p_plan.set_defaults(fonction=cmd_plan)

    p_rel = sous.add_parser("relister", help="Mode à blanc par défaut ; --executer pour publier.")
    p_rel.add_argument("--plan", default="plan.csv")
    p_rel.add_argument("--n", type=int, default=50)
    p_rel.add_argument("--executer", action="store_true", help="Publier pour de vrai.")
    p_rel.add_argument("--max", type=int, default=1, help="Plafond d'annonces publiées (défaut 1).")
    p_rel.add_argument("--duree", type=int, default=off.DUREE_DEFAUT_S, help="Durée en secondes.")
    p_rel.set_defaults(fonction=cmd_relister)

    p_ann = sous.add_parser("annuler", help="Retirer une annonce en cours.")
    p_ann.add_argument("--carte", required=True, help="assetId ou slug de la carte.")
    p_ann.add_argument("--executer", action="store_true", help="Retirer pour de vrai.")
    p_ann.add_argument("--n", type=int, default=50)
    p_ann.set_defaults(fonction=cmd_annuler)

    p_quo = sous.add_parser(
        "quotidien",
        help="Repositionner chaque jour au plus bas du marché, sans passer sous le plancher. "
        "Mode à blanc par défaut.",
    )
    p_quo.add_argument("--n", type=int, default=50)
    p_quo.add_argument(
        "--perimetre", default="perimetre.csv", help="Fichier d'exclusions/inclusions."
    )
    p_quo.add_argument("--max-cartes", type=int, default=3, dest="max_cartes",
                        help="Plafond de publications par passage (défaut 3).")
    p_quo.add_argument("--baisse-max-pct", type=float, default=10.0, dest="baisse_max_pct",
                        help="Baisse maximale d'une carte en un jour, en %% (défaut 10).")
    p_quo.add_argument("--executer", action="store_true", help="Publier pour de vrai.")
    p_quo.set_defaults(fonction=cmd_quotidien)

    args = parseur.parse_args(argv)
    return args.fonction(args)


if __name__ == "__main__":
    sys.exit(main())
