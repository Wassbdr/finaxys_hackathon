# API démo — pour le Dev 1

**Pas de page livrée dans ce lot.** Brancher votre page sur `http://127.0.0.1:8010`.
Tout est local ; CORS limité aux origines configurées ci-dessous. Aucun LLM ni appel payant.

```sh
make install
make demo-live SCENARIO=vigie/scenarios/jour_noir.yaml
make demo-replay
```

Options : `PORT=8010 SEED=42 SPEED=1.2`. La journée dure 180 ticks, soit environ
150 secondes à la vitesse par défaut, plus le démarrage. Le dernier résultat reste
consultable jusqu'à Ctrl+C, qui arrête les deux contrôleurs et leur Redis dédié.
`make demo-replay RECORDING=recordings/<nom>` choisit une sauvegarde ; sans argument,
il reprend le dernier direct. Le replay ne relance ni les agents ni les instances KRYS.

## Autorisation des commandes locales (direct et replay)

Les lectures restent accessibles sur `127.0.0.1` ; le CORS HTTP permet
leur lecture depuis les seules origines configurées. Les flux WebSocket sont des
lectures et ne reçoivent pas de commande. **Toute écriture HTTP**, y compris les
routes historiques `/record` et `/inject`, exige simultanément :

- `Origin` exactement dans la liste autorisée (sinon HTTP 403) ;
- `Content-Type: application/json` (sinon HTTP 415 après les vérifications précédentes).

Le terminal affiche les origines autorisées. Aucun secret de lancement n’est
généré, affiché ou demandé dans la page. Une question au chatbot requiert
seulement son texte. Aucun cookie d’authentification n’est utilisé ; l’écoute
reste exclusivement sur `127.0.0.1`, dans le direct comme dans le replay.

Par défaut, l'origine est `http://127.0.0.1:8010` (ou le `PORT` choisi).
Configurer **l'origine exacte de la page du Dev 1**, par exemple :

```sh
VIGIE_DEMO_ORIGINS=http://localhost:5173 make demo-live SCENARIO=vigie/scenarios/krach_rumeur.yaml
VIGIE_DEMO_ORIGINS=http://localhost:5173 make demo-replay
```

Plusieurs origines se séparent par une virgule. L'option CLI répétable
`--allowed-origin http://localhost:5173` prime sur cette variable. Pas de joker,
de chemin ni d'origine `null` : servir la page en HTTP local, plutôt qu'en `file:`.
`localhost` et `127.0.0.1`, ports inclus, sont des origines différentes.
Le navigateur envoie `Origin` ; le JavaScript ne doit pas essayer de le définir.
Le prévol CORS autorise `Content-Type` uniquement pour ces origines.

```js
const response = await fetch('http://127.0.0.1:8010/demo/record', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({})
});
if (!response.ok) throw new Error(`Enregistrement refusé : ${response.status}`);
const recording = await response.json();
```

Les requêtes simples de formulaire ou `text/plain` sont refusées. CORS n'est pas
l’autorisation : le contrôle d’origine et de contenu JSON s’applique avant l’exécution
de la route, même à un client qui ignore CORS. Pour un client hors navigateur,
fournir `Origin` explicitement et `Content-Type: application/json`. Les origines
étrangères ou absentes restent refusées avec 403 ; `text/plain` avec une origine
autorisée reste refusé avec 415, avant toute exécution de route.

Les commandes humaines restent désactivées par défaut : activation explicite
`--human-commands`, puis préparation et confirmation distinctes à usage unique
(120 secondes). Le replay ne les active jamais. Le SMTP réel reste désactivé
par défaut ; sa configuration explicite et ses limites d’envoi sont inchangées.

## Routes

| Route | Usage |
|---|---|
| `GET /demo` | Dernier paquet cohérent des **trois** banques + `history`, `running`, `error`. |
| `WS /demo/stream?since_tick=-1` | Un paquet complet par tick, ticks manqués rejoués depuis `since_tick` exclusif. |
| `GET /state?world=A` (ou `B`, `C`) | État complet au format existant de l'équipe. |
| `GET /graph?world=C` | Graphe au format existant. |
| `WS /stream?world=C&types=execution,alert` | Messages signés au format existant, sans changement pour A/B. |
| `POST /demo/record` | Corps JSON `{}` et autorisation ci-dessous : sauvegarde cohérente des trois banques ; réponse `{recording, replay}`. |

