# KRYS : proposition de couche 3 (Dev 2)

Base : `origin/banque-horizon`, `ed18463`. Aucun ancien contrôleur KRYS n'est requis.

Dev 2 fournit `contagion.py`, `herding.py`, `analytics.py` et `eval.py`. `core.py` propose l'interface commune
`VigieState` et `Finding(tick, layer, kind, target, severity, evidence, score)`.
Les couches 1 et 3 produisent ces constats ; le Dev 1 possède les métriques système,
le graphe, les références/z-scores, les scores, les seuils, les feux, la politique du
portique et le journal des décisions. Aucun de ces contrôles n'est repris ici.
L'interface de phase 0 doit encore être figée ensemble.

## Exécuter

- `make couche3 NO_LLM=1` génère `runs/couche3-config.yaml`, puis un
  `Procfile.generated` dont le processus `vigie` remplace `vigie_stub`. Ce profil
  impose `gate.impl: bus`, `vigie.stub: false`, `chaos.announce_compromised: false`.
  Les modes down/stale/slow restent visibles. Le profil historique et ses essais
  gardent leurs valeurs par défaut ; `make run` reste le lancement de l'équipe.
- `make test` exécute toute la suite avec un audit Python interdisant le réseau.
- `make eval-couche3` joue php2shell_day, ledger_legacy, rumeur_mimetisme,
  trader_corrompu_49m et un run propre, sans
  Redis serveur ni LLM. `python -m vigie.eval --out rapport.json` sauvegarde les mesures.
  Les cinq cas proposés sont `php2shell_day`, `ledger_legacy`,
  `rumeur_mimetisme`, `trader_corrompu_49m` et `clean`.
  `--seed 42` fixe la même graine pour les colonnes `sans_vigie` et `avec_couche3`.

`main.Runtime` reçoit les messages signés et `/meta`. La couche 3 n'a ni client
Redis, ni objet World. Le processus ne consulte aucune clé de simulation. Les
champs d'oracle ajoutés aux exécutions sont exclus de `VigieState`. Le processus
émet un heartbeat signé chaque tick et des alertes de constats. Le portique conserve le refus des opérations marquées, avec les message_id en
preuve. Une règle provisoire de gel herding ciblé est ajoutée
([FREEZE.md](FREEZE.md)). Une signature de requête invalide est rejetée avant
l’analyse.

Le module herding livré est intégré par défaut : `Runtime.analytics` reçoit les
messages signés, `Analytics` observe le bus et ses `Herding.findings(tick)` sont
émis à l'ouverture de chaque tick avec la classe commune `vigie.core.Finding`,
`layer=3`. La logique de calcul et ses essais sont ceux du patch livré ; seule
la dataclass locale a été remplacée par cet import commun. L'estimation R0 du
module analytics est conservée dans `contagion_r0` dans l'évaluation. La version
récente du lot a retiré `pdg.py` ; aucun ancien module PDG n'est réintroduit.

Herding mesure l'alignement, le nombre effectif d'agents, la similarité des
justifications et les séquences réflexives, avec leurs preuves. Les traces LLM
sont observées si elles existent ; aucun LLM n'est appelé pour ces calculs.
Sans référence propre explicite, `herding_excess` reste inconnu. Les mesures
herding restent inchangées. Une règle PROVISOIRE de couche 3 gèle
les seuls ordres alignés sur un actif lorsque le signal dépasse sa référence
apprise et possède un témoin. Le Dev 1 peut remplacer cette règle par sa politique
([FREEZE.md](FREEZE.md)). D'autres producteurs `on_message(msg) -> list[Finding]` restent
injectables via `runtime.layers`.

## Ce qui est mesuré et ses limites

Une CVE signée de `secu` est croisée avec la langue/version de `/meta`, sans croire
la liste des services affectés de l'alerte. C'est une suspicion de vulnérabilité,
pas une preuve d'attaque : elle peut précéder l'attaque et bloquer un usage sain.
Un prix sauté (>20 %), figé sur sept observations ou une dérive monotone forte
(six rendements de même signe >0,5 %, variation cumulée >8 %) produit aussi un
constat. Ces critères locaux sont des hypothèses de détection explicites, pas
les seuils de score/feu de la couche 2.

