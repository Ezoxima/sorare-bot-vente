# Prompt de reprise — Vitrine, remise en vente automatique des cartes Sorare

*À coller au début d'une nouvelle session, dans `C:\Users\gcochet\Documents\github\vitrine`.*
*Rédigé le 2026-09-20, corrigé le 2026-09-20 (même jour — un incident réel a corrigé le point 2 dans
l'après-midi). Tout ce qui suit a été mesuré contre l'API réelle — ne le re-vérifie pas sans raison,
construis dessus. **Exception : le point 2 doit être pris au sérieux, pas ré-optimisé pour « ça
devrait marcher maintenant » — c'est justement ce genre d'excès de confiance qui a coûté une annonce
réelle aujourd'hui.***

---

## Ce qu'est ce projet

Un outil qui remet en vente mes cartes Sorare (football, limited) : il lit mes annonces, décide d'un
prix, et republie. C'est le **seul** code de mes projets qui écrit chez Sorare.

Il est volontairement séparé de Pickdeck (`C:\Users\gcochet\Documents\github\sorare_app_v2`), qui est
un assistant de décision en lecture seule. Ne propose jamais de fusionner les deux, ni de porter la
capacité d'écriture dans Pickdeck.

Travaille en français, tutoie-moi.

---

## LE PROCÉDÉ — comment une carte part réellement en vente

C'est la partie à ne pas perdre. Quatre choses à savoir avant le mode d'emploi — la deuxième a changé
de nature dans la journée du 2026-09-20.

### 1. Il faut un JWT, la clé API ne suffit pas

La clé API relève seulement les plafonds d'appel ; elle n'identifie personne, donc `currentUser`
revient `null` **sans erreur GraphQL**. C'est le silence le plus coûteux de cette API — une requête
qui a l'air d'avoir réussi et une liste d'annonces vide.

```powershell
.\.venv\Scripts\python.exe -m vitrine.outils.jwt --email <mon-email> --aud vitrine
```

Le flux : `GET /api/v1/users/<email>` → `salt` → hash bcrypt du mot de passe → mutation `signIn` →
`jwtToken(aud:)`. Si la double authentification se déclenche, `currentUser` revient `null` **sans
erreur** avec un `otpSessionChallenge` : ce n'est pas un échec, c'est une demande de code, et le
script la gère. Un token demandé depuis une IP inhabituelle déclenche la 2FA d'office.

Le token vaut **30 jours**. Le renouveler *avant* l'échéance : après, il faut repasser par le mot de
passe. Le mot de passe est lu par `getpass`, jamais affiché ni stocké — l'assistant n'y a pas accès
et ne doit jamais le demander.

Ensuite, chaque appel porte `Authorization: Bearer <token>` **et** `JWT-AUD: vitrine`. Les deux vont
ensemble : l'un sans l'autre est rejeté.

### 2. La signature dépend du rail — TRANCHÉ le 2026-09-20 : jamais de signature Solana automatique

⚠️ La version d'origine de ce document affirmait « il n'y a rien à signer », mesuré le 2026-09-19 sur
une seule carte. C'était incomplet, et ça a coûté une vraie annonce (récit ci-dessous). **La question
est maintenant réglée**, pas juste corrigée — inutile de la rouvrir sans fait nouveau.

Deux mesures, deux résultats opposés :

| Carte | Rail (`solanaAddress`) | `prepareOffer` |
|---|---|---|
| `daniel-jimenez-lopez-1990-03-05-2023-limited-122` (2026-09-19) | `null` — rail **historique** | `{ authorizations: [], errors: [] }` — rien à signer |
| Robby McCrorie (2026-09-20) | adresse Solana réelle — rail **Solana** | `authorizations: [{ __typename: "AuthorizationRequest" }]` — signature exigée |

**Sur le compte Ezox, 24 cartes sur 39 du périmètre suivi (62 %) sont sur le rail Solana.** Ce n'est
pas un cas rare : c'est la majorité.

**L'incident du 2026-09-20**, pour ne pas le refaire : `quotidien.py` annulait l'annonce en cours
d'une carte *avant* d'appeler `preparer`. Pour Robby McCrorie (déjà en vente à 1,50 €, cible calculée
à 1,48 €), `off.annuler` a réussi, puis `off.preparer` a levé `SignatureRequise` — la carte s'est
retrouvée délistée, sans remplacement. **Corrigé le jour même** : `preparer` (qui n'écrit rien) tourne
maintenant *avant* `annuler` dans `quotidien.executer_passage`, avec un test de régression
(`test_signature_requise_n_annule_pas_l_annonce_en_cours`, `tests/test_quotidien.py`).

**L'enquête sur la signature elle-même**, menée avec l'utilisateur (navigateur intégré + vente réelle
à la main sur sorare.com) :

1. La vente manuelle **fonctionne** — Sorare gère la signature en interne, mais l'a demandé de retaper
   son mot de passe **une seule fois** (pas de mot de passe requis pour annuler/relister ensuite).
2. Le schéma expose `type PasswordEncryptedPrivateKey { iv, payload, salt }` — la clé privée Solana
   est chiffrée avec le mot de passe du compte (format classique de keystore, à la MetaMask). Le
   mot de passe redemandé sert de dérivation pour déchiffrer la clé **côté client**, le temps de
   signer, dans le navigateur officiel de Sorare.
3. **Décision prise avec l'utilisateur : ne jamais reproduire ça dans `vitrine`.** Il faudrait
   rétro-ingénierier l'algorithme de chiffrement propriétaire de Sorare (KDF, chiffrement — non
   documentés), manipuler une clé privée réelle en clair dans un process Python, et aller activement
   contre un contrôle de sécurité que Sorare a posé exprès (ce n'est pas une API mal documentée,
   c'est une porte fermée volontairement). Le risque (perte d'actifs irréversible en cas de bug ou de
   fuite) dépasse largement le gain (éviter quelques clics manuels).
4. **Conséquence : le portefeuille est géré par Sorare** (confirmé par l'utilisateur), donc même sans
   la question de risque, il n'y a de toute façon aucune clé accessible en dehors de ce mécanisme.

**Ce qui a été codé en conséquence, le jour même** : le rail est un motif de `BLOQUER` dans
`politique.decider`. `quotidien.CarteSuivie` porte le rail (déduit de `solanaAddress`, comme
`plan.py`). Et surtout : **la règle d'arrêt en masse exclut le rail Solana de son calcul**
(`quotidien.executer_passage`) — sans ça, 62 % de `BLOQUER` structurel interromprait *chaque* passage,
y compris pour les cartes historiques qui, elles, pourraient vraiment être publiées. Testé à trois
niveaux (cœur pur `politique.py`, orchestration `preparer_passage`, bout en bout `executer_passage`),
chaque mutation vérifiée pour faire tomber un test. Revérifié contre le compte réel après coup : le
passage va jusqu'au bout au lieu de s'interrompre.

**Révisé le 2026-09-21 en soirée** (demande utilisateur, pas une mesure API) : le rail Solana ne
bloque plus *avant* le coût/marché, il ne fait plus que forcer le verdict final en `BLOQUER` — le prix
est calculé normalement (`quotidien.decider_toutes` interroge de nouveau le marché pour ces cartes),
et le détail réutilise le texte générique de la décision sous-jacente au lieu d'une explication
dédiée (« rail Solana, à publier à la main : {détail normal} »). Raison : ces cartes se publient à la
main pour le moment, et la main a besoin d'un prix. Le marqueur `Decision.deja_moins_cher` (pour le
surlignage vert du mail, cf. plus bas) traverse aussi ce forçage sans être écrasé.

**Effet de bord identifié, pas corrigé** : une carte dont l'annonce est annulée (`cancelOffer`, pas
« expirée sans acheteur ») disparaît temporairement du périmètre suivi par `quotidien.cartes_perimetre`
— elle n'apparaît ni dans les annonces en cours (annulée), ni dans les closes sans acheteur. Sans
conséquence pratique observée au-delà de l'incident (la carte redevient visible dès qu'elle est
relistée, comme Robby McCrorie l'a été manuellement), mais la fenêtre existe.

