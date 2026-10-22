# 22 — Revue du cahier des charges face à l'objectif final : des propositions d'investissement argumentées, multi-marchés

Statut : **proposée le 22/10/2026, à valider** (décisions ouvertes : §22.9). Demande utilisateur :

> « L'objectif final de l'outil est de me proposer des actions ou produits financiers, sur différents marchés, avec les arguments d'investissement, le cours d'entrée, le cours de sortie, le potentiel de gain, le risque, le timing… comme me le fait aujourd'hui Claude dans nos échanges du projet « Revue de portefeuille du 5 août 2026 ». Ne plus dépendre que des lettres Momentum et Zonebourse, et que l'outil identifie lui-même de nouveaux signaux et propositions. »

Ce document confronte cet objectif au cahier des charges actuel (docs/00 à 21) et au code. Il propose les ajustements. **Une fois validé**, il sera reporté dans docs/00, 04, 05, 06, 07, 09 et 15-roadmap ; les divergences sont signalées dans docs/15-status.md (règle 8). Règles absolues inchangées :

- jamais d'ordre passé par l'outil ;
- jamais de donnée inventée ;
- **le LLM rédige mais ne calcule pas** : les prix, objectifs, potentiels, risques et tailles sont calculés en Python.

## 22.1 Constat : ce que le cahier des charges couvre déjà

- **Mécanique du projet Claude reprise** (docs/00 §0.1) :
  - score sur 100 (blocs C/T/F/R/M) ;
  - BUY / WATCH / SELL / HOLD / NO TRADE ;
  - risque de 0,75 % par trade ;
  - rapport en 10 rubriques.
- **Fiche de proposition** (docs/09) :
  - entrée et prix maximal, stop, objectifs 1 et 2, horizon, taille, risque réel ;
  - comparaison des comptes ;
  - « argumentaire (3 lignes) », vigilance, invalidation, sources, expiration.
- **Détecteurs techniques** (docs/06) : D1 gap, D2 cassure, D3 expansion, D4 pullback ; D5 lettres, D6 nouvelles ; sorties S1 à S9.
- **Retour d'expérience** (docs/18) : taux de réussite des propositions comparables.

## 22.2 Écarts avec l'objectif

| # | Objectif | État actuel (spec / code) | Écart |
|---|---|---|---|
| E1 | L'outil trouve **lui-même** des idées | Spec : D1–D4 + scoring (phase 2). Code : **aucun détecteur propre**. Les seules propositions viennent des lettres et restent en WATCH ; la watchlist momentum est calculée mais ne produit pas de proposition. | **Majeur** : la phase 2 (MVP) est à faire. |
| E2 | **Plusieurs marchés**, Europe et monde | Spec : places éligibles au PEA ; US, Suisse et Royaume-Uni en « phase 6 au fil de l'eau » ; Asie absente. Code : 4 places seulement (Paris, Amsterdam, Bruxelles, Xetra ; 393 valeurs). Les capitalisations hors euro sont vides (valeurs suédoises, danoises, norvégiennes écartées sans le dire). | **Majeur.** |
| E3 | **Produits financiers** au-delà des actions | Spec et code : actions seulement, positions acheteuses seulement ; dérivés hors périmètre. Aucun ETF, ETC ou obligation. | À décider (§22.9 Q1). |
| E4 | **Moyen terme** | Le mot `medium` existe dans la nomenclature, mais aucun détecteur ne produit de proposition à moyen terme. Les règles de sortie s'arrêtent à 40 séances. | **Majeur** pour l'objectif « court et moyen terme ». |
| E5 | **Arguments d'investissement** (thèse) | Spec : argumentaire de 3 lignes. Code : rien. Le modèle `proposals` n'a pas de champ thèse, et l'assistant a interdiction de recommander. | **Majeur** : c'est la valeur ajoutée attendue « comme Claude ». |
| E6 | **Cours de sortie et potentiel de gain** | Objectifs mécaniques uniquement (2R/3R, plus haut à 20 jours ou 52 semaines). Pas de potentiel en %, pas d'objectif fondé sur la valorisation ou le consensus des analystes. | Moyen. |
| E7 | **Timing** | Validité (`expires_at`) et horizon prévus. En revanche, pas de « pourquoi maintenant », pas de catalyseurs datés par idée, pas de calendrier macro (BCE, Fed, inflation). | Moyen. |
| E8 | **Contexte macro et monde** | Seul le régime technique CAC / STOXX existe. Taux, devises, matières premières et indices US/Asie manquent ; seulement la revue de presse (docs/21) et les flux RSS français. | Moyen. |
| E9 | **Revue de portefeuille** comme le 5 août : chaque ligne passée en revue avec verdict garder / alléger / renforcer / vendre et stop et objectif mis à jour | Sorties S2 à S9 spécifiées ; code : S1 (franchissement de stop) et S7 (liquidation SRD) seulement. Pas de revue périodique ligne par ligne. | **Majeur.** |
| E10 | **Données** | EODHD « EOD + Intraday » ne fournit ni fondamentaux, ni consensus, ni calendrier de résultats. L'offre « All-in-One » (+70 $/mois) est repoussée en phase 6. | Bloquant pour E4, E6 et le bloc F du score. |

