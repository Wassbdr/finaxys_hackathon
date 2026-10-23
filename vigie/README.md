# Vigie — branchement de l'équipe

Un processus Python indépendant, branché exclusivement sur Redis. Les agents,
services, exécuteur, API et journal sont ceux du dépôt de l'équipe. Argent et
données sont simulés. Aucun appel LLM, aucun service payant, aucun dashboard ajouté.

Dans le venv du dépôt (Python 3.11 ou plus) :

```sh
make test
make compare
make run
```

`make run` / `make procfile` génèrent `runs/vigie-config.yaml` avec
`gate.impl: bus`, `gate.default_on_timeout: reject`, `vigie.stub: false` et
`llm.enabled: false`. Ils apprennent d'abord le profil habituel sur 120 ticks de
référence sans attaque, même graine, et enregistrent le journal et les profils
sous `runs/vigie-reference-*`. Le Procfile généré et le Procfile statique utilisent
ce profil effectif ; générer celui-ci avec `make procfile` avant honcho.
Les valeurs stub de `config.yaml` restent le profil des tests historiques de
l'équipe et du lockstep historique. Lancer la démonstration avec les cibles Make
ci-dessus ; `python -m sim.launch` ne branche pas cette Vigie.

Les chemins générés sont relatifs lorsque `runs_dir` est relatif, sans chemin OVH.
Le comparatif n'utilise que TCP sur `127.0.0.1` et un broker Redis compatible
embarqué, sur un port libre ; il n'affecte pas le broker de la démonstration.
Les tests interdisent aussi les sockets par un hook d'audit Python. Les tests LLM
existants utilisent leurs doubles fournis par l'équipe, sans appel extérieur.

## Les quatre couches

1. Identité/intégrité : HMAC de chaque message, schéma fermé, émetteur connu,
   types/outils et destinataires autorisés, empreinte de configuration et du
   fichier de l'agent. Une clé révoquée est refusée au portique. L'empreinte déclarée
   est optionnelle dans le contrat de l'équipe ; le contrôle de fichier local
   ne prétend pas attester la mémoire d'un processus compromis.
2. Règles fixes : deux visas signés (Compliance et Risk), proposition originale
   retrouvée dans le trafic, anti-rejeu, montants, positions, exposition globale,
   VaR historique recalculée, liquidité, perte, KYC et changement d'IBAN.
   La source est l'état réel utilisé par `/state`, complété par `world:ledger`,
   `world:prices` et les exécutions signées du tick courant, jamais la vue de Risk.
   En cas d'état absent/périmé ou de bail périmé, le portique refuse.
3. Comportement : profils gelés du run de référence, cumul de grosses tranches
   sous le seuil individuel sur dix ticks, suspicion par agent qui baisse avec
   le temps. Le ralentissement est un feu orange adressé au seul agent concerné.
4. Cohérence : remontée transitive des `depends_on` et union des provenances
   originales. Un parent inconnu, un service compromis/figé/indisponible, une
   mauvaise version ou une réponse historiquement contaminée bloque l'action.
   Une confiance contredisant Quant ou une faillite non corroborée par les vraies
   dépêches déclenche une quarantaine ciblée. Les news injectées du scénario
   indiquent source et fiabilité ; une affirmation de l'agent ne fait pas preuve.

La Vigie observe aussi les propositions refusées avant le portique. Elle publie
un résumé signé vers `human` pour chaque décision. Elle renouvelle son heartbeat
et son `light_change` signé avec `expires_at_tick` à chaque tick. Rouge reste
verrouillé jusqu'à `human_decision` signé `human` avec `action: resume`.
Quarantaine révoque la clé au portique et les outils du registre, et adresse un
rouge au seul agent. `human_decision` signé avec `action: unblock` et l'identifiant
de l'agent restaure ses permissions. Rouge/quarantaine et l'anti-rejeu sont
conservés dans Redis lors d'un redémarrage de la Vigie.

Le monde fourni livre aussi les feux ciblés à son exécuteur, sans filtrer `to`.
Après un feu ciblé, la Vigie réaffirme donc le feu global **à l'exécuteur seul**.
Le Trader concerné garde son feu ciblé et les autres poursuivent ; un vrai rouge
global reste rouge. Un test emploie le véritable World de l'équipe pour cette règle.

Le statut détaillé est dans `{prefix}vigie:status` (registre sans clés, suspicion,
quarantaines, profils et propagation), les décisions dans
`{prefix}vigie:decisions`. Les alertes signées arrivent dans le journal et le flux
existants ; `/graph` reste celui de l'équipe.

## Démonstrations pour le jury

```sh
make compare
make compare SCENARIO=vigie/scenarios/trader_corrompu_49m.yaml
make compare SCENARIO=scenarios/vigie_debranchee.yaml
```

Chaque comparaison termine après la durée du YAML. Même graine et même scénario
pour A sans Vigie et B avec une Vigie réellement séparée. Résultats chiffrés,
journaux signés A/B, profil appris et log du processus sont sauvegardés dans un
nouveau dossier `runs/vigie-compare-*`. `TICKS=20` permet un court essai.

`avoided_loss_eur` est le P&L trading + fraude de B moins celui de A, sans compter
les virements légitimes comme pertes. `avoided_fraud_eur` isole la fraude ; il ne
faut pas additionner ces deux métriques. Une Vigie peut coûter de l'opportunité :
un écart négatif reste affiché. Le scénario des 4,9 M€ démontre l'isolement,
pas une promesse de gain. Son extension YAML `vigie_orders` est lue par
`vigie.scenario` dans le comparatif ; le lanceur historique `sim.inject scenario`
ne sait pas injecter cette extension.

Pour débrancher en direct pendant `make run`, utiliser la commande existante :

```sh
.venv/bin/python -m sim.inject kill-vigie
```

Elle utilise `{prefix}agent:vigie:pid`, enregistré par le vrai processus. Les
autres processus restent vivants et passent seuls à l'orange après le délai
existant de cinq ticks ; BusGate refuse sur expiration, sans remplacer le mort
par un stub. Le comparatif du scénario de déconnexion tue aussi le vrai PID.

## Frontière du contrat fourni

Les messages du bus et les visas sont signés. La réponse de `BusGate` fournie par
l'équipe est un JSON Redis sans vérification HMAC côté exécuteur : le broker
local fait donc partie de la frontière de confiance. Ce module ne transforme
pas ce contrat en transport authentifié contre un attaquant ayant accès en
écriture à Redis. Il ne modifie pas les fichiers de l'équipe pour le faire.
Le bail est vérifié par le portique ; les agents de l'équipe appliquent leur
délai de heartbeat et ne lisent pas eux-mêmes `expires_at_tick`.

L'auditeur LLM est désactivé et ne fait aucun appel API. La fonction d'avis
éventuelle ne peut qu'augmenter une suspicion et refuse le même modèle que celui
des agents ; elle ne donne aucun pouvoir d'exécution.
