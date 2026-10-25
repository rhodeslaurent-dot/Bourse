# 24 — Comité d'investissement multi-agents

> Demande de l'utilisateur (25/10/2026) : faire challenger les étapes majeures de la sélection — analyse des informations d'entrée, sélection des opportunités, timing et volume — par des agents spécialisés.
> **Version 2 (25/10/2026)**, après deux contre-expertises indépendantes (quantitative et technique, §24.9). **Développée en mode observation** : les verdicts sont enregistrés et mesurés, **jamais appliqués** ; leur application passera par un ADR après au moins 3 mois de mesure. À valider.

## 24.1 Principe : le comité discute, Python décide des chiffres, vous décidez de l'ordre

- **Règle 3** : les modèles ne reçoivent que des **faits numérotés calculés en Python** (ceux de la thèse, docs/22 §22.8) et des **taux de base** (backtest docs/27, historique réel docs/18). Aucun champ numérique dans leurs sorties : uniquement des **listes fermées** (verdict, confiance, vraisemblance, gravité). Tout texte contenant un nombre absent des faits est écarté (garde-fou de la thèse).
- **Borné** : le verdict ne peut que **maintenir, réduire, différer ou rejeter** — jamais augmenter une taille, élargir un stop ou lever un blocage. L'effet est calculé par Python (`bounded_effect`), jamais par le modèle.
- **Règle 10** : le dossier transmis, le pré-mortem, le verdict, les éléments écartés et le coût sont stockés (`committee_runs`).
- **Règle 1** : aucun ordre ; le verdict est une aide.

## 24.2 Pourquoi 2 appels et pas 8 agents (arbitrage de la contre-expertise)

La version 1 proposait 8 rôles et 3 tours (≈ 15 appels par proposition). La contre-expertise a objecté : (1) des agents nourris du **même dossier** produisent des avis **corrélés** — la « diversité » est illusoire et l'unanimité ne prouve rien ; (2) les rôles « technique », « risque » et « fiscal » **recalculent** ce que Python fait déjà, mieux et de façon testée ; (3) l'agent « fondamental » n'a **pas d'historique de fondamentaux** pour être jugé ; (4) le coût et la latence croissent sans gain mesuré. Décision du comité d'arbitrage (25/10/2026) : **commencer petit et mesurer**.

