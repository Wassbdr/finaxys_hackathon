# Gel provisoire de couche 3

Cette règle est PROVISOIRE et remplaçable par la politique du Dev 1. Elle ne change
ni les calculs du module herding livré, ni le confinement de services de contagion.
`Runtime` la charge par défaut depuis `freeze_profile.json` ; une configuration
`vigie.freeze_profile: null` conserve la couche 3 antérieure sans gel.

Le module ne reçoit que `VigieState`, des messages signés déjà projetés et les
identifiants des traders. Il ne possède ni store, ni objet de simulation, ni
client réseau. Les prix utilisés pour la boucle sont ceux des messages Quant,
et les ventes sont des `execution` signées : aucune vérité cachée n'est lue.

## Signal et preuves

Chaque actif est analysé séparément sur 30 ticks, au moyen de `Herding.snapshot`.
L'indice doit être strictement supérieur à sa référence propre apprise. Le
profil livré exige trois traders de même direction nette et vingt ticks de
persistance du signal. Au moins un témoin est nécessaire :

- vente exécutée, baisse observée ensuite par Quant, puis proposition de vente
  dépendant de cette mise à jour ; les quatre message_id de la séquence sont
  ceux du détecteur réflexif existant ; ce témoin ne concerne que les ventes ;
- cosinus TF-IDF moyen des justifications LLM >= 0,98, entre auteurs distincts
  alignés, rattachées à l'actif par `response.sentiment` ou sa mention explicite ;
- même référence de nouvelle observée chez au moins deux traders alignés,
  provenant d'une seule source, apparue pour la première fois dans la fenêtre,
  sans autre source pour cette référence dans les ordres du bus. Une alerte
  publique signée world/inject/secu de type market_event/source_report concernant
  l'actif est aussi traitée comme corroboration et empêche ce témoin.

Les justifications déclarées et les sources sont des observations, pas une
preuve de véracité de leur contenu. Le mot « nouveau » désigne la première vue
sur ce bus. Le prix peut baisser pour d'autres raisons que la vente : la boucle
est une séquence prouvée, sans attribution causale certaine.

## Portique et durée

Un gel retient son actif, son sens, sa cohorte d'origine et ses preuves. Le
portique ne refuse que les ordres de cette cohorte, dans ce sens, sur cet actif.
Il retrouve la proposition originale dans les messages vérifiés ; les autres
actifs, le sens opposé, les autres traders et les paiements ne sont pas refusés
par cette règle. Le contrôle de contagion antérieur reste appliqué séparément.

N = 10 ticks. Le bail est renouvelé tant que le même signal persiste, puis expire
automatiquement dix ticks après sa dernière confirmation. Un retournement ne
change pas silencieusement le sens d'un gel existant. La cohorte d'origine ne
s'élargit pas pendant son bail. Chaque début et chaque fin émet un
`Finding(kind='herding_freeze', layer=3, target=<actif>, evidence=[message_id...])` :
warning/score=indice au début, info/score=0 à la fin.

Une `human_decision` signée par `human`, action `unfreeze` ou `unblock`, target
actif ou `*`, lève le gel. Le signal doit ensuite retomber avant qu'un nouveau
cycle puisse le réarmer. Le CLI existant peut émettre cette commande :
`python -m sim.inject human unblock BOREAL`. Un message du même type signé par
un trader n'est pas une autorisation humaine.

## Apprentissage et évaluation séparés

Commande de reproduction, sans réseau :

```
python -m vigie.calibrate_freeze --report runs/gel-entrainement.json
python -m vigie.eval_freeze --out runs/gel-eval42.json
```

Les dossiers de sortie doivent exister. La première commande écrit le profil ;
la seconde le lit sans le modifier. Pour conserver celui livré lors d'un rejeu,
utiliser `--profile /chemin/autre-profil.json` pendant l'apprentissage.

Graines d'entraînement fixées : 7, 11, 19, 23, 31, 53, chacune avec clean et
rumeur_mimetisme. Référence par actif = moyenne des indices propres observables,
une valeur par tick/actif pour ne pas surpondérer les rafales de messages. Grille
fixée avant 42 : marges 0 / 0,05 / 0,15 / 0,30 / 0,40 ; minimum de traders 2 / 3 ;
persistance 1 / 3 / 5 / 10 / 20 ticks. N=10 et similarité=0,98 restent fixes.
Choix lexicographique : minimiser les débuts de gel propres, puis maximiser le
nombre de ticks gelés sur la rumeur, puis les paramètres les plus petits.
Il s'agit d'un rejeu des caractéristiques passives d'entraînement ; les effets
financiers et la rétroaction des refus sont mesurés ensuite dans le moteur.

Références apprises : ACME 0,7881773399 ; BOREAL 0,9597069597 ; CITRA 0,9029733959.
Sélection : marge 0, trois traders, persistance 20 ticks ; zéro gel sur les six
runs propres d'entraînement. 42 est explicitement interdit à l'entraînement et
utilisé seulement en évaluation, sans nouvel ajustement de paramètres.

Le rapport d'évaluation contient cinq cas, avec sans Vigie, couche 3 antérieure
sans gel et couche 3 avec gel. Les transitions détaillent actif/sens/traders,
preuve, témoin, début/fin et échéance. Un gel encore actif à la fin d'un scénario
n'a pas de fin inventée ; l'échéance restante est dans son enregistrement.
L'absence de gel sur le run propre testé ne garantit pas une absence générale de
faux positifs. Pas de checkpoint/reprise des baux entre redémarrages dans ce lot.
