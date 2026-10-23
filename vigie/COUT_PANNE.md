# Argus — coût d’une panne de Krys

Base d179dd0 ; aucune attaque, aucun LLM, aucun réseau. Deux graines fixées : 42 et 43. Une journée = 300 ticks de 1 seconde (5 minutes simulées). Coupure SIGKILL à t60 ; nouveau PID après 10, 30 ou 60 ticks. Amorçage initial explicite sur fichier absent, comme --fresh-freeze-state ; pas de réamorçage après la panne. Décisions humaines signées resume et unfreeze(*) à redémarrage + 10 ticks, délai fixé avant mesure. Aucun effacement de checkpoint ni réamorçage neuf.

Rejouer : `make cout-panne` ; vérifier : `make verify-cout-panne RUN=<dossier>`. Reprendre/restituer sans écraser les preuves : `python -m vigie.panne --resume <dossier>`.

## Résultats

| Graine | Coupure (temps simulé) | Mode | Refus / montant € | Non proposées / montant demandé € | Exécutions du témoin perdues / montant € | Δ P&L € | Premier refus | Reprise effective / première opération | Indisponibilité |
|---:|---:|---|---:|---:|---:|---:|---:|---|---|
| 42 | 10 ticks | [redémarrage actuel](../runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83/42/panne-10/result.json) | 28 / 57 230 805,06 | 5 / 14 971 893,48 | 29 / 61 512 606,90 | +9 085,97 | 66 | PAS DE REPRISE (redém. t70, humain t80) | ≥240 ticks |
| 42 | 10 ticks | [relais attesté](../runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83/42/relais-10/result.json) | 0 / 0,00 | 0 / 0,00 | 0 / 0,00 | +0,00 | aucun | continu dès t60 ; opération t66 | 0 ticks |
| 42 | 30 ticks | [redémarrage actuel](../runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83/42/panne-30/result.json) | 27 / 56 659 805,06 | 6 / 15 542 893,48 | 29 / 61 512 606,90 | +9 085,97 | 66 | PAS DE REPRISE (redém. t90, humain t100) | ≥240 ticks |
| 42 | 30 ticks | [relais attesté](../runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83/42/relais-30/result.json) | 0 / 0,00 | 0 / 0,00 | 0 / 0,00 | +0,00 | aucun | continu dès t60 ; opération t66 | 0 ticks |
| 42 | 60 ticks | [redémarrage actuel](../runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83/42/panne-60/result.json) | 27 / 55 211 436,25 | 6 / 15 542 893,48 | 29 / 61 512 606,90 | +9 085,97 | 66 | PAS DE REPRISE (redém. t120, humain t130) | ≥240 ticks |
| 42 | 60 ticks | [relais attesté](../runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83/42/relais-60/result.json) | 0 / 0,00 | 0 / 0,00 | 0 / 0,00 | +0,00 | aucun | continu dès t60 ; opération t66 | 0 ticks |
| 43 | 10 ticks | [redémarrage actuel](../runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83/43/panne-10/result.json) | 35 / 85 824 382,02 | 25 / 52 915 443,02 | 48 / 96 079 037,79 | −469 923,40 | 67 | PAS DE REPRISE (redém. t70, humain t80) | ≥240 ticks |
| 43 | 10 ticks | [relais attesté](../runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83/43/relais-10/result.json) | 0 / 0,00 | 0 / 0,00 | 0 / 0,00 | +0,00 | aucun | continu dès t60 ; opération t67 | 0 ticks |
| 43 | 30 ticks | [redémarrage actuel](../runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83/43/panne-30/result.json) | 34 / 79 807 066,30 | 26 / 53 842 443,02 | 48 / 96 079 037,79 | −469 923,40 | 67 | PAS DE REPRISE (redém. t90, humain t100) | ≥240 ticks |
| 43 | 30 ticks | [relais attesté](../runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83/43/relais-30/result.json) | 0 / 0,00 | 0 / 0,00 | 0 / 0,00 | +0,00 | aucun | continu dès t60 ; opération t67 | 0 ticks |
| 43 | 60 ticks | [redémarrage actuel](../runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83/43/panne-60/result.json) | 34 / 74 263 128,18 | 26 / 53 418 944,73 | 48 / 96 079 037,79 | −469 923,40 | 67 | PAS DE REPRISE (redém. t120, humain t130) | ≥240 ticks |
| 43 | 60 ticks | [relais attesté](../runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83/43/relais-60/result.json) | 0 / 0,00 | 0 / 0,00 | 0 / 0,00 | +0,00 | aucun | continu dès t60 ; opération t67 | 0 ticks |

## Trois phrases pour le jury

