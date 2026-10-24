# Argus — onglet Conformité

Base `f9c9d9107715e4803786fc59e835cf559de4306c`. Accès : `/dashboard#conformite` ;
résumé repliable dans `/demo/page`. Le module possède ses fichiers et ne modifie
pas `dashboard.js`. Trois lignes raccordent l’onglet à `dashboard.html`, deux à
la démo ; trois lignes raccordent les ressources publiques à `api/app.py`.

Les états décrivent une **couverture technique dans la démonstration**, jamais
un agrément réglementaire. Les dix éléments gardent leurs limites, leur comparaison
avec les contrôles classiques et un lien vers une pièce publique. Les badges des
actions, alertes et résumés sont des repères de lecture ; ils ne qualifient ni un
incident majeur ni l’applicabilité d’un règlement. Le lexique est lisible aussi
sur téléphone, sans dépendre exclusivement du survol.

Le bouton « Préparer la notification DORA » appelle seulement `/demo/krys/ask`
avec la question constante `Brouillon DORA`. Il exige le jeton de lancement et
réutilise le garde CSRF de la démo. Les preuves sont celles du journal signé lu
par le chatbot ; les mesures affichées ne lui sont pas transmises. Sans API de
démo, jeton ou preuve, le refus reste explicite. Aucun dépôt auprès d’une autorité,
aucune commande humaine, aucun envoi de mail n’est ajouté.

## Preuves et mesures

`data/conformite-fixed-comparison.json` est la copie exacte du fichier
`fixed-comparison.json` du run `remesure-fusion-cf4973a-surete-v2`, dans le worktree
`remesure-fusion`. Son empreinte, le commit mesuré `cf4973a`, le commit de livraison
`48a980a`, l’échantillon (six scénarios d’attaque, dix journées chacun) et sa source
figurent dans `data/conformite.json`. Les pièces de code et le rapport de mesure
sont servis en lecture seule par une liste de chemins fermée. Aucun accès à un
état bancaire, aucune règle de décision ni valeur financière nouvelle.

La matrice relie DORA aux articles 10, 17–20 du [règlement 2022/2554](https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=CELEX:32022R2554),
avec l’article 12 du règlement délégué 2024/1774 pour la journalisation. AI Act :
articles 12, 14 et 26 du [règlement 2024/1689](https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=CELEX:32024R1689),
au conditionnel. MiFID II RTS 6 : articles 12, 15 et 16 du
[règlement 2017/589](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj?locale=fr).
Le mécanisme simulé ne démontre pas l’annulation des ordres ouverts sur des
plateformes réelles. Les contrôles classiques reproduisent les plafonds et l’arrêt.
L’assurabilité reste hypothétique, sans borne universelle ni promesse de prime.

Les recherches internes du 23/10/2026 sont les quatre `recherche-*.md` fournis par
le tech lead. Elles présentent le report à 2027 comme non confirmé au JO. La
vérification documentaire complémentaire a repéré sur EUR-Lex le
[règlement 2026/1744](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32026R1744),
qui modifie l’article 113. La console conserve cette distinction de sources et
n’utilise aucune date reportée pour décider d’un statut : qualification du système,
texte applicable et calendrier restent à valider dans le dossier de la banque.

## Vérification

`python -m vigie.testing -k conformite` : rendu DOM exécuté sous Node, contenus
hostiles, preuves et dénominateurs, brouillon authentifié et routes en liste blanche.
`make test` reste la suite complète ; aucune connexion ni appel LLM autorisé.