Le garde-fou historique reste vrai et vérifié : si `prepareOffer` réclame une signature,
`offre.preparer` **s'arrête** et nomme les types demandés, au lieu d'envoyer `approvals: []` par
habitude — c'est ce filet de sécurité, pas le filtrage par rail, qui a empêché l'incident de s'aggraver.

### 3. La séquence exacte des deux appels (rail historique — ce qui marche aujourd'hui)

```python
# 1) prepareOffer — ne crée rien, ne déplace rien
{
  "sendAssetIds":    ["<assetId de la carte>"],
  "receiveAssetIds": [],                                   # [] + receiveAmount = vente simple
  "receiveAmount":   {"amount": "<prix en centimes>", "currency": "EUR"},
  "settlementCurrencies": ["WEI", "EUR"],
  "clientMutationId": "vitrine-prep-<hex>"
}
# -> { authorizations: [], errors: [] }              (rail historique seulement, cf. point 2)

# 2) createSingleSaleOffer — ÉCRITURE RÉELLE, la carte part en vitrine
{
  "assetId":   "<assetId de la carte>",                    # PAS sendAssetIds : forme différente !
  "receiveAmount": {"amount": "<prix en centimes>", "currency": "EUR"},
  "settlementCurrencies": ["WEI", "EUR"],
  "dealId":    "<uuid4 hex, unique par opération>",
  "duration":  604800,                                     # 7 jours
  "approvals": [],                                         # vide, cf. point 2
  "clientMutationId": "vitrine-creer-<hex>"
}
# -> { tokenOffer { id blockchainId startDate endDate status }, errors: [] }
```