Le grand livre fantôme commence avec le capital et les positions d'ouverture
configurés. Il intègre seulement `executor/execution` signé, dédupliqué par
exec_id, avec `qty * fill_price` ou le montant du paiement. La trésorerie annoncée
par le heartbeat signé de Risk est confrontée aux soldes démontrés sur ce tick et
le précédent (tolérance de 0,05 € pour les arrondis). Aucune lecture `/balances`,
`/state`, `world:*`, `tainted` ou des modes de services. Une perte d'exécution sur
le bus peut donc créer une suspicion erronée ; un redémarrage en milieu de run
sans rejouer le journal n'est pas pris en charge.

Les ancêtres sont parcourus à la demande : une suspicion marque rétroactivement
les messages déjà vus, y compris un enfant arrivé avant son parent. Les cycles
sont bornés par les identifiants visités. Une dépendance manquante n'est pas une
contamination inventée : son traitement relève du Dev 1. Aucune réhabilitation
automatique d'un service n'est proposée.

R0 = agents émetteurs distincts marqués / messages racines marqués (sans parent
marqué). Multiplicateur = montant des propositions marquées / montant des
propositions directement exposées ; `null` si ce dénominateur est nul. Un ordre
et ses copies de conformité/risque/exécution ne sont pas additionnés. Ces mesures
sont descriptives, pas des taux épidémiologiques calibrés. Les temps de maîtrise
sont les ticks CVE, découverte d'un rayon observé et premier refus ciblé ; un
`null` signifie qu'une étape n'est pas survenue. Le rayon peut évoluer ensuite.

L'évaluation seule consulte l'oracle pour noter les messages. Elle publie le
marquage au moment de réception ET le marquage rétrospectif final, avec TP/FP/FN,
précision et rappel (`null` si le dénominateur est nul). Un message est réellement
piégé si sa provenance est étiquetée par le moteur ou s'il dépend d'un tel
message. Les pertes évitées sont le delta de P&L net final entre les runs A/B ;
elles incluent les changements de trajectoire du marché causés par les ordres,
et peuvent être négatives. Les deux bras A/B reçoivent la même graine et les
mêmes injections, pas une vérité cachée dans le détecteur.

L’évaluation livre les cinq cas demandés, avec et sans couche 3.
Le gel herding exige une anomalie persistante et une preuve supplémentaire.
News-wire peut rester non détecté dans php2shell_day. Aucun contrôle d’identité
ou de règle financière des anciennes couches 1/2 n’est ajouté. Le service `/meta` indisponible au démarrage ne bénéficie pas
du croisement CVE ; le processus l'annonce, sans inventer sa version. Le runtime
n'a pas été déployé ; seul son transport simulé, à client strict, est éprouvé.


Le scénario Trader-3 contient trois injections de 4,9 M€ aux ticks 3/4/5.
`eval.schedule_evaluation` adapte la commande de scénario `corrupt-order` sans
modifier le moteur de l'équipe : signature Trader-3, nonce distinct, dépendance
et prix du dernier message Quant observé. Aucun ancien contrôleur n'est importé.
Les colonnes `injected_orders`, `injected_executions` et
`injected_blocked_operations` permettent de vérifier ce qui arrive à ces ordres.
Ces étiquettes d'évaluation ne sont jamais données au détecteur. L'observateur
n'étiquette pas ces ordres comme des données de service piégées : un marquage
contamination nul ne signifie donc pas que le trader corrompu est reconnu.
La description historique du scénario évoque orange/quarantaine ; la couche 3
seule ne reprend aucune des anciennes politiques de couches 1/2.

## Démo triple en direct (sans page)

`make demo-live SCENARIO=vigie/scenarios/jour_noir.yaml` expose l'API locale
sur `127.0.0.1:8010` : A « Sans Krys », B « Contrôles classiques », C « Krys » (Krys, couche 3 avec gel livré).
La page est fournie par le Dev 1. Le contrat exact, les exemples et le replay
sont dans [DEMO_API.md](DEMO_API.md). `make demo-measure` rejoue les trois
variantes hors ligne sur les graines 42 et 43. Aucune règle de B n'est affaiblie.
Le lanceur classique exclut le processus couche 3 ajouté au Procfile de cette
base ; les contrôles classiques restent identiques au patch original.

Dans ce scénario, C évite toute fraude mais ne reprend aucune opération après
la suspicion du ledger : sa dépendance est commune à toutes les décisions Risk.
Un feu VERT et des heartbeats actifs ne prouvent pas une continuité métier.