## 22.3 Fiche d'investissement cible (remplace la carte de docs/09 §9.1)

Chaque proposition, quelle que soit son origine (détecteur, lettre, revue), produit **la même fiche**.

| Rubrique | Contenu | Calculé par |
|---|---|---|
| Instrument | Nom, ISIN, type (action / ETF), place, devise, secteur, capitalisation | Référentiel |
| Verdict | ACHAT / À SURVEILLER / ALLÉGER / VENDRE / CONSERVER / PAS DE TRADE, score sur 100 avec ses blocs, confiance sur 5 | Python (docs/06 §6.6) |
| Horizon | court (≤ 6 semaines) ou moyen (1 à 6 mois), avec la famille d'origine (§22.4) | Python |
| **Thèse** | 3 à 5 arguments, **chacun relié à des faits du dossier** (identifiant de fait) : catalyseur, tendance, fondamentaux, contexte sectoriel et macro, avis des lettres | LLM (Sonnet), garde-fous §22.6 |
| **Pourquoi maintenant** | Déclencheur constaté (cassure, retour sur moyenne, publication, convergence des lettres) avec sa date | Python (fait), LLM (formulation) |
| **Entrée** | Zone d'entrée, **prix maximal**, type d'ordre conseillé (limite, plage de déclenchement), validité | Python (docs/07) |
| **Sortie** | Stop (type, niveau, niveau glissé) ; objectif 1 et objectif 2 avec leur **méthode** affichée : multiple de R, résistance, plus haut 52 semaines, **objectif moyen des analystes** (source, date, nombre d'analystes) si la donnée existe | Python ; le consensus est une donnée sourcée, jamais calculée par le LLM |
| **Potentiel / risque** | Potentiel de gain en % et en € jusqu'à l'objectif 1 et 2 depuis le prix maximal ; perte en % et en € au stop glissé ; ratio gain / risque | Python |
| **Probabilité observée** | Taux de réussite et gain moyen des propositions comparables (même famille, régime, horizon), avec effectif et rétrécissement n/(n+k) ; « historique insuffisant » sous `learning.min_samples` | Python (docs/18) |
| Taille | Nombre de titres, montant, risque réel, compte conseillé (PEA / CTO / SRD) et coût comparé | Python (docs/07, 08) |
| **Calendrier** | Prochains résultats, détachement, événements macro de l'horizon, « pas d'entrée avant publication » | Python (calendriers) |
| Vigilance et invalidation | Risques identifiés (LLM, reliés aux faits) ; condition d'invalidation chiffrée (Python) | Mixte |
| Sources | Lettres, nouvelles, articles, données avec `data_status` et horodatage de marché | Python |

Le **potentiel** n'est jamais une promesse. La fiche affiche « objectif technique » ou « objectif des analystes (source, date) » et la probabilité observée, ou « historique insuffisant ».

## 22.4 Moteur d'idées : quatre familles complémentaires

1. **A — Technique court terme** (docs/06 D1 à D4, inchangés). Appliquée à **tout l'univers**, pas seulement aux valeurs citées par les lettres. C'est le MVP (phase 2).
2. **B — Moyen terme « tendance + qualité »** (nouveau détecteur D7, hebdomadaire).
   - **Conditions** :
     - force relative à 6 et 12 mois dans le premier quintile ;
     - cours au-dessus de la moyenne 200 jours, elle-même croissante ;
     - fondamentaux : croissance du chiffre d'affaires, marge, révisions de bénéfice par action positives, dette nette sur EBITDA bornée ;
     - valorisation raisonnable face au secteur.
   - **Entrée** : sur repli vers la moyenne 50 jours ou sur cassure d'une base.
   - **Stop** : large, en multiple d'ATR ou sous la moyenne 200 jours.
   - **Objectifs** : objectif des analystes s'il existe, sinon technique.
   - **Durée maximale** : 120 séances.
   - Paramètres dans `detectors.d7_medium.*`. Le risque par trade est le même (0,75 %) : un stop plus large donne une taille plus petite.
3. **C — Rotation sectorielle et géographique** (nouveau D8, hebdomadaire).
   - **Mesure** : force relative des secteurs et des régions, via des ETF ou indices de référence.
   - **Production** : propositions d'**ETF** (PEA si éligible, sinon CTO) et une **liste de secteurs porteurs** qui ajoute des points au bloc M du score des familles A et B.
4. **D — Externe** (D5 lettres et revue de presse, docs/20 et 21).
   - Une recommandation de lettre devient un **signal** recalculé par le moteur (niveaux vérifiés, score, portes de risque). Elle n'est plus seulement une proposition WATCH recopiée.
   - La convergence de plusieurs lettres ajoute des points au bloc C. Aucune lettre seule ne suffit à un ACHAT.

Chaque famille alimente le même score, les mêmes portes de risque, la même fiche (§22.3) et les mêmes statistiques de retour d'expérience (docs/18).

## 22.5 Revue de portefeuille (le travail du 5 août, automatisé)

- **Quand** : chaque samedi matin (job `portfolio_review`), et sur demande depuis `/portefeuille` (« Revue complète »).
- **Pour chaque position** :
  - thèse d'origine, et ce qui a changé depuis (cours, nouvelles, lettres, résultats) ;
  - règles de sortie S2 à S9 évaluées en Python ;
  - verdict CONSERVER / RENFORCER / ALLÉGER / VENDRE, avec stop et objectifs mis à jour (sans jamais baisser un stop) ;
  - texte rédigé par le LLM à partir de ces seuls faits.
- **Synthèse** :
  - exposition par compte, secteur, pays et devise ;
  - risque ouvert ;
  - liquidités ;
  - **les 5 meilleures idées nouvelles** (fiches §22.3), toutes familles confondues ;
  - plan d'action de la semaine.
- **Format** : une page `/revue`, un PDF ou e-mail, et un résumé Telegram de 12 lignes au plus.

## 22.6 Rôle de Claude (LLM) : rédacteur sous contrôle

- **Entrée** : un dossier de faits numérotés construit en Python. Il comprend :
  - la fiche calculée et les indicateurs ;
  - les nouvelles, articles et avis des lettres ;
  - le contexte macro et le calendrier ;
  - l'historique comparable et les leçons retenues (docs/18).
- **Sortie** : schéma `ThesisOut` validé par Pydantic, température 0. Il contient :
  - `arguments[]` : texte d'au plus 200 caractères et identifiants de faits ;
  - `risques[]` ;
  - `pourquoi_maintenant` ;
  - `ce_qui_invaliderait`.
- **Garde-fous** :
  - tout chiffre du texte doit figurer dans le dossier (règle déjà appliquée à l'assistant) ;
  - tout identifiant de fait doit exister ;
  - un argument sans fait est écarté.
- **Assistant (docs/17)** : il reste interdit de **créer** une recommandation. Il peut **expliquer, comparer et challenger** les fiches produites par le moteur. Exemple : « Pourquoi X plutôt que Y ? » La réponse s'appuie sur les deux fiches.
- **Coût** : environ 5 à 15 fiches par semaine plus une revue hebdomadaire, soit quelques dollars par mois avec Sonnet, dans le budget de 50 $/mois.

## 22.7 Univers élargi, par étapes

| Étape | Marchés | Comptes | Prérequis |
|---|---|---|---|
| U1 | Europe P0 + P1 : Paris, Amsterdam, Bruxelles, Xetra, Milan, Madrid, Stockholm, Copenhague, Helsinki, Oslo | PEA / CTO / SRD Paris | Calendriers P1, conversion des capitalisations hors euro (taux BCE stockés), correspondance du screener |
| U2 | **États-Unis** (NYSE, Nasdaq ; capitalisation ≥ 2 Md$ et forte liquidité), Suisse, Royaume-Uni | CTO Saxo | Calendriers NYSE/SIX/LSE, change USD/CHF/GBP, fuseau New York, retenue à la source et frais de change dans le coût |
| U3 | **ETF UCITS** : indices, secteurs, régions, thèmes ; éligibles PEA ou non | PEA / CTO | Liste d'ETF (fichier de référence daté), données de cours EODHD |
| U4 (option) | Asie (Japon, Hong Kong) **via ETF** d'abord ; actions en direct plus tard si l'intérêt est confirmé | CTO | — |

Le routage de compte (docs/08) devient général : **PEA si éligible et plus favorable, sinon CTO**, SRD si les conditions sont remplies. Une action hors UE va toujours en CTO.

## 22.8 Données nécessaires

| Besoin | Source proposée | Coût | Décision |
|---|---|---|---|
| Fondamentaux, révisions, objectifs des analystes, calendrier des résultats, US | **EODHD All-in-One** (même fournisseur, même API) | +70 $/mois (99,99 $ au lieu de 29,99 $) | Q2 |
| Calendrier macro (BCE, Fed, inflation, emploi) | EODHD Economic Events (inclus dans All-in-One) ; à défaut, fichier daté maintenu à la main | inclus | Q2 |
| Contexte macro marchés (taux 10 ans, euro-dollar, pétrole, or, S&P 500, Nikkei) | EODHD fin de journée (déjà payé) : tickers d'indices, devises, matières premières | 0 | — |
| Nouvelles monde | Flux RSS en anglais (Reuters, CNBC, MarketWatch…), revue de presse Momentum (docs/21) | 0 | — |
| Cours US en temps réel (optionnel) | Différé de 15 min suffisant pour des ordres préparés la veille ; le temps réel n'est pas nécessaire au début | 0 | — |

La qualification A0.7 (docs/15) est étendue à ces sources. Couverture européenne à vérifier : les objectifs des analystes chez EODHD sont surtout fournis pour les grandes valeurs. Quand l'objectif manque, la fiche affiche « non disponible », jamais une estimation.

## 22.9 Décisions à prendre par l'utilisateur

- **Q1 — Produits** : actions **et ETF** (proposé) ? Les dérivés (turbos, warrants), les obligations et les cryptos restent-ils hors périmètre (proposé : oui) ?
- **Q2 — Données** : passer à EODHD All-in-One (+70 $/mois) pour les fondamentaux, le consensus et les calendriers ? Proposé : oui au démarrage de la famille B (moyen terme), après un mois d'essai de qualification.
- **Q3 — Marchés** : ordre U1 → U2 (États-Unis) → U3 (ETF) → U4 (Asie via ETF) ? Faut-il avancer les États-Unis avant le moyen terme ?
- **Q4 — Format de référence** : copier dans le dépôt (`docs/reference/`) ou coller dans la conversation une **fiche type** et la **revue du 5 août 2026** du projet Claude. Cette conversation n'a pas accès aux projets claude.ai. La fiche §22.3 sera calée dessus : rubriques, ordre, vocabulaire.
- **Q5 — Nombre d'idées** : la règle « 3 décisions par jour au plus » (docs/00 objectif 3) est maintenue. Est-ce aussi « 5 idées nouvelles par semaine » dans la revue du samedi ?

## 22.10 Feuille de route révisée (proposée)

| Phase | Contenu | Remplace |
|---|---|---|
| **2 — MVP propositions** (en cours de préparation) | Univers U1 ; familles A (D2, D3, D4) et D recalculée ; score ; fiche §22.3 complète avec **thèse LLM** ; sorties S2 à S6 ; **revue de portefeuille du samedi** ; brief et rapport | Phase 2 actuelle, étendue avec la thèse et la revue |
| **2b — Moyen terme** | EODHD All-in-One (si Q2) ; famille B (D7) ; objectifs des analystes ; calendrier macro ; bloc F du score | Partie de la phase 6 |
| **3 — Monde et ETF** | U2 (États-Unis, Suisse, Royaume-Uni), U3 ETF, famille C (D8 rotation), routage général des comptes, contexte macro multi-actifs | Partie de la phase 6 |
| 4 — Ouverture (D1) | Inchangé (phase 3 actuelle) | — |
| 5 — KPI P&L et fiscal ; 6 — Journal et post-mortem | Inchangés (phases 4 et 5 actuelles) | — |

Les phases 4 et 5 actuelles (KPI, fiscal, post-mortem) passent après l'élargissement, puisque l'objectif prioritaire est la qualité et le nombre des propositions. Le suivi de positions et de stops reste en place. À valider.

## 22.11 Critères d'acceptation (à reporter dans docs/15 après validation)

- **A22.1** : une proposition affiche toutes les rubriques de §22.3. Le potentiel, le risque et le ratio gain / risque sont recalculés à l'identique par un test Python. Aucun nombre de la thèse n'est absent du dossier.
- **A22.2** : sur une semaine de données figées, l'outil produit des propositions **sans aucune lettre** (familles A et B).
- **A22.3** : une valeur suédoise et une valeur américaine traversent l'univers avec une capitalisation en euros convertie au taux stocké, et sont routées vers le bon compte.
- **A22.4** : la revue du samedi couvre 100 % des positions, chacune avec un verdict et un stop jamais abaissé.
- **A22.5** : un objectif des analystes absent est affiché « non disponible ». Aucun objectif n'est produit par le LLM.