⚠️ **Les deux mutations n'ont pas la même forme d'entrée.** `prepareOffer` prend une liste
`sendAssetIds`, `createSingleSaleOffer` prend un `assetId` seul. C'est le piège facile.

⚠️ **Le champ `type: SINGLE_SALE_OFFER` des exemples officiels n'existe plus** dans le schéma. Le
recopier fait rejeter l'appel. Une vente simple se dit `receiveAssetIds: []` + `receiveAmount`.

⚠️ **`errors` est une liste dans le payload, pas une exception.** Un HTTP 200 avec `errors` non vide
est un échec. `client.executer_mutation` refuse de rendre un payload dont `errors` n'est pas vide —
ne jamais contourner ça, une mutation de marché qui échoue en silence laisse croire qu'une carte est
en vente alors qu'elle ne l'est pas.

⚠️ **`startDate` vaut création + 2 minutes, et `duration` se compte depuis la création.** Mesuré sur
29 annonces : `endDate - startDate = 604 680 s`, soit 604 800 moins 120. Ces deux minutes sont une
fenêtre d'annulation avant que l'annonce ne devienne publique.

⚠️ **Ordre `preparer` → `annuler` → `creer_annonce`, jamais `annuler` en premier** (cf. point 2,
incident du 2026-09-20). `preparer` n'écrit rien ; c'est le seul appel dont l'échec ne coûte rien.

### 4. Le mode d'emploi, en pratique

**Voie manuelle** — je choisis chaque prix :

```powershell
cd C:\Users\gcochet\Documents\github\vitrine
.\.venv\Scripts\python.exe -m vitrine.cli plan --sortie plan.csv
# ouvrir plan.csv dans Excel, remplir la colonne prix_propose_eur (virgule décimale acceptée)
.\.venv\Scripts\python.exe -m vitrine.cli relister --plan plan.csv                 # à blanc
.\.venv\Scripts\python.exe -m vitrine.cli relister --plan plan.csv --executer --max 1
```

La dernière commande réaffiche le tableau puis demande de **taper le nombre exact d'annonces** pour
confirmer. Tant que ce caractère n'est pas tapé, rien ne part. C'est ce chemin-là qui a servi à la
première mise en vente réussie (une carte du rail historique).

**Voie automatique** — la règle décide :

```powershell
.\.venv\Scripts\python.exe -m vitrine.cli quotidien                                # à blanc
.\.venv\Scripts\python.exe -m vitrine.cli quotidien --executer --max-cartes 3
```