| Étape | Qui | Rôle |
|---|---|---|
| Contrôle des données (ancien A1) | **Python** | fraîcheur, statuts, écarts de sources déjà portés par la proposition (`à vérifier`, `BLOQUÉ`) |
| Taux de base | **Python** | espérance du détecteur au backtest (n, R moyen, gagnants, intervalle) et historique réel comparable |
| **Pré-mortem** (avocat du diable) | appel 1 | « le trade a échoué : pourquoi ? » — 2 à 4 causes reliées à des faits, vraisemblance et gravité en classes, causes **hors stop** signalées, signal contraire le plus fort, donnée manquante décisive |
| **Président** | appel 2 | verdict borné dans une liste fermée, confiance du comité (séparée de l'action), motifs reliés aux faits, dissidence retenue ou écartée, condition de révision |
| Effet | **Python** | ce que le verdict changerait (titres × 0,75 ou × 0,5 arrondis à l'inférieur, BUY → WATCH, NO_TRADE) |

## 24.3 Qui passe en comité, quand

- Job `committee_review` (08:10, jours de marché), après le moteur (07:50) : les `committee.top_n` (5) premiers **BUY** du classement et **toute proposition éligible à une dérogation** R21 (les cas marginaux, là où un second regard vaut le plus).
- **Idempotence** : une délibération par (proposition, empreinte du dossier et des versions de prompts) ; un comité non réuni (budget, IA indisponible) est retenté au passage suivant.
- **Société masquée** : nom, ISIN et ticker sont remplacés par « la société » pour que les modèles jugent les faits, pas un souvenir de l'entreprise (biais de notoriété et de connaissance a posteriori).

## 24.4 Garde-fous

| Garde-fou | Effet |
|---|---|
| Élément sans identifiant de fait valide, ou avec un nombre absent des faits | écarté, listé dans « éléments écartés » |
| **Quorum** : aucune cause valide au pré-mortem | pas de verdict (« comité sans quorum ») — jamais un « maintenir » par défaut |
| Verdict sans motif valide | neutralisé en « maintenir, confiance faible » et signalé |
| **Budget réservé avant l'appel** : coût estimé des 2 appels + dépense du jour du comité > `committee.daily_budget_usd` | « comité non réuni (budget) », aucun appel |
| Mode | `observation` imposé par le code, quoi qu'indique `params.yaml` |

## 24.5 Mesure (avant toute application)

`measure` regroupe les **issues définitives** des propositions (`signal_outcomes`, R net) par verdict et par (verdict, confiance) : moyenne, médiane, part de gagnants, t, intervalle par rééchantillonnage. Parce que les verdicts **n'ont rien changé** et que les issues sont simulées pour toutes les propositions, la comparaison est propre.

**Puissance (contre-expertise du 25/10/2026)** : à 5 délibérations par jour, 3 mois donnent ≈ 250 issues, ≈ 80 à 100 cas indépendants ; l'écart minimal détectable entre « réduire / rejeter » et « maintenir » est ≈ 0,75 R, alors qu'un écart réaliste est ≈ 0,3 R (≈ 170 cas minoritaires nécessaires). **Le comité ne pourra pas conclure en 3 mois sauf effet énorme.** Protocole retenu :
- une délibération **par montage** (valeur + détecteur) sur 7 jours (fait) ;
- critère principal **ordinal** : corrélation de rang entre le degré de prudence du verdict et le R net, sur tous les cas ; critères secondaires : stop touché (oui / non), MAE en R ;
- l'ADR à 3 mois décide seulement d'**arrêter ou de prolonger** ; extension possible aux WATCH avec un modèle moins cher pour gagner en nombre de cas (après l'ADR-011).

## 24.6 Coût

2 appels au `llm.write_model` par délibération (≈ 6 000 jetons en entrée et 900 en sortie chacun, réservés avant l'appel) : ≈ 0,06 $ par délibération avec Sonnet 4.6 (3 $ / 15 $ par million de jetons), ≈ 0,30 $ par jour pour 5 propositions ; plafond `committee.daily_budget_usd` (0,50 $). Le passage aux modèles 5.x (docs/25 L2, ADR à décider) diviserait ce coût.

## 24.7 Interface

Fiche d'opportunité : section **« Comité d'investissement — en observation »** — verdict, confiance, effet s'il était appliqué, motifs, pré-mortem (causes hors stop marquées), dissidence, condition de révision, modèles, coût, éléments écartés.

## 24.8 Critères d'acceptation

- **A24.1** Aucun verdict ne peut augmenter une taille, élargir un stop ou lever un blocage ; en observation, la proposition reste inchangée (tests).
- **A24.2** Un élément contenant un chiffre absent du dossier est écarté (test).
- **A24.3** Dossier, pré-mortem, verdict et éléments écartés stockés (`committee_runs`, migration 0020).
- **A24.4** Budget insuffisant → « comité non réuni (budget) » sans appel (test).
- **A24.5** Sans cause valide → pas de verdict (quorum, test).
- **A24.6** La société est masquée dans le texte envoyé (test).
- **A24.7** `measure` donne l'issue moyenne par verdict (test) ; tableau sur `/apprentissage` : lot suivant.

## 24.9 Contre-expertises du 25/10/2026 et suites données

| Objection | Suite |
|---|---|
| Agents corrélés sur un même dossier : l'unanimité ne vaut rien | 2 appels de rôles opposés (pré-mortem puis arbitrage) ; aucune règle d'unanimité |
| Les agents recalculent ce que Python fait | technique, risque, fiscal, données : Python ; les modèles ne voient que des faits |
| Pas d'historique de fondamentaux pour juger un agent « fondamental » | rôle supprimé |
| Appliquer des verdicts non mesurés est un risque | mode observation ≥ 3 mois, puis ADR |
| Champs numériques (probabilités) = le LLM calcule | classes fermées seulement |
| Ancrage sur la notoriété de la société | société masquée |
| Coût et dérive du budget | réservation avant l'appel, plafond dédié, idempotence |
| Cibler là où un second regard vaut le plus | top 5 BUY + cas éligibles à dérogation R21 |
| Calibrage (Brier) par agent | remplacé par la mesure par verdict (§24.5), plus robuste avec peu de cas |

## 24.10 Suites possibles (après mesure)

Tableau de mesure sur `/apprentissage` ; ligne du verdict dans le message Telegram ; troisième appel « analyste du contexte » (macro, secteur, signaux faibles docs/26) si la mesure montre que le pré-mortem rate surtout des causes de contexte ; E4 « portefeuille du samedi ».
