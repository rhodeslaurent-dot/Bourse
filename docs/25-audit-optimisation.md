# 25 — Audit de l'outil et propositions d'optimisation (25/10/2026)

> Demande de l'utilisateur : refaire le point sur le fonctionnement, les fonctionnalités et leur imbrication ; vérifier l'optimisation (fonctionnement, efficacité, couverture, crédits Anthropic, coût / valeur ajoutée) ; proposer de nouvelles fonctionnalités vers une plateforme qui **maximise le gain pour un risque limité**.
> Statut : **constat et propositions**, à valider. Version 2 du 25/10/2026 après contre-expertises (§25.6) ; plusieurs propositions **développées en autonomie** le même jour (§25.5). Les estimations de coût sont à recaler sur les coûts réels (tableau par usage de `/sante`, commande `llm-costs`).

## 25.1 Comment l'outil fonctionne aujourd'hui (chaîne de décision)

```
 SOURCES                       TRAITEMENT (Python)                         DÉCISION / SORTIES
 EODHD (13 places, EOD)  ─┐    univers (784) → indicateurs, force relative
 Yahoo (Milan, consensus) ┤    détecteurs D2 D3 D4 D7 + lettres testables ──► moteur (07:50)
 TradingView (capis,      ┤    admission au prix max, coût complet par         classement, fiche, thèse (Claude),
   consensus)             ┤      compte, taille R7, plafonds R8/R9,            carnet d'ordres, Telegram top 3
 BCE, macro, calendrier   ┤      portail R10, annonces R14, dérogation R21 ──► rapport du jour (08:45)
 Lettres (IMAP) + presse  ┤    revue des lettres (Claude) → avis par valeur ──► /lettres, préconisations
 13 flux RSS + 6 médias   ┤    classement des nouvelles (Claude / règles)  ──► catalyseurs (bloc C)
 AMF : positions courtes, ┤    publications réglementaires (règles)        ──► vigilance (bloc R), faits
   dirigeants (à brancher)┘    suivi des positions, stops, revue samedi    ──► /revue, alertes P1
                                apprentissage (issues, espérance rétrécie)  ──► /apprentissage
```

Points forts confirmés : règles de gestion en Python testées (394 tests), coût complet par compte, instantanés de décision, aucune donnée inventée, sources qualifiées et datées, vigilance médias et positions courtes, apprentissage sur données réelles.

## 25.2 Constats (ce qui n'est pas optimal)

