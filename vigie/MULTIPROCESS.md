# Recette multiprocessus

`python -m vigie.multiprocess --redis-server /chemin/redis-server --out /dossier/neuf`

Exécuter depuis la racine du dépôt. Nécessite Redis Server local ; aucune dépendance Python supplémentaire, aucune API LLM. Le harnais lance trois mondes isolés en parallèle : propre (180 ticks), rumeur (150), PHP2Shell (180), graine 42, cadence réelle d'un tick/seconde. Chaque monde a son Redis éphémère sans persistance, cinq services HTTP, un processus World/Executor, une KRYS avec checkpoint neuf explicite, sept processus d'agents exécutant `agents.runtime.main` sans modification de sa boucle. Les abonnements sont établis avant le départ ; les processus sont arrêtés à la fin. Le répertoire de sortie doit être neuf.

Quant, Traders et Compliance reçoivent un backend de règles qui dort uniformément entre 1 et 6 secondes. Chaque acteur a un générateur privé `42:<acteur>:latency`, sans cache. Traders et Compliance attendent aux vrais points d'appel ; Quant, qui n'appelle pas de LLM métier, reçoit explicitement une attente synthétique via une trace `justify` avant son calcul. Les réponses métier restent celles des règles existantes. Le backend API n'est jamais construit. Risk et Payment restent sans délai ajouté. La graine reproduit les marchés et les séries de délais par acteur ; l'ordonnancement des processus et donc le P&L ne sont pas déterministes.

Seul le harnais peut lire l'état du monde pour noter le P&L. KRYS conserve la règle bus signé et `/meta`. Journaux signés, réponses du portique, état final, logs par processus et résumé JSON restent dans chaque dossier. Les métriques séparent les suspicions de service, les gels, les refus du portique et les refus d'exécution ; les métriques herding ordinaires ne sont pas des suspicions. `--cases clean` et `--ticks N` permettent une recette ciblée (une durée raccourcie ne prouve pas les scénarios complets).

## Soldes retardés

Risk inclut désormais `view.as_of_tick`, issu du solde qu'il a réellement lu. Le tick d'émission du heartbeat reste distinct. KRYS compare au tick de la vue déclarée ; pour un ancien émetteur sans ce champ, elle conserve le tick de son heartbeat, jamais le tick courant de l'observateur. Les échantillons proviennent toujours des seules exécutions signées et des ticks signés.

La fenêtre est ±1 tick autour de la vue pour une lecture HTTP à cheval sur une frontière. Les vues horodatées attendent deux ticks signés avant comparaison : le ledger peut être publié avant l'annonce de son exécution sur le bus. L'attente et la fenêtre sont fixes ; les appels LLM de six secondes ne les élargissent pas. La file est bornée à 128 vues ; une saturation ou un tick impossible déclenche la garde conservatrice existante. Les anciens heartbeats gardent leur comparaison immédiate pour compatibilité. Une corruption persistante reste détectée après ces deux ticks. Cette tolérance ne garantit pas l'absence de faux positifs sous une panne réseau prolongée ou un retard de publication arbitraire.

Cette recette vérifie le détecteur dans le runtime séquentiel existant ; elle ne rend pas les agents concurrents et ne corrige pas leur traitement tardif des contrôles ou leurs propositions vieillies. La politique du Dev 1 et un backend LLM réel restent hors de cette mesure.
