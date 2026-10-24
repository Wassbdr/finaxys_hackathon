# Onglet Impact — Argus / Krys

L’onglet `/dashboard#impact` lit uniquement les archives publiées dans
`vigie/console/data/impact/`. Le résumé de `/demo/page` pointe vers cet onglet.
Les noms et valeurs du direct ne remplacent jamais les mesures archivées.

Six cartes montrent les comparaisons, leurs dénominateurs, l’échantillon et
une limite : contamination, débit sain, journées normales, premier constat,
fraude au virement et panne/relais. Les sources et leurs empreintes sont
accessibles par les liens de chaque carte, sans le dossier `runs/` d’origine.
`manifest.json` distingue le commit mesuré `cf4973a` du commit d’analyse
`48a980a` ; la panne a été mesurée sur `d179dd0` et livrée dans `dfdd053`.

La borne approximative de la règle de trois porte sur **319 opérations**,
pas sur dix journées : 3/319 ≈ 0,94 % à 95 %, uniquement sous l’hypothèse
que les opérations soient indépendantes. La dépendance au sein d’une journée
et les dix refus pendant un vrai krach sont affichés. Un gel normal sans
refus est également mentionné. La panne compare le même Krys avec et sans
relais ; aucun chiffre Sans Krys ou contrôles classiques n’est inventé pour
ce banc. Ses témoins sont réutilisés pour trois durées de panne.

Les quatre fichiers `fixed.json`, `normal.json`, `crash.json` et `fraud.json`
sont copiés octet pour octet des mesures. `detections.json` et `outage.json`
sont des extraits documentés, avec chemins de runs, message_id et empreintes.
Les ticks de premier vol sont relus dans les journaux signés déjà mesurés ;
l’ordre des messages prouve que le constat classique suit le premier virement
volé, parfois au même tick. Les barres de détection représentent le tick médian
parmi les runs avec constat ; les absences restent affichées comme telles.

Pour réexporter les mêmes archives, depuis la racine de son worktree :

```sh
.venv/bin/python -m vigie.console.impact_data \
  --measurements ../remesure-fusion/runs/remesure-fusion-cf4973a-surete-v2 \
  --outage ../cout-panne/runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83
```

Aucun de ces chemins n’est requis pour lancer la console livrée : toutes les
données qu’elle lit sont versionnées. Le serveur de l’équipe monte le routeur
`impact_api` ; `make demo-live` réutilise le même serveur API. Aucun nouveau
serveur, paquet ou service n’est nécessaire.

Le routeur ne propose que GET et la liste fermée de fichiers de cet onglet.
Le code ne crée aucune commande et ne modifie pas les gardes CSRF existants.
Les textes sont insérés par `textContent`, les liens de preuve restent dans
une liste locale fermée, les barres sans valeur sont vides. Une archive absente
ou illisible produit une erreur visible, sans chiffre de remplacement.
La charte reprend les jetons de la console ; les banques ont toutes la même
couleur d’encre, sans vert de victoire ni rouge attribué à un concurrent.

Essais : `make test` ; la suite interdit le réseau et le LLM. L’essai DOM utilise
Node lorsqu’il est disponible, sans navigateur ni appel réseau. Les contrôles
visuels du lot utilisent Chromium en mode hors ligne et interceptent toutes
les requêtes ; aucun serveur de simulation n’est lancé pour les captures.
