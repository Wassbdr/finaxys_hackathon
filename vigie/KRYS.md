# Argus — Demandez à Krys

Depuis la racine, lancer `make demo-live`, puis ouvrir
`http://127.0.0.1:8010/demo/page`. Copier le jeton de lancement affiché dans le
terminal dans le champ masqué du panneau. Le jeton reste en mémoire dans cette
page, sans stockage persistant. L’API écoute sur 127.0.0.1 ; conserver le tunnel
SSH habituel. Une page ouverte directement en `file://` ne peut pas commander
l’API : son origine n’est pas autorisée.

Exemples : « Les alertes ? », « Quel est le R0 ? », « Qui est en quarantaine ? »,
« Pourquoi executor:42:17 ? », « Les dernières propositions ? », « Brouillon DORA ».
Les réponses sont des modèles de phrases sans LLM, sans recherche distante,
sans dépendance supplémentaire. Aucun appel Mistral n’est implémenté.

Les questions générales (« Présente Argus », « Que se passe-t-il en cas de panne ? »)
recherchent les mots-clés des sections de [KRYS_EXPLICATIONS.md](KRYS_EXPLICATIONS.md),
texte de référence approuvé et conservé tel quel. Krys rend le texte de la section
et cite son identifiant, par exemple `[argus]` ou `[panne]`. La recherche exige
au moins la moitié des mots utiles couverts par les mots-clés ; une égalité ou
une faible différence de scores entraîne un refus. Aucun texte n’est généré ni
chiffre calculé à partir de cette source. Les résultats historiques cités restent
ceux du texte, sans devenir des résultats du direct.

Les questions sur les alertes, l’état, les propositions et « Pourquoi message_id ? »
gardent la priorité et exigent leurs preuves signées ; leur absence n’est jamais
compensée par le texte général. Les demandes explicites de définition passent
par le texte (par exemple « Définition d’un tick »). Sans correspondance suffisante,
le chat répond « je n'ai pas de source pour répondre à cela ». Les garde-fous
d’injection et d’exfiltration s’appliquent avant toute recherche.

Pour autoriser l’opérateur local à envoyer les commandes existantes :

```sh
.venv/bin/python -m vigie.demo_live live --human-commands
```

Cette autorisation concerne le détenteur du jeton éphémère de lancement ; ce
n’est pas un système de comptes individuels. Par défaut, ces routes refusent
avec 403. Le replay reste en lecture seule même avec cette option. Choisir
« Lever un gel » avec le nom de l’actif, ou « Reprendre après un rouge » avec
`*`, préparer le récapitulatif, puis cliquer sur **Confirmer**. Le nonce expire
après 120 secondes et ne sert qu’une fois. Un changement d’état invalide la
demande. Le résultat indique une publication signée, jamais une exécution
présumée. Le contrôleur peut maintenir un refus lié à une autre cause.

Le chat ne signe rien. « Je confirme » dans une question ne fait rien. La sortie
de quarantaine d’un agent n’existe pas dans le contrôleur actuel : Krys la
refuse, sans introduire une commande fictive. Les quarantaines affichées sont
celles des dépendances de sources suspectes.

La connaissance lit exclusivement `C/journal.jsonl`, vérifie la chaîne et chaque
HMAC, puis projette les champs publics utiles. Les instantanés `krys_status`
sont produits et signés par le contrôleur à partir de ses propres observations.
Les sources API du simulateur ne sont jamais transmises au moteur de chat.
Le tick d’affichage borne la lecture en replay ; il ne fournit aucun chiffre.
Les limites de 20 000 messages et 16 Mio de contenu projeté rendent toute réponse
partielle explicite et interdisent alors les commandes humaines.

Les notifications regroupent les constats par cause et cible ; une opération
refusée est attribuée une seule fois à une cause. Les montants viennent des
messages d’exécution ou de leur parent Risk signé. Un montant inconnu reste
inconnu. Les cinq incidents avec le plus de refus sont présentés en premier.
Ce regroupement déterministe reste sommaire : il ne démontre pas une cause
racine unique. Le volume refusé ne mesure pas les pertes évitées. Le brouillon
DORA ne qualifie ni conformité, ni échéance, ni impact client : ces champs
restent à valider et compléter par un humain.

Toutes les écritures réutilisent le garde CSRF de la démo (origine exacte,
jeton éphémère, JSON). Les commandes ont un schéma fermé distinct des questions.
Les journaux du chat sont limités à 128 catégories et nombres de sources en
mémoire : aucun texte, nom, adresse IP, jeton ou empreinte de question.
Le DOM du panneau emploie `textContent` et ne transforme pas les preuves en
liens arbitraires. Les gardes d’injection sont heuristiques et peuvent refuser
des questions contenant une attaque citée ; ce n’est pas un classifieur.

Le panneau est indépendant du flux et du tableau de la démo : une indisponibilité
du chatbot ne coupe pas les trois banques. Aucun tableau comparatif ni seuil
du portique n’est modifié.