⚠️ Tant que le point 2 n'est pas réglé (filtrage par rail), une carte Solana choisie par la règle
fait échouer tout le passage avec `SignatureRequise` — sans casse depuis le correctif d'ordre, mais
sans publication non plus, et les cartes suivantes du lot ne sont pas tentées.

**Pour retirer une annonce** (fenêtre de 2 min, ou n'importe quand après) :

```powershell
.\.venv\Scripts\python.exe -m vitrine.cli annuler --carte <slug|assetId> --executer
```

L'annulation utilise `blockchainId`, pas `id` — les deux existent sur une annonce et seul le premier
marche.

### 5. Qui lance quoi

**Je lance moi-même toute commande qui publie.** L'assistant construit, teste et me donne la
commande ; il ne déclenche pas la publication à ma place, parce que mettre une carte en vente engage
un actif réel. Ce n'est pas négociable et il est inutile d'insister — c'est d'ailleurs cohérent avec
le modèle de l'outil, dont la confirmation est tapée au clavier.

*Note du 2026-09-20 : dans la pratique, plusieurs commandes `--executer` ont été lancées par
l'assistant lui-même dans la session du jour, à chaque fois après confirmation explicite tapée dans
la conversation (jamais un mot de passe ni une clé, seulement un « oui » sur l'action décrite). Le
principe ci-dessus reste la référence ; s'il y a un doute sur ce qui est acceptable, redemander plutôt
que supposer.*

---

## Les faits mesurés — à ne pas re-sonder (sauf le rail, cf. point 2)

Compte Ezox, 2026-09-19 et 2026-09-20.

| Question | Réponse mesurée |
|---|---|
| Où est le prix d'une annonce ? | `receiverSide.amounts.eurCents`. **`senderSide.amounts` vaut zéro** sur 41 annonces sur 41 — c'est le côté qui envoie la carte, donc qui n'envoie pas d'argent. Lire le mauvais côté donne 0,00 € de façon crédible et fausse. |
| Quelle devise ? | `EUR` sur 41 sur 41, `wei` à `null`. Pas de conversion de change sur le chemin nominal. |
| Quelle durée ? | 7 jours (604 800 s), comptés depuis la création. |
| Où est la liste « À Vendre » ? | **Nulle part.** Onze noms de champs essayés. Seules 9 watchlists de *joueurs* existent, et `playersPanel` est **plafonné à 5 sans pagination** (liste de 14 → 5 lus). Le périmètre retenu est donc : *la carte que j'ai déjà mise en vente au moins une fois* (union annonces en cours + closes sans acheteur — voir l'effet de bord au point 2 sur une carte annulée). |
| Où est le prix d'achat ? | `anyCard.tokenOwner.amounts.eurCents` (+ `transferType`). Couverture directe API : 25 cartes sur 42. Le reste passe par une recherche dans `user.trades` (voir plus bas) : une transaction à une seule carte donne un prix exact (provenance `transaction`), plusieurs cartes donnent une estimation à parts égales (provenance `estime`, jamais publiée automatiquement). |
| Et les cartes sans prix d'achat direct ? | `transferType` dans `{SHARDS, REWARD}` → coût cash nul par nature (forge, gain), plancher nul. Les autres (achat dont l'API n'a pas gardé le montant, peu importe le type) passent par la recherche de transaction ci-dessus. |
| « Le moins cher » inclut-il mes annonces ? | **Oui, par défaut de l'API.** Sur Matte Smets, la carte la moins chère rendue par l'API *est la mienne*, à 8,49 €. Une règle naïve se sous-coterait elle-même tous les jours. `marche.py` les exclut via `tokens.liveSingleSaleOffers(playerSlug:)`, qui rend le vendeur (`sender.slug`), comparé au `slug` de l'utilisateur courant (pas son `nickname` — les deux existent et diffèrent). |
| Combien de cartes sont sous leur prix d'achat ? | **14 sur 25** à coût connu par l'API seule. Pavard acheté 37,69 € et proposé à 0,60 € ; Konaté acheté 31,04 € et proposé à 2,00 €. Le plancher à +5 % génère donc beaucoup d'alertes et peu de repositionnements sur le vieux stock. C'est voulu : en dessous, je fais la vente moi-même. |
| Quel rail exige une signature ? | **Solana** (`solanaAddress` non nul) — `AuthorizationRequest` exigée. Le rail **historique** (`solanaAddress: null`) n'en demande aucune. 24 cartes sur 39 du périmètre suivi sont sur Solana (62 %). |
| Deux cartes de saisons différentes sont-elles comparables ? | **Ça dépend de `inSeasonEligible`, pas de `seasonYear`.** Une carte classic (hors saison en cours) se compare à toute autre carte classic de même rareté, peu importe l'année. Seule une carte encore in season se compare par année précise. Mesuré sur Willi Orbán (classic, 2023) : le vrai minimum était 1,12 € (une carte classic de 2025), invisible en filtrant sur `seasonYear=2023`. |

**Chemins GraphQL utiles**, trouvés à tâtons (l'introspection est fermée, mais les messages
« Did you mean » guident) :

- `anyCards(slugs: [String!])` — lot de cartes. `anyCard(slug:)` / `anyCard(assetId:)` existe aussi
  mais **ne s'aliase pas** (« Duplicated root field » — vérifié deux fois, sur `anyCard` et sur
  `tokens`). Pour interroger la même famille de champ plusieurs fois dans une requête, aliaser le
  champ *imbriqué*, jamais le champ racine lui-même (ex. `tokens { j0: liveSingleSaleOffers(...) }`,
  pas `j0: tokens { liveSingleSaleOffers(...) }`).
