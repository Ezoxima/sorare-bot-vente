# Vitrine

Remise en vente assistée des cartes Sorare. Deux façons de tarifer :

- **à la main** — `plan`/`relister` : lit les annonces closes sans acheteur, produit un plan que tu
  tarifes toi-même, republie sous contrôle explicite ;
- **automatiquement** — `quotidien` : repositionne chaque jour au prix le plus bas du marché, sans
  jamais descendre sous le prix d'achat + marge ; quand le plancher l'en empêche, prévient par mail
  au lieu de publier.

Les deux passent par le même modèle de sécurité : **mode à blanc par défaut**, publication toujours
plafonnée, confirmation tapée avant toute écriture.

Projet indépendant, non affilié à Sorare.

## Pourquoi ce dépôt existe à part de Pickdeck

Pickdeck est un assistant de **décision** : il classe, explique, et ne soumet jamais rien.
Vitrine **écrit** chez Sorare. Ce sont deux natures différentes, et mélanger les deux aurait fait
entrer une capacité d'écriture dans un outil dont toute la doctrine repose sur le contraire.

Conséquence assumée : Vitrine ne partage aucun code avec Pickdeck. Le client GraphQL et l'outil de
JWT en sont des reprises, pas des imports.

## Ce qui a été mesuré, et qu'il ne faut pas re-deviner

Sondé le 2026-09-19 contre l'API réelle, sur un compte réel.

| Question | Réponse mesurée |
|---|---|
| Faut-il signer quelque chose ? | **Non.** `prepareOffer` rend `authorizations: []` sur les 4 configurations de devise essayées. Témoin : un `assetId` inexistant est rejeté en `NOT_FOUND`, donc l'entrée est bien lue. |
| Faut-il la clé privée du compte ? | **Non**, conséquence directe du point précédent. Aucune clé privée n'entre dans cet outil. |
| Où est le prix d'une annonce ? | Sur `receiverSide.amounts`. `senderSide.amounts` vaut **zéro** sur 41 annonces sur 41 — c'est le côté qui envoie la carte. |
| Quelle devise ? | `EUR` sur 41 annonces sur 41, `wei` à `null`. Pas de conversion de change sur le chemin nominal. |
| Que vaut `duration` ? | 7 jours (604 800 s) en usage constaté, **comptés depuis la création**. Les 29 annonces observées affichaient `endDate - startDate = 604 680 s`, soit 120 s de moins : `startDate` vaut création + 2 min, ce qui laisse une fenêtre d'annulation avant publication. |
| Combien de cartes concernées ? | 12 annonces closes, dont 2 déjà relistées à la main et 2 cartes devenues non cessibles → **8 actionnables**. Flux à venir : ~5 annonces expirent par jour. |

⚠️ Le champ `type: SINGLE_SALE_OFFER` que montrent les exemples officiels **n'existe plus** dans le
schéma. Une vente simple se dit `receiveAssetIds: []` + `receiveAmount`.

Un second sondage, le 2026-09-20, a cadré `quotidien` :

| Question | Réponse mesurée |
|---|---|
| Où est la liste « À Vendre » ? | **Nulle part.** Seules 9 watchlists de joueurs existent, et le champ qui en rend les cartes est plafonné à 5 sans pagination. Le périmètre retenu est donc *la carte déjà mise en vente au moins une fois* (union des annonces en cours et closes sans acheteur, déjà lues par `inventaire.lire`). |
| Où est le prix d'achat ? | `anyCard.tokenOwner.amounts.eurCents` côté API. Une deuxième source avait été mesurée (`card.purchase_price_eur_cents` côté base Pickdeck), mais écartée : elle attribue le montant total d'un achat groupé à chacune des cartes qu'il contient, et le gain de couverture ne justifiait pas la dépendance à une base externe. |
| Que vaut le coût d'une carte forgée ou gagnée ? | Nul (`transferType` dans `{SHARDS, REWARD}`), doctrine déjà actée côté Pickdeck. |
| « Le moins cher » inclut-il mes propres annonces ? | **Oui, par défaut.** Sur Matte Smets, la carte la moins chère rendue par l'API pour ce joueur est ma propre annonce. `marche.py` les exclut systématiquement. |

Un troisième sondage, le 2026-09-21, a corrigé `marche.py` (deux fois le même jour) :