L'API est la même application `api.app.create_app`, alimentée par trois sources.
Aucun changement d'`api/app.py` ni de `sim/compare.py`. Utiliser `/demo` ou son flux
pour comparer les courbes au **même** tick : trois appels `/state` peuvent croiser
un tick en cours. L'injection interactive est désactivée dans ce profil ; le scénario
est planifié à l'identique dans A, B et C. `/demo/record` enregistre les trois mondes ;
le `/record` historique ne sauvegarde qu'un monde et ne suffit pas au replay triple.

## Contrat JSON v1

Un paquet du flux est exactement `{schema_version, tick, seed, banks, events, complete, replay}`.
`banks` contient `A`, `B`, `C`. `events` est la liste des commandes/notes du scénario
**de ce tick**. `complete` devient vrai au dernier tick ; `replay` distingue le secours.
À l'ouverture, `GET /demo` peut avoir `tick=-1`, `banks={}` ; attendre le premier paquet.
Le flux garde tous les ticks du run en mémoire. À chaque tick, les trois banques
sont calculées avant publication ; cible 1,2 tick/s, jamais de tick sauté si le
portable ralentit. En cas d'erreur, `/demo.error` est non nul et le flux envoie
`{schema_version:1, tick:<dernier tick envoyé>, error:<texte>}` puis se ferme.

Chaque banque contient exactement :

| Champ | Sens |
|---|---|
| `label` | Sans Krys / Contrôles classiques / Krys (Krys, couche 3). |
| `pnl_eur`, `loss_eur`, `fraud_eur` | P&L réel net de fraude ; perte = max(0, −P&L) ; montant volé positif. Paiements légitimes exclus des pertes. |
| `light` | Feu global observé : VERT, ORANGE, ROUGE. **VERT ne signifie pas « aucune action bloquée ».** |
| `executed`, `blocked`, `gate_blocked` | Cumuls : exécutions réussies, refus explicites d'Executor, dont refus du portique KRYS. |
| `executed_after_detection` | Exécutions strictement après le tick du premier constat ; null sans constat. |
| `last_execution_tick`, `detection_tick`, `first_block_tick` | Ticks, null si l'événement n'a pas eu lieu. Le constat peut être une vulnérabilité avant attaque. |
| `contaminated_executions` | Nombre noté par l'arbitre, **jamais fourni aux instances KRYS**. |
| `working_agents`, `paused_agents` | Agents financiers qui ne sont pas / sont en pause. Un heartbeat actif n'est pas une opération exécutée. |
| `decisions`, `last_decisions` | Décisions du portique de ce tick / les trois dernières décisions. A n'a pas de KRYS : listes vides. |
| `findings` | Six derniers constats ou refus utiles : `{tick, text, proof:[message_id]}`. |

Une décision est exactement `{tick, message_id, approved, reason, evidence}` :
`message_id` identifie la `risk_decision` soumise ; `approved` est un booléen ;
`reason` est la chaîne brute du contrôleur ; `evidence` contient jusqu'à trois
identifiants du journal. Exemple réel, C, graine 42, tick 24 :

```json
{"approved":false,"evidence":["risk:24:31","risk:20:26","executor:3:5"],"message_id":"risk:24:31","reason":"{\"evidence\":[\"risk:24:31\",\"risk:20:26\",\"executor:3:5\",\"executor:4:7\",\"executor:10:14\",\"executor:14:19\"],\"kind\":\"marked_operation\"}","tick":24}
```

Le paquet réel complet du tick 0 est versionné dans
[`demo_api_example.json`](demo_api_example.json) ; un test le compare exactement
à un vrai tick du scénario. Pour `GET /demo` s'ajoutent `history` (points
`{tick,A,B,C}`), `running` (simulation en cours), `error` (null ou texte).
Ne pas confondre `running=false` et un crash : le dernier paquet peut avoir `complete=true`.

## Limite à rendre visible

Dans **jour_noir**, C suspecte le ledger au tick 20. Puisque ce ledger est une
dépendance commune, C bloque toutes les nouvelles opérations, bien que son feu
reste VERT et ses agents actifs. B passe au rouge global après le premier IBAN volé.
Ne pas afficher « C continue les opérations saines » : ici, le compteur vaut zéro.
`blocked=0` chez B n'indique pas une absence de protection : après le rouge,
les agents ne proposent plus d'actions. Ces opportunités supprimées ne sont pas
comptées comme des refus explicites. Afficher aussi `light` et `paused_agents`.
Les vraies pertes servent à afficher et noter, jamais à décider.

## Variante 2 : dérive du marché, flux de paiement sains