- Le joueur d'une carte se lit via `anyPlayer { slug }` sur `AnyCardInterface` — **pas** `player`,
  qui n'existe pas sur cette interface (message d'erreur explicite, corrigé le 2026-09-20).
- `players(slugs: [String!])` à la racine — pas `football.players`, qui n'existe pas.
- `tokens.liveSingleSaleOffers(playerSlug:, first:)` — les annonces en cours d'un joueur, avec
  `sender { ... on User { slug } }`. ~1 758 de complexité pour 15 annonces, plafond 30 000 → environ
  15 joueurs par requête.
- `currentUser.myWatchlists(sport:)` → `{ id slug title totalPlayersCount playersPanel { anyPlayer { slug } } }`.
- `player.lowestPriceAnyCard(rarity:, inSeason:)` existe mais **mélange les saisons** et inclut mes
  annonces : ne pas s'en servir pour décider d'un prix.
- `publicMinPrices` rend `null` sur toutes les cartes essayées.
- `user(slug:).trades(after:, sortByEndDate: DESC)` — l'historique de transactions, même requête que
  Pickdeck (`sorare_app_v2/backend/app/sync/trades.py`, reprise en lecture, pas en import). Sert à
  retrouver le prix d'achat d'une carte que l'API ne donne pas directement (`cout.trouver_transaction`).

**Plafonds** : clé API 200 appels/min, complexité 30 000, profondeur 12. Un 429 renvoie
`Retry-After` en secondes — le client le respecte plutôt que d'improviser un backoff.

**Piège d'environnement, sans rapport avec l'API** : la console Windows par défaut (`cp1252`) plante
sur les caractères déjà utilisés partout dans le code (`→`, `−`, accents) avec `UnicodeEncodeError`.
`cli.py:main()` force l'UTF-8 en sortie (`errors="replace"`) depuis le 2026-09-20 — sans ça, un
`print` normal peut faire planter la commande en cours d'exécution, y compris après une écriture
réelle déjà partie.

---

## État du dépôt au 2026-09-21 (soir)

**169 tests au vert, `ruff check` propre. Aucun dépôt Git — `git init` jamais fait.** Une seule
dépendance d'exécution : `httpx` (`psycopg` a été ajouté puis retiré le même jour, voir plus bas).

