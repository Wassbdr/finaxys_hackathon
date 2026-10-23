# Vigie commune — proposition de fusion

Sources : Dev 1 `801cd70`, Dev 2 `94e7aa7`. Le mode intégré conserve les décisions,
les feux et le tableau de bord de Marius ; les constats de couche 3 les alimentent.
La proposition attend la relecture de Marius et Édouard.

## Dev 1 — Marius : métriques, politique et tableau de bord

`control.Controller` conserve le contrôleur, ses règles, son registre, sa politique,
son auditeur et son statut de tableau de bord. L'import historique
`from vigie.core import Controller, Refused` reste valide. `metrics/`, `graph.py`,
`detect.py`, `policy.py`, `provenance.py`, `shadow.py` et les interfaces du tableau
de bord sont conservés. `state.VigieState` adapte le grand livre fantôme au contrat
commun `core.VigieState`, avec ses méthodes `read` et `checkpoint`.

`control_marius.Controller` conserve aussi la version Dev 1 non couplée pour
comparaison et arbitrage ; le service intégré utilise exclusivement `control.Controller`.
La différence de reprise après `human unblock` doit être validée par les auteurs :
le Dev 1 peut libérer les nouvelles données d'un service, tandis que la quarantaine
persistante Dev 2 les refuse encore. L'intégration garde ce refus prudent ; elle
n'invente pas une politique de libération ou de score.

Les `Finding` des couches 1 et 3 sont transmis au même consommateur de politique.
Les poids, témoins et feux restent ceux du Dev 1 ; aucun nouveau poids n'est déduit
implicitement du score d'un détecteur. L'autorisation finale passe par ses règles
puis par les contraintes ciblées de la couche 3. La règle de gel reste provisoire,
comme dans la livraison Dev 2, en attendant sa politique définitive.

`herding_marius.py` conserve intégralement le calcul historique. Le champ de statut
`herding` reste celui du tableau de bord existant ; `herding_couche3` expose le
calcul Dev 2 séparément. L'ancien appel `Herding(cfg)` est compatible ; le
constructeur par liste de traders de `herding.py` utilise le calcul Dev 2.

[Documentation Dev 1 d'origine](README_DEV1.md), conservée pour retrouver les contrats historiques.

## Dev 2 — Édouard : contamination et mimétisme

Le contrat canonique est `core.Finding(tick, layer, kind, target, severity, evidence,
score)` et `core.VigieState`. `finding.Finding` réexporte exactement cette classe.
L'extension de présentation `detail` du Dev 1 reste accessible et apparaît dans
`as_dict()` ; les sept champs dataclass et leur sérialisation restent inchangés.

`contagion.py`, `herding.py`, `analytics.py`, `injection.py`, `freeze.py`,
`admission.py`, `validation.py`, les propositions humaines et les essais du Dev 2
sont conservés. `main.Runtime` reste disponible comme portique de couche 3 seule.
Le contrôleur intégré partage sa fabrique de messages signés avec cette couche,
qui fournit ses constats, ses preuves et ses refus ciblés. Une réponse d'injection
suspecte n'isole pas toutes les réponses saines du même service.

[Documentation Dev 2 d'origine](README_COUCHE3.md), [gel provisoire](FREEZE.md),
[admission](FAILOPEN.md), [rapprochement différé](MULTIPROCESS.md).

## Commandes et évaluations séparées

```sh
make test                  # essais de tests/ ET vigie/tests/, réseau et LLM interdits
make run                   # contrôleur intégré, profil propre, agents sans LLM
make couche3 NO_LLM=1       # couche 3 seule, portique minimal conservé
make demo-measure          # jour_noir, trois banques historiques, graines 42 et 43
make eval-couche3           # cinq cas Dev 2, graine 42 par défaut
make metrics               # banc Dev 1 conservé dans vigie.eval_metrics
make compare               # comparatif Dev 1, broker local dédié
make compare-sim           # ancien comparatif sim.compare
make propositions RUN=... # propositions humaines hors ligne
make surete-utilite        # plan Dev 2 de 640 runs, à remesurer sur cette fusion
```

L’ancien CLI `python -m vigie.eval --scenarios ...` reste routé vers le banc Dev 1 ;
le singulier `--scenario` et le défaut sans argument restent ceux de couche 3.

Le film de Marius et la démo à trois banques sont tous deux conservés. La colonne C
de cette démo reste la couche 3 seule ; elle ne mesure pas le contrôleur intégré.
Les modules `compare`, `improve`, `eval`, `eval_metrics`, `surete_utilite` et
`simulation` peuvent noter ou montrer la vérité terrain hors du contrôleur. Les
rapports `METRIQUES.md` et `SURETE_UTILITE.md` restent historiques.

Les deux formats du scénario trader sont gardés côte à côte :
`trader_corrompu_49m.yaml` pour le banc couche 3, et
`trader_corrompu_49m_marius.yaml` pour le banc historique Dev 1. Le comparatif Dev 1
peut également lire le format canonique explicite `corrupt-order`. Le choix d'un
scénario commun pour la présentation finale reste à valider avec les deux auteurs.

## Bus signé, métadonnées et redémarrage

Le service intégré lit uniquement le bus et ses propres checkpoints. Il ne lit
plus `params`, `graph`, `state`, les modes internes ou les étiquettes de simulation.
Les modifications opérateur sont publiées par l'injecteur sous forme d'alertes
signées. Les compteurs réseau sont publiés par World sous forme de heartbeats
signés `network_calls`, activés par les profils intégrés. Leur projection ne
contient que source, cible, type de lien, caractère transitif et nombre d'appels.
Le calcul historique `Graph.topology` reste disponible sur ces données publiques.

Les `/meta` sont récupérés au lancement ; leur publication signée par un service
peut aussi alimenter la couche 3. Une vue Risk avec `as_of_tick` passe par le
rapprochement différé avant le contrôle de conservation du Dev 1 ; les anciens
heartbeats conservent leur contrôle historique. Une vue honnête retardée ne
provoque donc pas un rouge de conservation.

`vigie.launch` crée un checkpoint de gel neuf dans le dossier du profil de
référence et passe explicitement `--fresh-freeze-state`. Rejouer ce Procfile sur
un checkpoint existant refuse de l'écraser. Pour reprendre un fichier, le service
accepte `--freeze-state CHEMIN` sans ce drapeau ; la règle prudente de la couche 3
sur la couverture perdue au redémarrage est conservée. Les appels de test à
`Controller` ou `serve` peuvent injecter leur checkpoint comme dans `main.Runtime`.

`HORIZON_NO_LLM=1` désactive aussi l'auditeur Mistral, même si une clé est présente.
Les backends factices injectés dans les essais restent utilisables.
