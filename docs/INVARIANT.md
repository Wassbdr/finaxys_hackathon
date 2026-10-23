# Comparateur facultatif E : Invariant Guardrails

L'annexe [ANNEXE_INVARIANT.md](../vigie/ANNEXE_INVARIANT.md) compare seulement
la couche 3 et Invariant sur trois scénarios, graine 42. Le tableau principal
et la démonstration restent à trois banques.

## Installation isolée

Dans un worktree dédié, `uv venv .venv`, puis :

```sh
uv pip install --python .venv/bin/python -r requirements-comparateurs.txt
# Pour reproduire exactement les versions utilisées dans notre évaluation :
uv pip install --python .venv/bin/python -r requirements-invariant.lock
```

`requirements.txt` de l'équipe reste inchangé. Invariant est épinglé au commit
`2340fe2d9cd619f73d5b67fa05bf8a08c7cad515` (distribution 0.3.5).
Licence et attribution : [third_party/invariant](../third_party/invariant/README.md).
Aucun fichier amont modifié. Les dépendances OpenAI présentes dans la distribution
ne sont pas appelées par nos règles ; aucun modèle, détecteur distant ou LLM n'est utilisé.

## Processus E indépendant

```sh
make invariant PY=.venv/bin/python
```

Ce lanceur prépare sa propre configuration et son Procfile dans un nouveau dossier
`runs/invariant-launch-.../`, sans écraser les configurations existantes. Il lance les
services et agents habituels avec `vigie.enabled=true`, `gate.impl=bus`, défaut refus,
heartbeat signé et le seul processus `vigie.invariant_guard` pour le portique.
Redis doit déjà être disponible, comme pour le lanceur de l'équipe. Pour une banque
E parallèle, configurer un préfixe Redis et des ports propres avant de lancer.
Aucun fichier de la démonstration ou de sa page n'est modifié.

Pour brancher seulement le contrôleur à une banque existante :

```sh
python -m vigie.invariant_guard --config runs/E/config.json --meta runs/E/meta.json
```

La configuration est publique, les métadonnées sont des copies de `/meta` indexées
par service. Sans `--meta`, seuls les endpoints `/meta` sont interrogés. Le contrôleur
n'utilise ensuite que les messages signés du bus et les demandes de portique. Son
accès Redis se limite à pub/sub et aux marqueurs de présence du processus.

L'API explicite `LocalPolicy` évalue [bank.ipl](../vigie/invariant_rules/bank.ipl).
L'alias amont `Policy` peut choisir un service distant : il n'est jamais utilisé.
Un audit hook interdit les connexions et résolutions pendant l'analyse Invariant,
sans empêcher le transport Redis du contrôleur. Une erreur moteur refuse l'opération
et apparaît dans les diagnostics ; l'arbitre interrompt l'évaluation si elle survient.

## Mesure hors ligne

```sh
make compare-invariant PY=.venv/bin/python RUN=runs/invariant-annexe-neuve
```

Le dossier doit être neuf. La commande impose le garde réseau de la suite, exécute
les deux contrôleurs avec le même adaptateur Lockstep et les mêmes graines/scénarios,
vérifie les journaux et écrit `metrics.json`, `comparison.md`, et les traces réelles
`invariant-traces.json` sous chaque run E. Les latences mesurées excluent le transport
Redis : elles ne sont pas un benchmark de service réseau. Le protocole bus et le
lanceur du processus indépendant ont leurs propres essais hors réseau.

## Interprétation et limites

Une opération est traduite en trace : lectures de services directement indiquées
par sa provenance signée, puis appel `execute_order` ou `execute_payment`. Les textes
externes disponibles dans `llm_trace` sont associés par service/hash de réponse.
Aucune réponse manquante n'est inventée. Une lecture sans corps conserve seulement
ses métadonnées de provenance ; elles suffisent à la règle de version vulnérable.

Le contrôleur authentifie les visas et les identités, puis le moteur applique les
flux CVE/instructions, plafonds, bénéficiaires, IBAN, cumul journalier réservé et perte
déclarée par Risk. Les réservations et le constat de reçu incohérent restent en mémoire.
Il n'a ni persistance de ces réservations au redémarrage, ni grand livre fantôme,
ni modèle de mimétisme, ni fermeture transitive supplémentaire entre agents.

Cela décrit cet intégrateur, pas une impossibilité de la bibliothèque : une application
peut fournir une trace plus riche, écrire d'autres règles ou ajouter son propre état.
L'opérateur `->` amont construit l'ordre des événements au sein des listes de trace ;
il ne prouve pas une causalité à lui seul. La provenance héritée permet néanmoins de
voir des dépendances indirectes déjà copiées dans une décision par ses producteurs.

Les seuils sont ceux de la configuration de l'équipe ; ils ne sont pas ajustés sur
les résultats de la graine 42. Une CVE publique rend une version suspecte sans prouver
une exploitation : refuser ces flux peut aussi bloquer des opérations saines. Le run
propre et les volumes d'exécution sont conservés dans les preuves pour le montrer.
