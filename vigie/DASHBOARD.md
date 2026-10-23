# Données Vigie pour l’équipe dashboard

Aucune interface ajoutée. Le contrat JSON du bus de l’équipe reste inchangé,
avec HMAC et préfixe de monde. La Vigie publie à chaque tick :

```text
WS /stream?types=alert,heartbeat&world=B
  from: vigie
  to: dashboard
  type: heartbeat
  payload.kind: vigie_state
  payload.notification: false
  payload.state: <instantané décrit ci-dessous>
```

En mode simple, omettre `world=B`. Les messages sont signés, enregistrés et
rejoués avec le journal existant. Un résumé compact (voyants, scores, compteurs, sources isolées) apparaît aussi parmi
les `GET /state` → `alerts[]` de l’équipe, sous `kind: vigie_summary`, champ `state`.
Les graphes et preuves détaillées se lisent dans le heartbeat `vigie_state`.
Cette séparation évite de recopier vingt gros graphes dans chaque `/state`.
Le champ de base `/state.vigie` de leur API est conservé tel quel.

Pour un consommateur Redis côté serveur : `{prefix}vigie:status` contient le
même état. Ne pas exposer le broker au navigateur ni les clés de signature.

## Instantané `schema_version: 1`

| Champ | Utilisation |
|---|---|
| `tick`, `light` | Horloge et feu global |
| `agents[id].lights` | Quatre voyants : clés `1` identité/intégrité, `2` règles, `3` comportement, `4` cohérence ; VERT/ORANGE/ROUGE |
| `agents[id].suspicion`, `.level`, `.quarantined` | Jauge 0–100, niveau et isolement au portique |
| `orders[id][]` | 20 dernières propositions : tick, notionnel, actif, message_id ; limite unitaire dans `behavior[id].limit_eur` ou configuration |
| `behavior[id]` | D3-A : fenêtre 20, cumul, nombre entre 0,9·limite et limite exclue, maximum propre et seuil |
| `cusum[id][count\|mean]` | Valeur, z modifié, CUSUM et seuil calibré |
| `profiles[id]` | Référence propre, durée 300 ticks, maxima, médianes, MAD/planchers et seuils |
| `counters.raw_alerts`, `.incidents`, `.notifications` | Bandeau brut → incidents → notifications |
| `incidents[]` | Racine, niveau, raisons, identifiants de preuve, premier/dernier tick et nombre brut ; les répétitions sont regroupées |
| `isolated_services[]` | Sources isolées ; ne pas les confondre avec les agents victimes |
| `ledger_evidence[]` | Soldes annoncés, valeurs indépendantes attendues et messages témoins |
| `claim_evidence[]` | Carte dit/vrai : client, assertion, absence de visa lié, dépendances à examiner |
| `contamination_graph` | Nœuds et arêtes au format agents/services du contrat `/graph` |

Le graphe reprend `nodes[{id,kind,role,mode,contaminated}]` et
`edges[{source,target,kind,count,messages,transitive,contaminated,proof}]`.
`proof` fournit des identifiants concrets du journal. Les flèches de cette vue
suivent la propagation : service → agent consommateur, puis agent parent →
agent dépendant. Leur sens est donc explicite, distinct des dépendances
agent → service de la route `/graph` d’origine. Les nœuds victimes peuvent
être `contaminated: true` tout en restant hors quarantaine et avec suspicion zéro.
Le graphe de l’API existante `/graph` demeure disponible ; la vue Vigie en ajoute
l’interprétation causale et l’isolement des sources. L’agrégation évite de renvoyer
l’intégralité du journal à chaque tick ; tous les liens message par message sont
conservés dans le journal et le moteur de provenance.

## Notifications et décisions humaines

Un incident humain a `payload.kind: vigie_incident`, `to: human`, une phrase,
`incident_id`, `agent`, `reason`, `layer`, `proof`, `source`, `victim` et l’action.
Les instantanés de télémétrie ne comptent pas comme notifications humaines.
Un même incident peut recevoir plusieurs notifications si son niveau change.
Le retour sous 10 peut notifier une reprise ; une quarantaine ne se lève jamais
par oubli du score.

Les commandes humaines restent celles du contrat : `human_decision` signé par
`human`, `action: unblock`, `target: trader-3` pour libérer un agent, ou le nom
exact du service pour lever son isolement ; `action: resume` pour le rouge global.
Restaurer un service ou laisser baisser le score ne vaut pas décision humaine.
Les messages historiquement contaminés restent refusés après la restauration.

La coupure de la Vigie conserve les cinq ticks et l’orange local du contrat de
l’équipe. Le PID reste dans `{prefix}agent:vigie:pid` ; le bouton existant peut
utiliser `POST /inject` avec `{"command":"kill-vigie"}`.