```sh
make demo-live SCENARIO=vigie/scenarios/derive_marche.yaml
make demo-measure SCENARIO=vigie/scenarios/derive_marche.yaml
```

Même durée, API et replay. Seul `market-feed` est compromis au tick 40 ; aucune CVE,
ledger et paiements restent sains. `jour_noir.yaml` est conservé à l'identique.
Sur les graines 42 et 43, C détecte la dérive au tick 46 et bloque les ordres
contaminés suivants ; **7 / 4 paiements sains continuent après ce tick**. Deux ordres
contaminés ont été exécutés avant la détection dans chaque run. B ne détecte pas
cette dérive et reste VERT : ses plafonds ne sont pas franchis.

Le compteur `executed_after_detection` de C vaut ici 7 / 4 : ce sont des paiements,
pas une reprise du trading. Pour les distinguer en direct, lire le flux existant
`/stream?world=C&types=execution` : `payload.kind` vaut `payment` ou `order` et
`payload.status=executed` indique une réussite. La comparaison hors ligne emploie
le **même seuil temporel de C** pour A/B/C (`metrics.json[*].continuity`), avec les
identifiants d'exécutions et de refus pour retrouver chaque preuve dans le journal.
Cette notation emploie les annotations de l'arbitre après coup, jamais pour décider.
**Le P&L de C est moins bon sur ces deux graines** : ce scénario démontre le
confinement avec continuité des paiements, pas une économie financière.

## Régime normal puis crise : vraie chute ou rumeur

```sh
make demo-live SCENARIO=vigie/scenarios/krach_reel.yaml
make demo-live SCENARIO=vigie/scenarios/krach_rumeur.yaml
```

`make demo-measure SCENARIO=<même fichier>` rejoue A/B/C sur 42 et 43. Le format du
flux reste inchangé. Les deux scénarios gardent 60 ticks ordinaires, puis portent
le herding de 0,3 à 0,8. Le vrai krach injecte six nouvelles publiques (−8 à −12 %)
sur les trois actifs entre t60 et t75, sans attaque. La rumeur compromet seulement
news-wire à t60 : ses fausses dépêches natives arrivent à t61, t66 et t72 ; aucun
choc fondamental n'est injecté. Bruit de marché et news ordinaires de la graine
restent actifs. Aucun seuil des instances KRYS n'est adapté aux scénarios.

- **Vrai krach** : B passe au rouge à t66 / t75. C ne bloque aucune action au
  portique. Sur la graine 43, C produit toutefois deux faux gels, t118 et t178,
  alors que Risk a déjà arrêté le trading. Leur coût financier mesuré est nul.
  Ne pas afficher « attaque détectée » : `detection_tick=118` est ici un faux
  constat comportemental. La similarité porte sur des traces à règles (LLM off).
- **Rumeur** : premiers gels C à t89 / t80 ; 14 / 26 refus de demandes marquées
  contaminées par l'arbitre. Le gel réduit les pertes de 846 901,57 / 1 019 404,92 €
  par rapport au diagnostic C sans gel. B reste vert sur 42 et coupe tout à t143
  sur 43. Le gel ne constitue pas une preuve que la source news-wire est compromise.

Le JSON hors ligne ajoute `crisis.worlds[A|B|C]` : ventes proposées/exécutées,
volumes, pic de ventes sur le même actif en dix ticks, pairs impliqués, preuves,
arrêt de Risk, rouge global, gels et refus propres/contaminés. L'attribution
financière sépare bruit, vraies news, glissement et impact de prix avec résorption.
`crisis.freeze_effect` contient le diagnostic **hors ligne seulement** de C sans
le gel, pour isoler son effet ; les trois colonnes du direct restent A/B/C avec
le profil livré. « Aide / nuit / neutre » désigne le delta de P&L de cette ablation,
pas une garantie sur d'autres graines. Un vrai krach peut coûter sans attaque ;
un regroupement de ventes ne prouve pas une irrationalité.

À vitesse très élevée, le bail de B expire plus vite en temps réel : lors d'une
validation à `SPEED=10` sous charge, deux refus `STALE` ont changé la trajectoire
B de la rumeur 42. Ce n'était pas une détection de la rumeur. Garder `SPEED=1.2`
pour la démo ; les vérifications de ces crises sont rejouées à `SPEED=2`, sans
modifier le bail ni les seuils de B. Un portable lent peut demander une vitesse
encore plus basse. Les dossiers automatiques ont désormais un identifiant unique
pour que deux mesures lancées dans la même seconde ne mélangent pas leurs journaux.
