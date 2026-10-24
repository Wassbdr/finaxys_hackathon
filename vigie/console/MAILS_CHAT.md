# Boîte d’envoi et Krys dans la console

`make demo-live` ou `make demo-replay`, puis `/console` : le même journal C vérifié
et la même borne de relecture que la vue Krys. La bulle « Demandez à Krys » des autres
vues ouvre ce même chat, déplacé dans la bulle : la conversation suit d’une vue à l’autre. Une question se pose directement,
sans champ d’accès supplémentaire ; les écritures exigent une origine exacte
autorisée et un contenu JSON.
Préparer puis confirmer une commande conserve les contrôles existants. Aucune
commande humaine n’est activée par ce lot.

Le serveur autonome `python -m api.app` expose aussi ces fonctions, en lecture seule
pour les commandes humaines. Son journal par défaut est `runs_dir/<préfixe>/journal.jsonl`
(monde C si configuré). Pour un autre dossier de lancement, définir
`KRYS_CONSOLE_JOURNAL=/chemin/du/journal.jsonl`. Il affiche les origines autorisées
et protège les écritures de son API avec le garde CSRF existant.
Les instances génériques `create_app()` sans raccord Krys n’ajoutent aucune autorité.

## Brouillons locaux

Le regroupement existant `Chat.incidents()` est réutilisé : cause + cible,
opérations dédupliquées, alertes brutes et preuves. Un volume refusé ne devient
jamais une perte évitée. La comparaison aux contrôles classiques n’est pas mesurée
dans cette boîte. Les preuves et textes sont rendus avec `textContent`.

`GET /console/mails-chat/outbox` est strictement local, sans transport SMTP.
Il ne lit que `Knowledge`, projection des messages après vérification de signature
et de chaîne ; ni monde caché, ni état du portefeuille, ni question libre exportée.

## SMTP facultatif

Par défaut aucun mail ne sort. Le lanceur peut charger son environnement depuis
le fichier de secrets du serveur. Ce module ne lit jamais ce fichier.
Variables nécessaires : `KRYS_MAIL=smtp`, `KRYS_MAIL_TO` (une seule adresse,
celle d’Édouard), `KRYS_SMTP_FROM`, `KRYS_SMTP_HOST`, `KRYS_SMTP_USER`,
`KRYS_SMTP_PASSWORD`. Facultatives : `KRYS_SMTP_TLS=ssl` (port 465 par défaut),
ou `starttls` (587), `KRYS_SMTP_PORT`, `KRYS_MAIL_STATE` (par défaut
`~/.cache/krys/mail.sqlite`). TLS et authentification sont obligatoires.
Ne pas mettre de valeur réelle dans le dépôt ou une commande partagée.

Même après activation, l’envoi demeure manuel : bouton « Envoyer à l’adresse
configurée », origine autorisée et contenu JSON. `POST /console/mails-chat/send` accepte
seulement un identifiant d’incident ; le serveur recalcule le mail depuis les preuves.
Il refuse un journal incomplet. Il n’accepte ni destinataire ni contenu libre.
Le corps ne contient ni cible, ni document, ni texte d’alerte : seulement les
libellés fixes, compteurs d’observation et identifiants techniques de preuve filtrés.
L’adresse personnelle configurée n’est utilisée que dans l’enveloppe d’acheminement.
Le lien relatif `/console#krys` vise la console locale ; il suppose l’accès
au poste de démo, pas un service public déployé.

Au plus un envoi par incident et dix réservations sur une heure glissante, y compris
entre processus/redémarrages partageant le même fichier SQLite. Réservation avant
le transport : un résultat incertain ne permet aucune relance automatique. La base
ne conserve que les empreintes d’incident et dates de réservation, sans adresse,
mot de passe ou contenu. Les réservations sont conservées sans purge pour empêcher
les doublons. Ne pas supprimer ce fichier ni choisir un fichier différent pour
contourner une limite. Le regroupement cause + cible dure tout le journal ; deux
rejeux strictement identiques ont la même empreinte et restent dédupliqués.

Les essais utilisent exclusivement un transport injecté. `make test` retire
`KRYS_MAIL` et interdit le réseau. Aucun mail réel n’a été envoyé pendant ce lot.