| Question | Réponse mesurée |
|---|---|
| `liveSingleSaleOffers` rend-il les annonces triées par prix ? | **Non.** « Sorted by updated time », d'après la doc du champ lui-même. Sur Willi Orbán (40 annonces en cours), les 30 premières (l'ancien plafond) ne contenaient pas la moins chère (1,50 €) ; le calcul rendait 1,92 € à la place, une carte bien réelle mais pas la meilleure. Corrigé par pagination jusqu'à épuisement (`hasNextPage`) avant de chercher un minimum, plutôt qu'un plafond fixe. |
| Le bon critère de comparaison est-il l'année de saison (`seasonYear`) ? | **Non — `inSeasonEligible`.** Expliqué par l'utilisateur, confirmé contre l'API : une carte hors saison (« classic ») se compare à toutes les cartes classic de la même rareté, peu importe leur année ; seule une carte encore *in season* se compare par année précise. `lowestPriceAnyCard(inSeason: Boolean, rarity: Rarity)` le confirme dans son propre schéma — pas de paramètre d'année. En comparant par `seasonYear` exact, le vrai minimum d'Orbán (1,12 €, saison 2025) était invisible depuis sa carte 2023 ; le calcul rendait 1,50 €/1,92 € au lieu de viser 1,11 €. |

Toujours le 2026-09-21, en soirée, trois changements demandés côté ergonomie/décision (aucune mesure
API, choix produit) :

- **Un prix à fixer dans tous les cas.** Même sans concurrent comparable, `politique.decider`
  propose désormais le plancher comme cible (au lieu de ne rendre aucun prix) — utile pour publier à
  la main.