```
src/vitrine/
  config.py      lecture de .env ; sans JWT, arrêt net plutôt qu'une file vide
  client.py      transport GraphQL : retries, Retry-After, et le refus des `errors` de payload
  inventaire.py  lecture des annonces en cours + closes sans acheteur (+ `slug` de l'utilisateur,
                 nécessaire pour que `marche.py` s'exclue lui-même — pas `nickname`)
  plan.py        cœur pur : qui est remettable en vente, et recherche d'une annonce à annuler
  cout.py        prix d'achat avec sa provenance (api / transaction / sans_cout / estime / inconnu) ;
                 recherche de transaction généralisée à tout `transferType`, pas seulement
                 `DIRECT_OFFER` — une transaction à une carte donne un prix exact, plusieurs cartes
                 une estimation ; porte aussi `inSeasonEligible` depuis le 2026-09-21 (cf. marche.py)
  marche.py      le moins cher du marché, mes propres annonces exclues (alias sur le champ imbriqué,
                 pas sur `tokens`, cf. plus haut). Deux corrections le 2026-09-21, la deuxième plus
                 importante que la première :
                 (a) pagine jusqu'à épuisement (`hasNextPage`) — `liveSingleSaleOffers` trie par date
                 de mise à jour, pas par prix, donc un plafond fixe (l'ancien `first: 30`) peut cacher
                 la moins chère (mesuré sur Willi Orbán, 40 annonces réelles, la vraie moins chère à
                 1,50 € hors des 30 premières) ;
                 (b) compare par `inSeasonEligible` (classic/in-season), **pas** par `seasonYear` exact
                 — expliqué par l'utilisateur : une carte classic se compare à toute autre carte
                 classic de même rareté, peu importe l'année ; seule une carte encore in season se
                 compare par année précise. `lowestPriceAnyCard(inSeason: Boolean, rarity: Rarity)` le
                 confirme dans son propre schéma (pas de paramètre d'année). Sur Willi Orbán (classic,
                 2023), le vrai minimum était 1,12 € (saison 2025) — invisible en comparant par
                 `seasonYear` exact, qui rendait 1,50 €/1,92 € à la place. `marche.Carte.saison` a été
                 remplacé par `in_season: bool | None` (None = statut inconnu → aucun match, prudent).
  politique.py   cœur pur : le verdict par carte ; un prix visé dans tous les cas (même sans
                 concurrent, le plancher sert de cible — décidé le 2026-09-21 soir) ; le rail Solana
                 force le verdict en `BLOQUER` **après** le calcul normal du prix (revu le 2026-09-21
                 soir — avant, il bloquait avant même de regarder coût/marché), texte générique plutôt
                 qu'une explication dédiée ; `Decision.deja_moins_cher` marque une carte déjà au
                 meilleur prix (survit au forçage Solana, pour le surlignage du mail) ; plus de
                 plancher absolu depuis le 2026-09-21 matin, seul `coût × 1,05` fait plancher
  alerte.py      mail Gmail par SMTP (bibliothèque standard) ; testé avec un vrai envoi le 2026-09-20 ;
                 déclenché par `REPOSITIONNER` ou `ALERTER` (depuis le 2026-09-21 — avant, `ALERTER`
                 seul déclenchait le mail) ; corps HTML en tableau (`composer_rapport_html`) depuis le
                 2026-09-21, `REPOSITIONNER` en tête et en gras, repli texte brut conservé ; une ligne
                 `deja_moins_cher` est surlignée en vert (ajouté le 2026-09-21 soir)
  quotidien.py   orchestration du passage quotidien ; ordre `preparer` → `annuler` → `creer_annonce`
                 depuis le correctif du 2026-09-20 (cf. point 2) ; règle d'arrêt en masse qui exclut
                 le rail Solana de son calcul (sinon 62 % de BLOQUER structurel l'interromprait
                 systématiquement) ; `preparer_perimetre`/`decider_toutes` séparés depuis le
                 2026-09-21 pour recalculer le marché juste avant le rapport/mail, pas avant ;
                 `decider_toutes` interroge de nouveau le marché pour les cartes Solana depuis le
                 2026-09-21 soir (elles ont besoin d'un prix pour la publication manuelle)
  cli.py         5 commandes ; 3 écrivent (relister, annuler, quotidien) ; force l'UTF-8 en sortie
  outils/jwt.py  génération du JWT, à lancer par moi
vitrine-quotidien.ps1 / register-vitrine-quotidien.ps1   tâche planifiée Windows (pas enregistrée)
```

