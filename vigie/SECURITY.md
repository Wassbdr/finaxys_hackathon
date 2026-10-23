# Durcissement du gel provisoire

Base d3c35f1, admission révisée après eefcfbd (voir `FAILCLOSED.md`). Le profil appris et ses références sont conservés sans réglage sur 42.

Le danger confirmé s'accumule dans une fenêtre bornée de 30 ticks : un trou court
n'efface plus les observations. Les sources communes relient les changements
d'actif/sens ; les volumes directionnels bruts évitent qu'un contre-ordre minuscule
annule la direction nette. Une cohorte changeante et un sens opposé ne passent
plus à côté d'un bail : leurs participants/sens sont ajoutés seulement après un
signal confirmé, avec preuves. Les étiquettes de source déclarées par les traders
ne sont plus une corroboration indépendante ; les alertes signées des autorités
publiques restent un témoin de corroboration. Les paiements et actifs sans signal
restent hors du gel, sauf état de sécurité devenu indéterminé.

Admission avant signature/copie/analyse : 64 Kio, 2048 nœuds, profondeur 12,
64 dépendances/références, 2048 caractères de justification. Entiers excessifs,
non-finis, cycles, surrogates Unicode, types incorrects et actifs inconnus sont
rejetés sans exception. Une entrée malformée non authentifiée ne gèle pas la
banque ; une opération non vérifiable est refusée. Une entrée malformée, même signée, ou une saturation du suivi est rejetée et
comptée par source ; elle ne constitue pas une faute interne globale.

Limites : 2048 événements et 4096 références de nouvelles, 128 identifiants par
preuve, 256 transitions, 4096 refus/conclusions conservés ; 64 traces LLM par
fenêtre pour borner les comparaisons quadratiques. Runtime admet au maximum
20 000 messages ou 16 Mio de données et 128 messages par acteur/tick. Les budgets sont réservés par source avant admission. Une saturation refuse
seulement les messages/opérations non analysables et leur dépendance publique ;
les autres chemins et les heartbeats restent disponibles. Le budget de
checkpoint est aussi réservé avant écriture (voir `FAILCLOSED.md`).

Seuls les ticks signés de clock commandent un bail après établissement de
l'horloge. Un recul ou saut reçu est rejeté et compté à sa source. Une divergence interne
de l'horloge, une référence interne absente, un bail divergent/perdu ou une panne
réelle d'écriture du checkpoint entraînent un refus conservateur.
Une copie indépendante des baux détecte leur disparition en mémoire. Les contrôles
d'état s'appliquent aussi si la règle de détection est explicitement désactivée.

Le service réel sauvegarde ses baux, preuves, horloge et historique borné avant
toute autorisation : JSON avec empreinte de profil et somme de contrôle, fichier
600, fsync puis remplacement atomique et fsync du répertoire. Le checksum détecte
la corruption accidentelle ; il ne protège pas d'un administrateur hostile.
Le chemin par défaut est runs/freeze-state-<empreinte du préfixe>.json.

Un redémarrage restaure les baux ET reste en refus : le checkpoint seul ne prouve
pas la réception continue du bus. Checkpoint absent, corrompu ou incompatible :
refus également. La reprise autonome par rejeu attesté n'est pas implémentée.
Il faut une intervention et un environnement effectivement neuf pour réamorcer :

```
python -m vigie.main launch --no-llm --fresh-freeze-state --freeze-state /chemin/neuf.json
```

L'amorçage est explicite et ne remplace JAMAIS un fichier existant. Sur une banque
qui continue à tourner, ne pas utiliser cet amorçage comme récupération : les baux
et le bus doivent d'abord être réconciliés par l'opérateur. Les Runtime sans store
restent réservés aux simulations/tests connus frais ; main/serve en production
reçoit le store. Une levée humaine normale d'un gel sain reste disponible ; elle
n'efface pas une faute globale de continuité ou de stockage.

Le lancement couche 3 impose default_on_timeout=reject. Les enveloppes RPC
illisibles/trop grandes n'émettent aucune autorisation ; leur demande expire en
refus. La configuration d'un exécuteur extérieur doit garder cette valeur.
Les horloges/HMAC, les rôles autorités et les écritures locales sont supposés
protégés ; aucune prétention de résistance à la compromission de ces autorités.
