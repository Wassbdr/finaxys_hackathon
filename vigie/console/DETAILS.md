# Onglet Détails

Ouvrir `/dashboard#details`. La page `/demo/page` donne un résumé et ce lien.
Un message peut être partagé par `/dashboard?details_message_id=<identifiant encodé>&details_mode=archive#details` ; `live` sélectionne le journal de la démo.

Tout le lot est en lecture seule : aucune route POST, aucun transport externe, aucun appel LLM. Le fournisseur du journal de la démo est celui de Krys, déjà borné au tick affiché et filtré par vérification HMAC. Sans ce fournisseur, la source « démo » indique son indisponibilité, sans basculer silencieusement sur l’archive.

Les données sous `data/details/` sont copiées sans modification des preuves mesurées. `manifest.json` donne les chemins originaux, commits et SHA-256. Les références `runs/...` des fichiers sources désignent les archives du serveur OVH ; les liens directs de l’onglet donnent les preuves embarquées. Les mesures ne sont jamais importées par le contrôleur.

Le journal embarqué contient une journée PHP2Shell (graine 42, Krys). Le bouton de vérification recalcule les 2 534 maillons depuis la genèse ; une chaîne vide ou rompue n’est pas déclarée valide. Les signatures HMAC sont présentes dans le journal brut et leur validation enregistrée est comptée, sans prétendre les revérifier avec les clés actuelles. Une chaîne intacte ne prouve pas la vérité d’une donnée et ne résiste pas à une réécriture intégrale sans ancrage externe. La provenance remonte au plus 64 messages, avec signalement des absences et de la troncature.

La courbe reprend les points existants et le comparatif A/B/C ; ses tableaux donnent les dénominateurs. Le rapport de panne porte sur 2 journées et un relais au banc, l’annexe Invariant sur 3 scénarios et une graine. Aucune valeur de seuil n’est modifiable dans cet onglet.

Validation : `make test PY=<python du venv>` interdit le réseau et désactive les LLM. Les essais `test_console_details.py` couvrent les routes, la corruption du journal, les cycles/références manquantes, les empreintes des copies et le rendu DOM avec contenus hostiles. Le faux DOM teste le code JavaScript réel ; il ne remplace pas une inspection visuelle au navigateur.