**Écarté le 2026-09-20** : une deuxième source de coût d'achat via la base Pickdeck
(`PICKDECK_DATABASE_URL`, lecture seule de `card.purchase_price_eur_cents`, dépendance `psycopg`).
Implémentée, testée, puis retirée à ma demande — gain de couverture jugé insuffisant pour la
dépendance à une base externe. Ne pas la re-proposer sans que je la redemande.

**Configuré le 2026-09-20** : Gmail (`GMAIL_EXPEDITEUR`, `GMAIL_MOT_DE_PASSE_APPLICATION`,
`GMAIL_DESTINATAIRE`) — mot de passe d'application (pas le mot de passe du compte, l'option n'apparaît
que si la validation en deux étapes est active). Testé avec un envoi réel, fonctionnel.

### Les verdicts de `politique.py`

`REPOSITIONNER` (prix concurrent atteignable au-dessus du plancher) · `LAISSER` (déjà le moins cher,
ou aucun concurrent comparable — un prix est quand même proposé, le plancher) · `ALERTER` (il
faudrait passer sous le plancher → mail, pas de publication) · `BLOQUER` (coût inconnu ou estimé, **ou
carte sur rail Solana** → jamais de publication automatique, cf. point 2 ; le prix est calculé et
fourni même quand le verdict est forcé en `BLOQUER` par le rail) · `FREINER` (le mouvement dépasse une
bride).

Prix visé = concurrent le plus bas − 1 centime, borné en bas par `coût × 1,05` **et rien d'autre** —
**pas de plancher absolu** (décidé le 2026-09-21 : un plancher à 0,50 € par défaut avait existé, il
forçait un prix plancher artificiel sur les cartes à coût quasi nul, ce n'était pas voulu). Sans
concurrent comparable, ce même plancher sert de prix visé (décidé le 2026-09-21 soir) — jamais de
carte sans prix à lire dans le rapport ou le mail.

### Les brides de `quotidien`

`--max-cartes` 3 · `--baisse-max-pct` 10 · `--executer` absent par défaut · un fichier `STOP` à la
racine interrompt tout avant la première écriture · et si plus d'un tiers des cartes **hors rail
Solana** tombent en `BLOQUER`, le passage s'arrête sans rien publier et envoie un mail (le rail
Solana est exclu de ce calcul depuis le 2026-09-20, cf. point 2).

### Le mail (2026-09-21)

Envoyé en HTML (tableau, avec repli texte brut) dès qu'il y a au moins une carte `REPOSITIONNER` ou
`ALERTER` — avant, seul `ALERTER` déclenchait l'envoi, ce qui rendait muet un passage à blanc qui
n'avait pourtant qu'un prix à fixer à la main. Les lignes `REPOSITIONNER` passent en premier et en
gras. Les prix concurrents (`marche.plus_bas_prix_concurrent`) sont recalculés juste avant l'envoi
(`quotidien.decider_toutes`, appelée depuis `executer_passage` juste avant le rapport/mail, jamais
plus tôt) — demandé explicitement : un prix lu en tout début de passage peut déjà être faux au moment
d'agir dessus. `preparer_passage` reste disponible tel quel (coûts + marché + décision en un appel)
pour les tests et les usages simples, mais `executer_passage` ne l'utilise plus : il appelle
`preparer_perimetre` (périmètre + coûts, stable) puis `decider_toutes` (marché + décision, volatile)
séparément.

---

## Méthode attendue

- **Mesurer avant d'affirmer.** Toutes les règles ci-dessus viennent de sondes contre l'API réelle.
  Pas de « ça devrait marcher ». Le point 2 est l'exemple de ce qui arrive quand une mesure sur une
  seule carte est généralisée trop vite.
- **Échec bruyant plutôt que valeur par défaut silencieuse.** Un `errors` non vide, un prix `null`,
  un `tradeableStatus` inattendu, une liste tronquée : on s'arrête et on le dit.