Sur ces deux journées simulées, une coupure de 1/6 de minute immobilise 61 512 606,90 à 96 079 037,79 € de volume brut d’exécutions du témoin, avec un écart de P&L de −469 923,40 à +9 085,97 € ; avec le relais attesté, le volume du témoin perdu est de 0,00 €.

Sur ces deux journées simulées, une coupure de 1/2 minute immobilise 61 512 606,90 à 96 079 037,79 € de volume brut d’exécutions du témoin, avec un écart de P&L de −469 923,40 à +9 085,97 € ; avec le relais attesté, le volume du témoin perdu est de 0,00 €.

Sur ces deux journées simulées, une coupure de 1 minute immobilise 61 512 606,90 à 96 079 037,79 € de volume brut d’exécutions du témoin, avec un écart de P&L de −469 923,40 à +9 085,97 € ; avec le relais attesté, le volume du témoin perdu est de 0,00 €.


## Lecture et limites

Le montant immobilisé est un volume brut d’ordres/virements, pas une perte financière ni un chiffre d’affaires. Δ P&L = résultat du cas − résultat du témoin avec Krys sans panne ; une valeur positive est un gain observé, une valeur négative une dégradation. Les deux graines ne constituent pas une estimation statistique ni une garantie.
« Non proposées » compare les identifiants métier (acteur/type/tick/proposal_id ou payment_id) des propositions du témoin. La dynamique peut aussi créer de nouvelles opérations : le JSON conserve additional_executions, leurs montants et les identifiants des opérations perdues. Ne pas additionner les volumes non proposés et les exécutions perdues : ils se recouvrent. Les refus indiquent les montants approuvés par Risk réellement présentés à Executor, pas les montants initiaux avant réduction de risque. Toutes les opérations sont saines au sens « aucune attaque », pas nécessairement opportunes financièrement.
Indisponibilité = ticks où le processus actif n’offre pas de réception attestée courante sans incertitude interne ; un refus métier de Krys en fonctionnement n’est pas une panne. Le JSON distingue extra_unavailable_ticks face au témoin. ≥ indique une observation tronquée en fin de journée, jamais une reprise supposée. Le premier refus dépend de l’arrivée d’une opération, pas seulement du tick de coupure. Les agents peuvent redevenir VERT au retour des heartbeats alors que le portique reste en refus : le JSON donne first_orange_tick et first_green_after_restart_tick, distincts de la reprise réelle. La première opération après redémarrage n’est pas le temps technique de bascule : le relais peut rester disponible en continu sans qu’aucun ordre arrive immédiatement. Les événements détaillent la bascule et les PID.
Le Runtime actuel ne sauvegarde pas toute son analyse : son démarrage avec checkpoint restaure le gel et pose checkpoint_unavailable ; la commande humaine unfreeze ne lève que les gels actifs, pas cette incertitude globale. Une commande humaine signée n’est donc pas présentée comme une reprise si le code ne la permet pas.
## Mitigation mesurée

Un second processus Runtime identique observe dès le début le même flux et les demandes d’approbation, avec son propre checkpoint. Ses sorties restent privées tant qu’il est passif. À la panne du primaire, la publication bascule seulement après vérification HMAC d’un reçu qui couvre le numéro, le hash du préfixe complet de messages/approbations et le hash de la réponse courante, sans incertitude interne. Un seul Hub publie vers la banque : jamais deux émetteurs concurrents. Le primaire relancé reste non autoritaire tant que son préfixe n’est pas celui du flux complet. Aucun compteur de suspicion ni verrou n’est effacé.
Ce relais conserve tout l’état analytique vivant ; c’est plus simple que de prétendre restaurer toute l’analyse depuis un checkpoint de gel incomplet. La signature atteste réception et réponse, pas la vérité des données. Le point unique du routeur/transport n’est pas supprimé : ce banc mesure la panne d’un processus Krys seul, pas celle de l’hôte, du disque, du journal ni d’une clé commune compromise.
Le transport de laboratoire remplace Redis par des pipes privés, sans socket. Le portique est le BusGate du dépôt, avec son délai réel de 2 secondes et refus sur dépassement. Les enfants exécutent le Runtime et FileCheckpoint actuels (fsync et écriture atomique), réseau interdit par audit Python. L’ordonnancement en ticks est déterministe ; les durées de panne simulées ne sont pas des durées murales de production. Les frais bancaires, pénalités SLA, coûts humains, coûts des deux processus et frais de financement ne sont pas modélisés. Le basculement est un prototype local à éditeur unique, pas une architecture de haute disponibilité déployée.

[Données et références des témoins](../runs/cout-panne-20261024-001421-11dc876dc19e4d9186a8d2a9e7130c83/metrics.json). Les journaux, flux d’entrée, reçus signés, checkpoints, décisions du portique et trajectoires sont conservés dans chaque dossier.
