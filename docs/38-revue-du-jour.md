# 38 — Revue du jour : la décision d'abord (lot R1)

> **Demande de l'utilisateur du 02/11/2026.** La page Opportunités était « clairement inexploitable » : trop d'informations diffuses, cinq étiquettes par valeur, aucune ligne « que faire ». À l'inverse, la revue écrite par Claude à partir des lettres (exemple fourni, D14 de docs/37) est « claire, interactive, didactique ». L'utilisateur a « moins d'infos, mais sait exactement quoi arbitrer, pourquoi, et quoi faire ou ne pas faire ».
>
> **Plan validé** (« ok ») : R1 revue du jour, puis R2 fiches simplifiées, puis R3 lecture des fiches et des gérants.

## 38.1 La page (accueil `/`)

L'ancienne page d'accueil (« Trois décisions », risque, contexte macro) reste disponible sur `/decisions`. La revue suit l'ordre de l'exemple de l'utilisateur :

1. **Verdict du jour**, en une ligne : « N achat(s) possible(s) aujourd'hui », « Aucun achat aujourd'hui » ou « Aucune idée aujourd'hui ». Il est suivi d'au plus quatre raisons courtes : coupe-circuit, portail de marché, puis nombre d'idées par décision.
2. **Ce que j'attends de toi**, par urgence :
   - urgent : position non protégée, cours d'une valeur détenue non mis à jour depuis plus de 2 séances ;
   - aujourd'hui : ordre à poser, stop à remonter, vente ou allègement ;
   - à vérifier : cours trop ancien pour décider.
3. **Marché** : les indices (séance, 5 séances) et les indicateurs macro avec leur évolution sur 5 points, le régime, le portail de marché et sa raison, le niveau de risque du jour, les annonces à 3 jours.
4. **Fiches du jour** : un bloc par valeur, la proposition la mieux classée.
   - **Une seule décision**, dans une liste fermée (`app/domain/review.py`) : ACHETER, ATTENDRE UN REPLI, À VÉRIFIER, INEXPLOITABLE, SURVEILLER, REFUS.
   - **Pourquoi** : la raison principale, en une phrase avec ses chiffres (« le dossier est trop faible au score (56 % des points, 80 % requis) »).
   - **Que faire**, en une phrase : poser l'ordre limite à X (titres, stop, validité) ; rien aujourd'hui (une limite à X n'achèterait qu'en cas de repli) ; vérifier le cours en direct ; rien.
   - La source : la lettre, son sens, les niveaux qu'elle écrit et sa citation, ou le détecteur.
   - Un petit tableau par objectif : gain brut et gain / risque brut **au cours de l'analyse** (arithmétique sur les valeurs stockées ; cours inconnu → « — »).
   - Le détail complet des contrôles reste sur la page Opportunités.
5. **Carnet d'ordres** : les limites « à poser » (ACHETER) et « prêtes pour un repli » (ATTENDRE UN REPLI), avec l'écart du cours.
6. **Portefeuille** : positions, cours et date du dernier cours (en rouge s'il est ancien), stop, verdict de la revue de portefeuille.
7. **Sources expertes** (gérants, podcasts, 10 derniers jours) :
   - un achat de gérant va en liste de surveillance : il ne publie ni stop ni objectif, donc il ne crée jamais d'entrée à lui seul (docs/23 §23.7) ;
   - « confirme la fiche du jour » quand la valeur a une fiche ;
   - une vente sur une valeur détenue invite à relire son stop.
8. **Sources** : lettres reçues, cours de clôture, heure de construction.

## 38.2 Règles de décision (`app/domain/review.py`)

Elles s'appliquent dans cet ordre :

1. **Inexploitable par construction** : le déclencheur publié par la lettre est au-dessus du prix maximal d'achat de l'outil. Aucun prix ne respecte à la fois la lettre et le calcul, et un repli ne lève pas la contradiction. C'est le cas d'Elior le 02/11/2026, vu par la revue de l'utilisateur.
2. **REFUS** : un blocage absolu (« BLOQUÉ : … ») ou une proposition NO_TRADE.
3. **À VÉRIFIER** : cours trop ancien (règle 2), jamais de fiche d'ordre.
4. **ACHETER** : BUY sans blocage ; ordre limite, ou ordre à plage pour une cassure.
5. **ATTENDRE UN REPLI** : la seule raison est « en attente d'un repli » ; c'est la règle du carnet d'ordres.
6. **SURVEILLER** : toute autre raison (score, stop trop large, pas d'objectif, résultats, détecteur en surveillance…).

Les raisons suivent un ordre fixe : blocage absolu, coupe-circuit, plafond, annonce, stop trop large, pas d'objectif, objectif trop proche, gain / risque, résultats, détecteur en surveillance, risque élevé, score, déclencheur, repli. Une raison inconnue est donnée telle qu'écrite, jamais omise.

## 38.3 Fiches de la page Opportunités (lot R2)

Chaque fiche montre d'abord :
- **une seule étiquette**, la décision de §38.2 ;
- **Pourquoi**, en une phrase chiffrée ;
- **Que faire**, en une phrase.

Tout le reste est replié derrière « analyse complète » : le niveau du verdict gradué, l'action du moteur, le statut, « à vérifier », les lettres qui confirment ou contredisent, l'historique comparable, la source, les niveaux, les prix, les objectifs et les contrôles. Les boutons de décision et le détail de l'instantané ne changent pas.

## 38.4 Envois

Le job `brief_premarket` (08:45 Paris) envoie la revue :
- sur Telegram (P3, ≤ 12 lignes : le verdict, deux raisons, trois choses à faire, quatre fiches) ;
- par e-mail : la revue d'abord, les 10 rubriques du rapport ensuite.

Le rapport en 10 rubriques reste archivé (`daily_reports`) et consultable sur `/rapport`.

## 38.5 Ce que R1 et R2 ne font pas encore

- **R3** :
  - lire les niveaux des fiches Momentum / Next Momentum (le 02/11/2026, déclencheur 1,87 / cible 2,05–2,15 / stop 1,76 pour Elior, et invalidation 93,14 pour Publicis, n'ont pas été lus) ;
  - afficher les achats des gérants même sans relevé de la semaine ;
  - vérifier pourquoi « C'est votre argent » du 30/10 n'est pas remonté.
- **Pas de rédaction par l'IA dans R1.** Le texte est déterministe. Une synthèse rédigée (règle 3 : chiffres absents des faits refusés) pourra venir ensuite si l'utilisateur la souhaite.
