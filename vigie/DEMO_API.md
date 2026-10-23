# API démo — pour le Dev 1

**Pas de page livrée dans ce lot.** Brancher votre page sur `http://127.0.0.1:8010`.
Tout est local ; CORS de l'API existante conservé. Aucun LLM ni appel payant.

```sh
make install
make demo-live SCENARIO=vigie/scenarios/jour_noir.yaml
make demo-replay
```

Options : `PORT=8010 SEED=42 SPEED=1.2`. La journée dure 180 ticks, soit environ
150 secondes à la vitesse par défaut, plus le démarrage. Le dernier résultat reste
consultable jusqu'à Ctrl+C, qui arrête les deux contrôleurs et leur Redis dédié.
`make demo-replay RECORDING=recordings/<nom>` choisit une sauvegarde ; sans argument,
il reprend le dernier direct. Le replay ne relance ni les agents ni les Vigies.

## Routes

| Route | Usage |
|---|---|
| `GET /demo` | Dernier paquet cohérent des **trois** banques + `history`, `running`, `error`. |
| `WS /demo/stream?since_tick=-1` | Un paquet complet par tick, ticks manqués rejoués depuis `since_tick` exclusif. |
| `GET /state?world=A` (ou `B`, `C`) | État complet au format existant de l'équipe. |
| `GET /graph?world=C` | Graphe au format existant. |
| `WS /stream?world=C&types=execution,alert` | Messages signés au format existant, sans changement pour A/B. |
| `POST /demo/record` | Sans corps : sauvegarde cohérente des trois banques ; réponse `{recording, replay}`. |

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
| `label` | Sans Vigie / Vigie classique / Couche 3 + gel. |
| `pnl_eur`, `loss_eur`, `fraud_eur` | P&L réel net de fraude ; perte = max(0, −P&L) ; montant volé positif. Paiements légitimes exclus des pertes. |
| `light` | Feu global observé : VERT, ORANGE, ROUGE. **VERT ne signifie pas « aucune action bloquée ».** |
| `executed`, `blocked`, `gate_blocked` | Cumuls : exécutions réussies, refus explicites d'Executor, dont refus du portique Vigie. |
| `executed_after_detection` | Exécutions strictement après le tick du premier constat ; null sans constat. |
| `last_execution_tick`, `detection_tick`, `first_block_tick` | Ticks, null si l'événement n'a pas eu lieu. Le constat peut être une vulnérabilité avant attaque. |
| `contaminated_executions` | Nombre noté par l'arbitre, **jamais fourni aux Vigies**. |
| `working_agents`, `paused_agents` | Agents financiers qui ne sont pas / sont en pause. Un heartbeat actif n'est pas une opération exécutée. |
| `decisions`, `last_decisions` | Décisions du portique de ce tick / les trois dernières décisions. A n'a pas de Vigie : listes vides. |
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