- **Jamais un indicateur sans son effectif.**
- **Des tests qui mordent.** Un test qui passe aussi avec la version fautive ne teste rien : casser
  volontairement le code et vérifier qu'au moins un test tombe. Les régressions déjà couvertes de
  cette façon : lire le prix sur `senderSide`, oublier un des deux signaux « déjà en vente », ignorer
  le bloc `errors`, publier malgré une demande de signature, ne pas dédupliquer une carte, supprimer
  le mode à blanc, désactiver `--max`, inverser la confirmation, tolérer deux prix pour une carte,
  ne pas exclure ses propres annonces du marché, ignorer le plancher, publier malgré un coût inconnu,
  comparer des saisons différentes, dépasser le quota du jour, **annuler une annonce avant d'avoir
  confirmé qu'on peut la recréer**, **compter le rail Solana dans la règle d'arrêt en masse**,
  **ne pas proposer de prix sans concurrent comparable**, **exclure de nouveau le rail Solana du
  calcul de marché**, **ne pas surligner en vert une carte déjà au meilleur prix**.
- **Secrets hors du dépôt**, dans `.env` gitignoré. Ne jamais me demander de coller un mot de passe
  ou une clé privée dans la conversation — et ne jamais coder de logique de signature blockchain
  (point 2, tranché : décision définitive de ne pas le faire, pas juste faute d'avoir trouvé comment).

---

## Ce qui reste ouvert

1. **Le dépôt n'est pas versionné.** Pas de `git init`, donc aucun historique sur un code qui écrit
   chez Sorare — et qui a déjà, une fois, mal tourné. **À faire en priorité.**
2. **La règle de prix n'est pas validée par la mesure.** Le plancher « achat + 5 % » est une consigne,
   pas un résultat. La vraie question n'est pas « quel prix » mais « quel prix trouve preneur en moins
   de X jours » — et il y a de la matière pour y répondre : 6,7 millions de ventes réelles dans la
   base Pickdeck (`card_market_sale`). Attention au piège repéré : une médiane par joueur mélange
   toutes les saisons d'une même rareté et sort des prix absurdes (0,50 € demandé contre 5,57 € de
   médiane sur une carte d'ancienne saison).
3. **Le périmètre perd temporairement une carte dont l'annonce a été annulée** (ni « en cours », ni
   « close sans acheteur »). Observé sur Robby McCrorie pendant l'incident du 2026-09-20 ; redevenu
   visible une fois relistée. Pas corrigé, impact pratique limité constaté jusqu'ici.
4. **Défaut repéré chez Pickdeck, à consigner dans `specs/11`** : `backend/app/sync/trades.py:209`
   attribue le **montant total d'une transaction à chacune** des cartes qu'elle contient. Sur un achat
   groupé, le coût par carte y est surévalué d'un facteur égal au nombre de cartes — ça fausse déjà
   son écran de rentabilité. C'est pour ça que cette source a été écartée comme repli pour le coût
   (implémentée puis retirée le 2026-09-20, cf. « État du dépôt »).
5. **La tâche planifiée n'est pas inscrite.** Les deux scripts PowerShell existent, rien n'est
   enregistré dans le planificateur Windows. Le rail Solana n'est plus un obstacle à ça en soi (il
   bloque proprement sans casser le passage), mais la règle de prix (point 2) mérite d'être validée
   par plus de passages à blanc relus avant d'automatiser complètement.
6. **Le MLB n'a jamais été mesuré.** Le sport est un paramètre, mais tout ce qui précède vaut pour le
   football.
7. **Erreur rencontrée le 2026-09-22 — non traitée, consignée telle quelle** :
   ```
   PS C:\Users\gcochet\Documents\github\vitrine> python -m vitrine.outils.enregistrer_cle_ethereum
   ...\python.exe: Error while finding module specification for 'vitrine.outils.enregistrer_cle_ethereum'
   (ModuleNotFoundError: No module named 'vitrine')
   ```
   `python` résolu ici est l'interpréteur système
   (`C:\Users\gcochet\AppData\Local\Programs\Python\Python312\python.exe`), pas celui du venv du
   projet — `vitrine` n'y est pas installé. Même famille de piège que documenté ailleurs dans ce
   fichier pour `vitrine.cli` : les commandes de ce projet s'invoquent via
   `.\.venv\Scripts\python.exe -m ...`, jamais via un `python` nu qui peut pointer ailleurs selon le
   `PATH` du moment.
