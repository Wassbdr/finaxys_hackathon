# Argus — Demandez à Krys

Depuis la racine, lancer `make demo-live`, puis ouvrir
`http://127.0.0.1:8010/console` : Krys en est la vue par défaut. Poser directement une question,
sans champ d’accès ni secret de lancement à saisir. La bulle de la console et
le brouillon DORA utilisent le même accès local. L’API écoute sur 127.0.0.1 ; conserver le tunnel
SSH habituel. Une page ouverte directement en `file://` ne peut pas commander
l’API : son origine n’est pas autorisée.

Exemples : « Les alertes ? », « Quel est le R0 ? », « Qui est en quarantaine ? »,
« Pourquoi executor:42:17 ? », « Les dernières propositions ? », « Brouillon DORA ».
Les réponses sont par défaut des modèles de phrases sans LLM, sans recherche
distante ni dépendance supplémentaire. Une reformulation Mistral est facultative.

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

## Reformulation Mistral facultative

Activation au lancement uniquement si **KRYS_LLM=mistral et MISTRAL_API_KEY non
vide** sont présents. La clé n’est jamais demandée dans le navigateur. La commande
suivante charge les secrets sur le serveur et lance la démo ; ne pas activer la
trace shell (`set -x`) et ne pas afficher l’environnement :

```sh
set -a
. ~/.config/secrets.env
set +a
KRYS_LLM=mistral make demo-live
```

Pour rester sans LLM : `KRYS_LLM= make demo-live`. Le drapeau HORIZON_NO_LLM de
la démo continue de désactiver les modèles des agents bancaires ; KRYS_LLM ne
concerne que la reformulation du chat. Sans les deux variables, aucun client
Mistral n’est créé. Les tests vident ces variables et utilisent des faux clients.

Le modèle est épinglé dans `krys_mistral.json` : **Mistral Small 4, version 26.03,
identifiant API mistral-small-2603**, sans alias latest ni remplacement automatique.
Le client appelle seulement chat/completions, sans outils, redirection, proxy
implicite, streaming de texte à l’écran ou retry. L’attente totale est bornée à
6 secondes ; en cas d’échec, de saturation ou de rejet, la réponse locale est
rendue intacte. Le panneau indique une reformulation uniquement lorsqu’elle a
passé les contrôles.

Ce qui part chez Mistral : seulement le texte factuel déjà construit par Krys
à partir d’une section approuvée ou des observations simulées du journal signé,
et les identifiants de sources fournis. **Ni question brute, ni journal complet,
ni prompt des agents, ni signature HMAC, ni donnée personnelle.** Les textes trop
longs ou contenant des formats personnels (courriel, IBAN, téléphone, adresse,
nom composé) restent locaux. Ce refus est conservateur : des noms de sociétés
composés peuvent aussi empêcher une reformulation.

Le prompt interdit d’obéir aux instructions de la question ou des données. Les
commandes humaines, refus de sécurité et réponses sans source ne passent jamais
par le modèle. Avant affichage, il faut au moins une citation fournie ; nombres
absents des faits, identifiants inconnus, consignes, formats personnels et HTML
entraînent le repli. Ces contrôles lexicaux ne prouvent pas une équivalence
sémantique complète : les sources restent affichées pour vérification humaine.
Aucun texte tiers ni détail d’erreur n’est journalisé.

Ordre de grandeur indicatif, tarifs publics consultés le 24/10/2026 : 0,15 USD
par million de jetons d’entrée et 0,60 USD par million en sortie. Une question
avec 1 000 jetons d’entrée et 300 en sortie représente environ **0,00033 USD**
(0,033 cent américain), hors taxes et conditions du compte. Ce calcul est une
estimation, aucun appel facturé n’a été effectué pour ce lot. Le contexte est
borné à 12 000 caractères, la sortie demandée à 768 jetons ; le nombre réel de
jetons dépend du texte.
Sources : [modèle et version](https://docs.mistral.ai/models/mistral-small-4-0-26-03),
[tarifs](https://docs.mistral.ai/inference/pricing),
[API chat](https://docs.mistral.ai/api/endpoint/chat).

Pour autoriser l’opérateur local à envoyer les commandes existantes :

```sh
.venv/bin/python -m vigie.demo_live live --human-commands
```

Cette autorisation est une option explicite du lanceur local ; ce n’est pas
un système de comptes individuels. L’origine exacte de la page et le contenu
JSON restent contrôlés pour chaque écriture. Par défaut, ces routes refusent
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
contenu `application/json` obligatoire), y compris les questions du chat.
Les commandes ont un schéma fermé distinct des questions.
Les journaux du chat sont limités à 128 catégories et nombres de sources en
mémoire : aucun texte, nom, adresse IP ou empreinte de question.
Le DOM du panneau emploie `textContent` et ne transforme pas les preuves en
liens arbitraires. Les gardes d’injection sont heuristiques et peuvent refuser
des questions contenant une attaque citée ; ce n’est pas un classifieur.

Le panneau est indépendant du flux et du tableau de la démo : une indisponibilité
du chatbot ne coupe pas les trois banques. Aucun tableau comparatif ni seuil
du portique n’est modifié.

Le CORS reste limité aux origines configurées. Une origine étrangère ou absente
renvoie 403 ; un contenu non JSON depuis une origine autorisée renvoie 415.
L’envoi SMTP réel reste désactivé par défaut, indépendamment de la disponibilité
du chat et des brouillons.