- **Le rail Solana n'écarte plus le marché.** Ces cartes sont publiées à la main pour le moment (plus
  de wallet à déchiffrer, cf. plus bas), mais elles ont quand même besoin d'un prix : `quotidien.py`
  les inclut de nouveau dans l'appel `marche.py`, et `politique.decider` calcule le prix normalement
  avant de forcer le verdict en `BLOQUER` (texte générique, plus l'explication détaillée du wallet).
- **Surlignage vert dans le mail.** Une ligne `LAISSER`/`BLOQUER` où le prix actuel est déjà au moins
  aussi bon que la cible (`Decision.deja_moins_cher`) est surlignée en vert dans le tableau HTML :
  rien à faire, visible d'un coup d'œil.

## Installation

```powershell
cd C:\Users\gcochet\Documents\github\vitrine
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

Puis un `.env` (gitignoré) avec `SORARE_API_KEY`, `SORARE_JWT`, `SORARE_JWT_AUD`.
Le JWT vaut 30 jours ; le renouveler **avant** l'échéance :

```powershell
.\.venv\Scripts\python.exe -m vitrine.outils.jwt --email <ton-email> --aud vitrine
```

Le mot de passe est lu par `getpass` : jamais affiché, jamais stocké, jamais dans le code.
Sans JWT, l'API renvoie `currentUser: null` **sans erreur** — l'outil s'arrête plutôt que de te
montrer une file vide.

Pour `quotidien`, trois réglages optionnels de plus dans `.env` (voir `.env.exemple`) :
`GMAIL_EXPEDITEUR`, `GMAIL_MOT_DE_PASSE_APPLICATION`, `GMAIL_DESTINATAIRE` — les alertes. Absents,
`quotidien` affiche un avertissement au lieu d'envoyer un mail, mais continue de fonctionner.
Le mot de passe est un **mot de passe d'application** Google (paramètres de sécurité du compte) ;
un mot de passe de compte normal ne fonctionne pas et ne doit pas être utilisé.

Le mail part dès qu'il y a quelque chose à savoir : au moins une carte `REPOSITIONNER` (avec son prix
visé, pour la publier à la main si tu ne veux pas de `--executer`) ou `ALERTER`. Un passage qui ne
rend que `LAISSER`/`FREINER`/`BLOQUER` n'envoie rien — rien à décider, rien à lire.

Le mail est un tableau HTML (avec un repli texte brut pour les clients qui ne l'affichent pas) : les
cartes `REPOSITIONNER` passent en premier et en gras, prix visé inclus — pensé pour se lire d'un
coup d'œil et se coller tel quel dans un tableur (Gmail préserve les colonnes au copier-coller). Les
prix concurrents sont recalculés juste avant l'envoi, jamais réutilisés d'un calcul fait plus tôt
dans le passage — le marché bouge.

## Usage

```powershell
.\.venv\Scripts\python.exe -m vitrine.cli inventaire
.\.venv\Scripts\python.exe -m vitrine.cli plan --sortie plan.csv
# remplir la colonne prix_propose_eur dans Excel, puis :
.\.venv\Scripts\python.exe -m vitrine.cli relister --plan plan.csv
.\.venv\Scripts\python.exe -m vitrine.cli relister --plan plan.csv --executer --max 1
.\.venv\Scripts\python.exe -m vitrine.cli annuler --carte <slug|assetId> --executer

# Repositionnement automatique — mode à blanc par défaut :
.\.venv\Scripts\python.exe -m vitrine.cli quotidien
.\.venv\Scripts\python.exe -m vitrine.cli quotidien --executer --max-cartes 3
```

Le CSV sort en `;` et UTF-8 avec BOM : Excel francophone l'ouvre sans abîmer les accents.

`quotidien` accepte un fichier `perimetre.csv` optionnel (colonnes `carte`, `exclure`, `inclure` ;
`carte` est un slug ou un assetId) pour exclure nommément une annonce, ou, en repli, pour restreindre
à une liste explicite (`inclure=oui` sur au moins une ligne bascule tout le fichier en liste
d'inclusion).

## Le modèle de sécurité

Cinq verrous, dans cet ordre :

1. **Le mode à blanc est le défaut.** Oublier un drapeau ne publie rien.
2. **Aucun prix n'est calculé.** La colonne `prix_propose_eur` sort vide ; une ligne sans prix est
   écartée avec son motif. Aucune règle automatique ne décide d'un montant.
3. **`--max` plafonne à 1 par défaut.** L'accident redouté n'est pas une annonce de travers, c'est
   tout le stock reposté d'un coup.
4. **Confirmation tapée** : il faut saisir le nombre exact d'annonces pour que l'exécution parte.
5. **Une liste tronquée arrête tout.** Les annonces en cours servent à repérer les cartes déjà
   remises en vente à la main ; si la lecture est plafonnée et en cache une, cette carte redeviendrait
   candidate et recevrait une **seconde** annonce. Au-delà du plafond (`--n`, 50 par défaut), le
   programme s'arrête au lieu de traiter une liste partielle comme complète.

S'y ajoute un garde-fou côté API : si `prepareOffer` réclame un jour une signature, le programme
s'arrête et nomme le type demandé, au lieu d'envoyer `approvals: []` par habitude.

### `quotidien` : six verrous de plus

1. **Jamais de prix sans coût fiable.** Un coût `estimé` ou `inconnu` bloque la carte
   (`Verdict.BLOQUER`) — elle n'est **jamais** publiée automatiquement, quel que soit le prix
   concurrent.
2. **Le plancher (coût × 1,05) n'est jamais franchi.** S'il faudrait passer dessous pour être le
   moins cher, la carte alerte (`Verdict.ALERTER`) au lieu d'être publiée. **Pas de plancher absolu**
   (décidé le 2026-09-21, après avoir eu un plancher à 0,50 € par défaut) : une carte à coût quasi
   nul (essence forgée, gain) peut viser un prix très bas sans butée artificielle — seul le coût réel
   compte. **Un prix à fixer dans tous les cas** (décidé le 2026-09-21) : même sans concurrent
   comparable, le plancher lui-même sert de cible proposée — jamais de carte sans prix à lire dans le
   rapport ou le mail.
3. **`--max-cartes` plafonne les publications par passage** (défaut 3, bas volontairement) ; au-delà,
   les cartes qui auraient dû se repositionner freinent (`Verdict.FREINER`) au lieu d'être ignorées
   en silence.
4. **`--baisse-max-pct` plafonne la baisse d'un jour sur l'autre** (défaut 10 %) — même logique.
5. **Le rail Solana bloque toujours la publication automatique.** Mesuré le 2026-09-20 sur une
   carte réelle : `prepareOffer` y exige une signature de portefeuille que cet outil ne fournira
   jamais (la clé est chiffrée avec le mot de passe du compte, `PasswordEncryptedPrivateKey` —
   décision prise de ne pas la déchiffrer en code, cf. `politique.py`). Ces cartes se publient à la
   main sur le site pour le moment (décidé le 2026-09-21) — le prix est calculé normalement (elles en
   ont besoin pour la publication manuelle), seul le verdict est forcé en `BLOQUER`, avec un texte
   générique plutôt qu'une explication technique dédiée. Elles n'entrent jamais dans la règle d'arrêt
   en masse ci-dessous, sans quoi un compte majoritairement sur ce rail (62 % mesuré ici)
   interromprait *chaque* passage.
6. **Règle d'arrêt en masse.** Si plus d'un tiers des cartes du périmètre **hors rail Solana**
   tombent en `BLOQUER`, le passage s'interrompt avant la première écriture et envoie un mail : une
   donnée qui se dégrade en masse est un symptôme, pas un cas particulier à absorber carte par carte.

Le fichier `STOP` à la racine interrompt tout passage avant la moindre écriture, s'il est présent.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

169 tests, sans réseau. Chaque garde-fou a été cassé volontairement pour vérifier qu'au moins un test
tombe (« tests à dents ») — cinq mutations prioritaires, toutes vérifiées :

- ne pas exclure ses propres annonces du calcul de marché (`marche.py`) ;
- ignorer le plancher (`politique.py`) ;
- publier malgré un coût inconnu ou estimé (`politique.py`) ;
- comparer des cartes de rareté ou de statut classic/in-season différents (`marche.py`) ;
- dépasser le quota quotidien de publications (`politique.py`).

Trois mutations supplémentaires, ajoutées le 2026-09-21 en soirée : ne pas proposer de prix sans
concurrent comparable, exclure de nouveau le rail Solana du calcul de marché, et ne pas surligner en
vert une carte déjà au meilleur prix — les trois cassées puis revérifiées.

S'y ajoutent les dix mutations historiques de `plan.py`/`offre.py`/`cli.py` (lire le prix sur
`senderSide`, oublier un signal « déjà en vente », ignorer le bloc `errors` d'un payload, publier
malgré une demande de signature, ne pas dédupliquer une carte, supprimer le mode à blanc, désactiver
`--max`, inverser la confirmation, publier par défaut, tolérer deux prix pour la même carte).

Les tests d'écriture n'assertent pas le code de retour mais le **compteur de publications** : un
`assert espion == []` sur chaque chemin qui ne doit rien envoyer.

## Comment `quotidien` décide

`cout.py` résout le coût d'acquisition d'une carte, en cascade, sans jamais inventer de prix :
API Sorare → coût nul pour l'essence forgée ou un gain → sinon, recherche de la transaction
d'origine dans l'historique (`user.trades`) — une seule carte dedans, c'est le prix exact ;
plusieurs (achat groupé), le montant est réparti à parts égales, étiqueté `estime` et jamais publié
automatiquement → transaction introuvable, coût inconnu. `marche.py` trouve le prix concurrent le
plus bas à rareté égale et même statut classic/in-season (`inSeasonEligible`, **pas** l'année de
saison exacte — une carte classic de 2023 se compare à une classic de 2025, cf. « ce qui a été
mesuré »), mes propres annonces exclues. `politique.decider` croise les deux et
rend un verdict parmi cinq (`REPOSITIONNER`, `LAISSER`, `ALERTER`, `BLOQUER`, `FREINER`), toujours
avec le détail du calcul et, sauf coût non fiable, un prix à fixer (`prix_vise_cents`) — même sans
concurrent comparable (plancher proposé) et même sur rail Solana (publication manuelle). `quotidien.py`
orchestre l'ensemble et n'écrit jamais sans `--executer` et une confirmation tapée. Le mail récapitulatif
(`alerte.py`) surligne en vert les lignes déjà au meilleur prix (`Decision.deja_moins_cher`).

## Ce qui n'est pas fait

- **L'annulation avant republication d'une carte jamais listée en cours.** Pour une carte déjà en
  vente, `quotidien` annule l'annonce en cours avant d'en recréer une (`annuler` puis
  `creer_annonce`) — la question « `createSingleSaleOffer` remplace-t-il l'annonce en cours sans
  annulation ? » reste non tranchée, donc non exploitée.
- **Le MLB.** Le sport est un paramètre, mais rien n'a été mesuré hors football.
- **L'enregistrement de la tâche planifiée.** `vitrine-quotidien.ps1` et
  `register-vitrine-quotidien.ps1` existent et sont prêts, mais aucune tâche Windows n'a été
  enregistrée par construction — mettre des cartes en vente engage des actifs réels, donc
  `Register-ScheduledTask` reste une commande que **tu** lances, après avoir relu plusieurs jours de
  passages à blanc (étape 6 du plan).