| # | Constat | Effet | Gravité |
|---|---|---|---|
| K1 | **Tout article RSS était classé par Claude**, même sans valeur rattachée, et les épisodes des médias l'étaient **deux fois** (classement + extraction) | coût sans usage | Élevée (coût) — **corrigé (L1)** |
| K2 | Modèles **Haiku 4.5 / Sonnet 4.6** : Haiku 5.5 coûte **≈ 10 fois moins** (0,10 $ / 0,50 $ contre 1 $ / 5 $ par million de jetons), Sonnet 5.5 ≈ un tiers de moins (2 $ / 10 $ contre 3 $ / 15 $) et est plus capable | coût et qualité ; mais la génération 5 **refuse la température** → conflit avec la règle 3 | Élevée (coût), décision requise |
| K3 | Revue des lettres : poste LLM probablement le plus lourd (Sonnet, dossier complet, jusqu'à 10 000 jetons de sortie). **Correction de la contre-expertise** : la revue à la réception ne lit déjà **que les lettres nouvelles** ; le seul levier est la taille du dossier | à mesurer sur `/sante` avant toute décision | Moyenne |
| K4 | **Aucun backtest** : seuils des détecteurs, objectifs et stops fixés par le cahier des charges ; l'apprentissage attend des mois de propositions réelles pour mesurer l'espérance | le classement (espérance en R) reste « historique insuffisant » longtemps ; paramètres non calibrés | **Très élevée (valeur)** — **fait (docs/27)** |
| K5 | ~~Scan unique à 07:50~~ — **retiré** : La Réunion a 2 à 3 h d'avance sur Paris ; le scan de 07:50 tombe à 10:50 (hiver) ou 09:50 (été) heure de La Réunion, plus d'une heure avant l'ouverture : un scan du soir n'apporterait qu'une préparation à 21:30, sans gain | — | — |
| K6 | Taille par trade fixe en % du capital ; pas de pondération par **volatilité, corrélation** ou espérance mesurée | risque de portefeuille mal réparti (positions corrélées) | Moyenne |
| K7 | Sorties (objectifs, stops suiveurs) non mesurées sur l'historique : pas d'analyse **MFE / MAE** (gain et perte maximaux en cours de position) | gains laissés ou rendus | Moyenne |
| K8 | Limite au prix maximal **posée à la main** chaque matin ; pas d'alerte quand le cours entre dans la zone | opportunités manquées dans la journée | Moyenne |
| K9 | Couverture : Italie à charger, États-Unis désactivés, 22 flux à renseigner (Investir, EQS, AMF dirigeants…) | univers et signaux incomplets | Faible à moyenne |
| K10 | Coût LLM visible en total mensuel seulement ; pas de **coût par usage ni de valeur attribuée** | impossible d'arbitrer coût / valeur | Moyenne |
| K11 | Pages nombreuses (14) ; la décision du jour est éparpillée (opportunités, rapport, lettres, revue) | temps de décision | Faible — **bloc « Idées du jour » sur l'accueil** (3 achats admis, verdict du comité, radar) |
| **K12** | **Règles de risque spécifiées mais non appliquées** : coupe-circuit (`risk.circuit_breaker`), régime orange × 0,5, portail restreint × 0,5, SRD restreint × 0,5 ; la fiche affichait « taille réduite (R10) » sans réduire | tailles **doubles** de la règle en portail restreint ; aucun arrêt après une série de pertes | **Critique (risque)** — **corrigé le 25/10/2026**, choix signalés en docs/15 |
| K13 | **≈ 70 paramètres de `params.yaml` sans effet** dans le code (relevé automatique) : surtout la phase 3 (D1 intraday, quotas par mode de disponibilité) et des réglages encore inutilisés (`risk.stop.trailing_*.atr_mult`, `admission.require_*`, `srd.margin_call_delay_sessions`, `review.*`) | un paramètre modifié sans effet trompe l'utilisateur | Moyenne — liste à trier : coder, retirer ou marquer « phase 3 » |

## 25.3 Crédits Anthropic : estimation et leviers

Estimation actuelle (à recaler) : classement des nouvelles ≈ 0,5–0,6 $/j, revue des lettres ≈ 0,5–1 $/j, thèses ≈ 0,1 $/j, extraction des lettres et des médias < 0,05 $/j → **≈ 1,2 à 1,8 $ par jour**, proche du plafond de 2 $/j.

| Levier | Gain estimé | Risque | Décision |
|---|---|---|---|
| L1 — Ne classer par Claude que les articles **rattachés à une valeur** du référentiel **et la revue de presse** (source `presse` : le contexte macro a besoin de la portée et des thèmes) ; les autres par règles (gratuit). Les flux `media_conseil` ne sont **plus classés** (l'épisode est déjà lu par `media_extract`, double appel supprimé) | −80 % sur le classement (à mesurer sur `/sante`) | faible : les articles non rattachés n'alimentent ni catalyseur ni fiche ; la rubrique « à éviter » du rapport garde leur sens par règles | **fait le 25/10/2026** (`llm.news_classify_scope`) |
| L2 — Migrer vers **Haiku 5.5** (classement, extractions, comité) et **Sonnet 5.5** (revue des lettres, thèses, président du comité), après un **jeu d'évaluation** (200 articles et 20 lettres déjà classés) | −70 à −90 % sur Haiku, −33 % sur Sonnet | changement de comportement ; **température non réglable** | **ADR + reformulation de la règle 3** (déterminisme = sortie validée + cache par empreinte + garde-fou des nombres) |
| L3 — **API Batch** (−50 %) pour ce qui n'est pas urgent : revue hebdomadaire, rattrapage du soir, bilan des médias | −50 % sur ces postes | délai (jusqu'à 24 h) | à faire |
| L4 — **Cache de prompt** sur les préfixes répétés (dossier commun du comité, instructions longues) | −50 à −90 % sur l'entrée répétée | aucun | avec le comité |
| L5 — ~~Revue incrémentale~~ (déjà le cas, contre-expertise) → **réduire le dossier** de la revue à la réception (valeurs citées par la lettre + positions, au lieu de tout le contexte) | à mesurer | perte de contexte | **en attente du coût réel** (`/sante`) |
| L6 — Tableau **coût par usage et par jour**, et coût par proposition suivie / décision prise | pilotage | aucun | **fait le 25/10/2026** : tableau sur `/sante` (jour, 7 jours, mois, part du cache, $ / appel, projection) et commande `llm-costs` ; coût par décision : avec l'attribution (proposition 9) |

Avec L1 + L2 + L5, le coût actuel tomberait vers **0,3–0,5 $ par jour**, ce qui finance largement le comité (≈ 0,2 $/j) et les signaux faibles.

## 25.4 Propositions de nouvelles fonctionnalités (classées par valeur / effort)

| Rang | Proposition | Valeur attendue | Effort | Pourquoi maintenant |
|---|---|---|---|---|
| **1** | **Banc de backtest au coût complet** (walk-forward, 10 ans d'EOD déjà accessibles par EODHD) : espérance en R par détecteur, régime, horizon et compte ; calibrage des seuils, objectifs, stops et durées par ADR ; analyse MFE / MAE | **la plus forte** : passer de règles « raisonnables » à des règles **mesurées** ; nourrit le classement (espérance) dès le premier jour | moyen | sans lui, l'apprentissage attend des mois et le comité discute sans référence |
| **2** | **Comité d'investissement multi-agents** (docs/24) | moins d'erreurs de sélection, de timing et de taille ; dissidence tracée | moyen | demandé ; coût faible avec L2/L4 |
| **3** | **Agent de signaux faibles publics** (docs/26) | idées précoces, vigilance sur l'emballement | moyen | demandé |
| 4 | **Construction de portefeuille** : taille pondérée par volatilité (ATR) et corrélation, budget de risque réparti par espérance mesurée (fraction de Kelly plafonnée), limites secteur / devise / facteur | risque mieux réparti à gain égal | moyen | après le backtest (besoin de l'espérance) |
| 5 | **Optimisation des sorties** : prise de bénéfices partielle, stop suiveur par structure ou ATR, stop temporel — choisis sur le backtest | gain moyen par trade | faible à moyen | après le backtest |
| 6 | **Scan du soir (18:30)** sur la clôture du jour + **re-contrôle du matin** avec le cours temps réel ; carnet préparé la veille | meilleure préparation ; moins d'urgence | faible | quick win |
| 7 | **Alertes de zone d'entrée** en séance (cours dans la zone du prix maximal, support qui tient) au lieu d'ordres posés à l'aveugle | entrées mieux placées | moyen | utilise `position_monitor` existant |
| 8 | **Cockpit « Aujourd'hui »** : une page et un message du matin — 3 idées avec verdict du comité, positions à surveiller, ordres à poser / annuler, contexte macro | temps de décision | faible | regroupe l'existant |
| 9 | **Attribution de performance** : par source (lettre, média, détecteur), par verdict du comité, par compte ; comparaison à l'indice | savoir ce qui rapporte | faible | alimente les ADR |
| 10 | Couverture : Italie (en attente), États-Unis grandes capitalisations au CTO (change et fiscalité déjà gérés), flux restants | plus d'opportunités | faible | progressif |

## 25.5 Feuille de route et avancement (25/10/2026)

| Étape | État |
|---|---|
| L1 (classement utile seulement), L6 (coût par usage) | **fait** |
| Backtest au coût complet (proposition 1, docs/27) | **fait** — à lancer une première fois (`backtest`) |
| Coupe-circuit et réductions de taille (K12) | **fait** |
| Signaux faibles S1 (proposition 3, docs/26) | **fait** — `radar-check --views` puis mesure (`radar-measure`) |
| Comité v2 en observation (proposition 2, docs/24) | **fait** — application par ADR après ≥ 3 mois de mesure |
| Idées du jour sur l'accueil (proposition 8, partiel) | **fait** |
| ADR modèles 5.x et règle 3 (L2) | **projet d'ADR rédigé** (ADR-011), décision de l'utilisateur |
| Construction de portefeuille, sorties (MFE / MAE), alertes de zone, attribution | après le premier backtest réel |
| K13 (paramètres sans effet) | à trier |

## 25.6 Contre-expertises du 25/10/2026 (quantitative et technique) et suites

| Objection | Suite donnée |
|---|---|
| L1 aurait privé la revue de presse de sa portée et de ses thèmes, et les médias étaient classés deux fois | la source `presse` reste classée par Claude ; les flux `media_conseil` ne le sont plus (L1 corrigé) |
| L5 inexact : la revue à la réception est déjà incrémentale | L5 réécrit (taille du dossier), décision sur coût mesuré |
| Mesurer avant d'optimiser (coût réel par usage) | L6 fait en premier |
| Backtest : biais de survie, pas de fondamentaux historiques, sur-ajustement | biais affichés avec chaque résultat ; segments apprentissage / contrôle ; nombre d'essais et seuil t ≥ 3 ; aucun recalibrage sans ADR |
| Comité : agents corrélés, recalculs, pas de mesure | comité réduit à 2 appels, Python pour les chiffres, mode observation (docs/24 §24.9) |
| Pas de protection contre une série de pertes | coupe-circuit appliqué (K12) |
| Garder le SDK épinglé (version d'API ancienne) | conservé ; à revoir avec l'ADR modèles 5.x |
| Coûts : compter les jetons de réflexion | aucun appel n'active la réflexion étendue aujourd'hui ; à intégrer dans l'ADR 5.x |
