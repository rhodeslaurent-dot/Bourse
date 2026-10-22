# 22 — Objectif final : propositions d'investissement classées, multi-marchés, au coût complet

Statut : **v2 du 22/10/2026**.

- **Décisions utilisateur du 22/10/2026** : réponses Q1 à Q5 (§22.0). Elles sont **acceptées** et reportées dans docs/00.
- **Rapprochement avec le référentiel de gestion** (§22.3) : **accepté le 22/10/2026**, avec les décisions de l'utilisateur sur R9, R10, R12, R13, R14, R19 et R21, et la feuille de route §22.10. Le référentiel est le document « Projet Investissement Bourse — Référentiel complet du cadre de gestion », version du 22/10/2026, fourni par l'utilisateur. Il **n'est pas versionné dans le dépôt**, qui est public ; seules les règles sont reprises ici, sans montants ni positions.

L'objectif, dans les termes de l'utilisateur :

> « Me proposer des actions ou produits financiers, sur différents marchés, avec les arguments d'investissement, le cours d'entrée, le cours de sortie, le potentiel de gain, le risque, le timing… comme Claude le fait dans nos échanges. Ne plus dépendre que des lettres ; que l'outil identifie lui-même de nouveaux signaux et propositions. »

Règles absolues inchangées :
- jamais d'ordre passé par l'outil ;
- jamais de donnée inventée ;
- **le LLM rédige mais ne calcule pas** : prix, objectifs, potentiels, risques, coûts, impôts et tailles sont calculés en Python et testés.

## 22.0 Décisions de l'utilisateur (22/10/2026)

| # | Question | Décision |
|---|---|---|
| Q1 | Produits | **Actions, puis ETF, puis cryptos, puis dérivés**, dans cet ordre de priorité. Toujours en position acheteuse ; la vente à découvert reste exclue. |
| Q2 | Données payantes | **Pas de nouvel abonnement récurrent**, sauf retour sur investissement démontré (§22.7). Il faut maximiser les sources gratuites ou déjà payées, pour chaque pays. |
| Q3 | Ordre des marchés | **1)** pays éligibles au PEA ; **2)** Europe non éligible au PEA (Royaume-Uni, Suisse…) ; **3)** États-Unis ; **4)** reste du monde, dont l'Asie. La performance visée de chaque proposition est calculée au **coût complet** : frais, taxes sur transactions, change, fiscalité de l'enveloppe, portage SRD. |
| Q4 | Référence | Référentiel de gestion du 22/10/2026, fourni par l'utilisateur (le lien de partage claude.ai n'est pas lisible par l'outil). |
| Q6 (23/10/2026) | Valeur au plus haut, sans résistance au-dessus | **Option B** : l'**objectif moyen des analystes** est retenu comme objectif exploitable s'il est publié par au moins `detectors.d7_trend_quality.min_analysts` analystes, dans la devise de cotation, et au-dessus du prix maximal (R4). Sans consensus utilisable, la valeur reste en WATCH « pas d'objectif exploitable ». Aucun objectif n'est un multiple du risque. La donnée vient de `fundamentals_refresh` (gratuit, §22.7). |
| Q5 | Nombre d'idées | **Pas de maximum.** L'outil identifie toutes les opportunités, les **classe** (meilleur gain net à l'objectif, meilleur ratio gain / risque net) et préconise les meilleures ; le classement fait le filtre. Horizon : de la journée à quelques semaines. |

**Conséquence sur docs/00 :**
- L'objectif 3 (« propositions par jour ≤ 3 ») devient : « **toutes** les opportunités sont classées ; les notifications et l'écran du jour ne montrent que le haut du classement ». Les paramètres sont `ranking.notify_top_n` et `ranking.home_top_n`.
- Le temps de travail reste d'environ 1 h par jour.

## 22.1 Écarts entre l'objectif et le cahier des charges ou le code

| # | Sujet | État actuel | Ajustement (§) |
|---|---|---|---|
| E1 | Idées propres à l'outil | Aucun détecteur propre n'est codé : les seules propositions viennent des lettres, en WATCH. | Phase 2 recentrée : §22.4, §22.10 |
| E2 | Marchés | 4 places en pratique (Paris, Amsterdam, Bruxelles, Xetra). Les capitalisations hors euro sont vides, ce qui écarte les valeurs nordiques sans le signaler. | §22.6 |
| E3 | Produits | Actions seulement ; dérivés et cryptos hors périmètre. | §22.5 |
| E4 | Moyen terme | Le libellé `medium` existe, mais aucun détecteur ne l'utilise. | §22.4 (famille B, jusqu'à quelques semaines) |
| E5 | Thèse d'investissement | Aucune ; le modèle `proposals` n'a pas de champ pour la thèse. | §22.2, §22.8 |
| E6 | Sortie et potentiel | Objectifs = multiples de R, donc un rapport gain / risque de 2 « par construction ». Pas de potentiel net. | §22.2, §22.3 (objectif exploitable) |
| E7 | Timing | Pas de « pourquoi maintenant », pas de calendrier macro. | §22.2, §22.7 |
| E8 | Contexte macro | Régime technique CAC / STOXX seulement. | §22.7 (tableau macro gratuit), docs/21 |
| E9 | Revue de portefeuille | Sorties S1 et S7 seulement, pas de revue ligne par ligne. | §22.9 |
| E10 | Coût complet et classement | Coûts estimés à l'entrée seulement, sans fiscalité ni change dans le potentiel. Aucun classement. | §22.2, §22.3 |
| E11 | **Règles du référentiel absentes ou différentes** (stop sous zone, prix maximal par le ratio gain / risque, cliquet, plafonds de risque, filtre de marché, disjoncteur Brent…) | Le cahier v1.1.1 (09/09/2026) précède six semaines de corrections. | §22.3 |

## 22.2 Fiche d'opportunité (une seule forme, quelle que soit l'origine)

| Rubrique | Contenu | Calcul |
|---|---|---|
| Instrument | Nom, ISIN ou identifiant, **type** (action, ETF, crypto ou ETP crypto, dérivé), place, devise, secteur, capitalisation, éligibilité PEA / SRD | Référentiel |
| Verdict | BUY / WATCH / SELL-REDUCE / HOLD / NO TRADE ; score sur 100 (blocs C / T / F / R / M), confiance sur 5, **rang** du jour | Python |
| Horizon | journée, quelques jours, swing de 2 à 6 semaines, avec la famille d'origine (§22.4) | Python |
| **En une phrase** (23/10/2026) | Ce qui fait l'opportunité, écrit **par règles** depuis les faits du signal (cassure du plus haut de 60 séances sur volume 2× la moyenne, repli sur la MM20 qui tient…), sans aucun chiffre absent du signal ; place de cotation, devise et secteur sous le nom | Python |
| **Thèse** | 3 à 5 arguments, **chacun relié à des faits numérotés** du dossier : catalyseur, technique, fondamentaux, secteur et macro, avis des lettres. Faits, sources, analyse, opinion et incertitudes séparés (référentiel §1.2). | LLM, sous les garde-fous §22.8 |
| **Pourquoi maintenant** | Déclencheur constaté et daté : repli sur support qui tient, clôture au-dessus de l'obstacle, publication, convergence de lettres | Python (le fait), LLM (la formulation) |
| **Entrée** | Protocole (repli sur support ou franchissement, §22.3 R6), zone, **prix maximal** = le plus bas du prix maximal technique et du prix maximal de ratio gain / risque net (§22.3 R4), type d'ordre, validité, péremption | Python |
| **Sortie** | Stop (règle de placement, niveau glissé), **objectifs exploitables** avec leur origine (résistance, plus haut sur 52 semaines, hauteur de la base, objectif publié par une lettre, objectif des analystes si la donnée est disponible), déclencheur de vente. **Pour chaque objectif** : gain brut en % depuis le prix maximal et gain net en € et en % pour le compte conseillé ; le premier est l'objectif cible, le dernier l'objectif maximal | Python |
| **Gain et risque nets** | Pour chaque compte possible (PEA / CTO / SRD), depuis le prix maximal : **gain net à l'objectif** en € et en %, après frais, taxes sur transactions, change et impôt de l'enveloppe ; **perte nette au stop glissé** ; **ratio gain / risque net** | Python (§22.3 R11) |
| **Espérance** | Taux de réussite et gain moyen des opportunités comparables (famille, régime, horizon), avec l'effectif et le rétrécissement n/(n+k) ; espérance nette en R. « Historique insuffisant » en dessous de `learning.min_samples`. | Python (docs/18) |
| Taille | Nombre de titres sur le prix maximal et le stop glissé, montant, risque, part du capital (plafond notionnel), compte conseillé | Python (docs/07, 08) |
| Calendrier | Résultats, détachement, **événements macro** (BCE, Fed, inflation, emploi) sur l'horizon ; « pas d'entrée avant publication » | Python |
| Vigilance et invalidation | Risques (LLM, reliés aux faits) ; condition d'invalidation chiffrée (Python) | Mixte |
| Sources | Lettres, nouvelles, articles, données avec `data_status` et horodatage de marché | Python |

## 22.3 Rapprochement avec le référentiel de gestion du 22/10/2026 (accepté)

Pour chaque règle, la proposition est d'**adopter la règle du référentiel**, qui résulte de la pratique et des erreurs constatées. Elle sera reportée dans `config/params.yaml` et docs/06 et 07. Les points marqués ⚖ sont encore à arbitrer dans le référentiel lui-même.

| # | Règle du référentiel | Cahier actuel | Proposition |
|---|---|---|---|
| R1 | **Placement du stop** : borne basse de la zone de support × (1 − 0,5 %), jamais dans la zone ni à sa borne haute | Plus bas de la structure − 0,25 × ATR | Si une **zone de support** est disponible (lettre ou zone détectée), le stop = borne basse × (1 − `risk.stop.wick_buffer_pct`) ; sinon, la règle actuelle. Un stop dans la zone est **refusé**. |
| R2 | **Invariant** : un stop ne descend jamais, sauf détachement de dividende | « Jamais en baisse » pour le trailing | Invariant général testé ; exception : ajustement du montant du dividende détaché. |
| R3 | **Cliquet** : clôture nette au-dessus d'un obstacle identifié → stop = obstacle × (1 − 0,5 %). Ordre de priorité : invariant > placement > cliquet | Trailing par l'ATR (S3) | Le cliquet sur obstacle devient la règle S3 par défaut ; le trailing ATR ne sert qu'en repli. L'ordre de priorité est codé. |
| R4 | **Prix maximal** = [(objectif + 2 × stop) ÷ 3] × (1 − frais aller-retour) : ratio gain / risque ≥ 2 **net de frais** sur un **objectif exploitable** (publié, non conditionné au franchissement d'un autre niveau) | Prix maximal = niveau + x % ; objectif = 2R ou 3R | Prix maximal retenu = **min**(prix maximal technique, prix maximal de ratio net). Objectif 1 = premier niveau **exploitable** (résistance, plus haut, objectif publié), **jamais** un simple multiple de R. Ratio minimal `admission.min_reward_risk` = 2. Aucune exception. |
| R5 | **Un prix maximal atteint n'est pas un signal d'entrée** : un repli **à travers** le support est une dégradation, pas une entrée (règle du 19/10) | Absent | Une proposition exige que la **structure tienne** : clôture au-dessus de la borne basse du support, franchissement non rendu. Sinon, le statut passe à « configuration dégradée ». |
| R6 | **Deux voies d'entrée** : repli sur support qui tient → entrée au fixing de clôture (17:35) ; franchissement → clôture qui confirme ou retest, au meilleur ratio | Ordre J+1 à plage de déclenchement à l'ouverture | Ajouter le protocole « fixing de clôture » (fiche à 17:15 Paris, ordre au fixing) et le protocole « retest ». La plage de déclenchement reste disponible. |
| R7 | **Ordres limites permanents** au prix maximal, posés chaque matin sur les 3 ou 4 dossiers les plus proches, validité jour, stop posé dès l'exécution, péremption à 3 séances | Validité ≤ 3 séances | Carnet d'ordres préparés trié par **distance au prix maximal**, rappel chaque matin, recalcul à 3 séances (`entry.expiry_sessions`). |
| R8 | **Plafonds de risque** (« heat », convention B bornée à zéro) : 5 % du capital pilote en régime normal, 3,5 % en régime dégradé ; **600 € par ligne** (30 % du plafond) ; **budget réservé de 300 €** inaccessible aux lignes de plus de 10 séances ; pondération SRD × 1,25 ; convention A = indicateur avec plafond souple | `max_open_risk_pct` 4 % unique | `risk.max_open_risk_pct_by_regime` {normal 0,05, dégradé 0,035} ; `max_line_risk_pct_of_cap` 0,30 ; `reserved_signal_budget_pct` 0,0075 et `reserved_budget_age_sessions` 10 ; `srd_risk_weight` 1,25 ; convention A affichée avec `risk.heat_a_soft_cap_pct`. |
| R9 | **Plafond notionnel par ligne** | `max_position_pct_of_capital` 0,20 | **Décision du 22/10 : 20 %** (inchangé) ; relevable seulement par dérogation (R21). |
| R10 | **Filtre de marché** : le référentiel propose l'option A (seuil absolu) ou l'option C (obstacle publié et avis de la lettre sur le CAC) | Régime calculé (feu vert / orange / rouge) | **Décision du 22/10 : option C, modulée par le niveau de risque conjoncturel, sans blocage absolu sur un seuil de marché.** Voir docs/07 §7.9 « portail de marché » : le portail est ouvert (taille normale) ou restreint (sélection plus exigeante et taille réduite) ; le régime rouge n'autorise une entrée que par dérogation (R21). |
| R11 | **Coût complet** : TTF française 0,40 % selon le **siège** de la société et sa capitalisation (et non la place de cotation) ; courtage aller et retour ; PFU 31,4 % en CTO depuis 2026 ; portage SRD ≈ 0,023 % par jour | TTF prévue par la capitalisation seule ; impôt absent du potentiel | Frais nets calculés par compte : courtage (`brokers.*`), TTF selon la **liste officielle annuelle** des sociétés concernées (gratuite), taxes étrangères (Italie, Espagne, droit de timbre britannique) en `tax.ftt_by_country`, change (`brokers.<b>.fx_fee_pct`), portage SRD, impôt de l'enveloppe (`tax.*` daté). Le crédit d'impôt d'une perte n'est pas supposé (`tax.assume_loss_offset: false`, prudent). |
| R12 | **Disjoncteur Brent** : au-dessus de 100 $, pas d'ouverture SRD ; réouverture sous 95 $ | Absent | **Décision du 22/10 : comme R10, pas de blocage absolu.** Un Brent au-dessus de 100 $ fait passer le SRD en mode **restreint** (gain net supérieur exigé, taille réduite), et entre dans le niveau de risque conjoncturel. Retour au mode normal sous 95 $ pendant 3 séances. |
| R13 | **SRD** refusé comme mode de fonctionnement | Comparaison neutre des comptes | **Décision du 22/10 : SRD proposé si son gain net dépasse celui du comptant, ou si la trésorerie du compte d'investissement ne suffit pas pour le comptant.** Toujours dans les limites de couverture et de levier, et en mode restreint si R12 est actif. |
| R14 | **Événement qu'un stop ne couvre pas** (BCE, Fed, résultats) : la règle de sortie s'applique avant l'événement | Pas d'entrée dans les 48 h avant des résultats | Étendu aux événements macro. **Décision du 22/10 : exception si l'opportunité est intéressante et faiblement exposée à l'annonce.** Exposition mesurée en Python sur les écarts d'ouverture historiques de la valeur lors des 8 derniers événements du même type : la perte au 75e centile ne doit pas dépasser `events.max_gap_loss_r` (1,5 R). Le calcul est affiché sur la fiche. |
| R15 | **Fiche testable** : une fiche de lettre n'est retenue que si elle donne un **niveau d'invalidation explicite** et des **objectifs chiffrés** ; c'est ensuite l'arithmétique qui décide | Recommandation de lettre → WATCH | Une fiche sans invalidation ou sans objectif = « non testable » : elle reste visible mais n'est pas classée. |
| R16 | **Capital pilote fixe**, révisé à la main, jamais recalculé selon le résultat | `capital_base: pilote` | Inchangé (déjà conforme). |
| R17 | **Poche ETF sanctuarisée** (investissement progressif mensuel) : exclue du capital pilote et des plafonds de risque, jamais source de liquidité ; clause d'accélération si le MSCI World recule d'au moins 15 % depuis son sommet | Absent | Enveloppe logique `pocket: dca` (positions marquées) ; rappel mensuel de versement ; alerte de la clause d'accélération (indice gratuit, §22.7). |
| R18 | **Position fiscale** : une moins-value conservée pour être cristallisée à une date choisie est exclue des plafonds de risque | Absent | Statut de position `tax_harvest`, avec une date cible et un rappel ; hors plafonds de risque. |
| R19 | **Contrôle de vraisemblance** des cours ; une estimation ne fonde jamais un verdict | Statuts de fraîcheur des données | **Décision du 22/10 : le cours est relevé en temps réel à chaque analyse** (proposition, thèse, revue des lettres, assistant, revue de portefeuille), via Saxo (temps réel si l'abonnement de la place est actif, sinon différé déclaré). Le cours utilisé, son horodatage de marché et son statut sont enregistrés. Recoupement entre sources ; un cours non temps réel est signalé et n'ouvre pas de fiche d'ordre intraday. |
| R20 | **Trésorerie non investie** : rappel d'un placement monétaire | Absent | Ligne de rapport « trésorerie non rémunérée » (montant, durée). Jamais une proposition de produit. |

| R21 | **Dérogation aux plafonds** (décision utilisateur du 22/10/2026 : « le plafond de risque peut être rehaussé, mais seulement si l'investissement est vraiment intéressant et avec mon accord ») | Cash additionnel réservé aux scores ≥ 85, confiance ≥ 4, risque faible | Une opportunité bloquée par un plafond (risque ouvert, ligne, notionnel R9) peut demander une **dérogation** si elle est **exceptionnelle** : rang 1 du jour, score ≥ `risk.override.min_score` (85), confiance ≥ 4, ratio gain/risque net ≥ `risk.override.min_net_rr` (3), portail de marché ouvert. La fiche reste `BLOQUÉ : <règle>` et affiche le dépassement exact (« +240 € de risque ouvert, soit 0,6 % du capital ») avec un bouton « Accorder une dérogation ». **Rien n'est relevé sans l'accord explicite de l'utilisateur**, donné pour cette seule proposition, motivé, horodaté et figé dans l'instantané de décision. Bornes absolues : plafond × `risk.override.max_multiplier` (1,5) ; le risque **par trade** (0,75 %) n'est jamais relevé. La dérogation expire avec la proposition. Le retour d'expérience compte à part les trades sous dérogation. |

Les montants en euros du référentiel (600 €, 300 €…) sont convertis en **pourcentages du capital pilote** dans `params.yaml`, pour suivre les révisions du capital.

## 22.4 Moteur d'idées : quatre familles, un classement

1. **A — Technique court terme** (D1 à D4 de docs/06, avec les règles R1 à R7) sur tout l'univers. Les zones de support et de résistance sont **détectées** (plus hauts et plus bas pivots regroupés en zones, gaps non comblés), pas seulement lues dans les lettres.
2. **B — Swing « tendance + qualité »** (D7, de quelques jours à quelques semaines). Conditions :
   - force relative à 3 et 6 mois dans le premier quintile ;
   - cours au-dessus d'une moyenne 200 jours croissante ;
   - fondamentaux lorsque la donnée gratuite existe : révisions, croissance, dette. Sinon le bloc F est « non attribué ».
   L'entrée suit R6, l'objectif doit être exploitable (R4), l'objectif des analystes est affiché s'il est disponible.
3. **C — Rotation** (D8, hebdomadaire). Force relative des secteurs, des régions et des classes d'actifs à travers des ETF. Elle produit des **propositions d'ETF** et des points du bloc M pour A et B.
4. **D — Externe** : lettres (docs/20), revue de presse (docs/21), alertes techniques Zonebourse. Une fiche de lettre **testable** (R15) devient un signal recalculé par le moteur. Les points de convergence vont au bloc C. Une lettre seule ne suffit pas à un BUY.

**Classement** (Q5) :
- Toutes les opportunités qui passent les tests d'admission (R4, R5, filtres, portail) sont classées par :
  1. **espérance nette en R**, lorsque l'historique est suffisant ;
  2. **ratio gain / risque net** ;
  3. **gain net à l'objectif** en %.
- Les tris secondaires sont proposés sur la page.
- Les opportunités refusées restent consultables avec leur motif (« P_max dépassé de 0,26 % »…).
- Les notifications ne concernent que les `ranking.notify_top_n` premières par jour (défaut 3), plus les incidents P1.

## 22.5 Produits, dans l'ordre de priorité de l'utilisateur

| Rang | Produit | Compte | Particularités |
|---|---|---|---|
| 1 | **Actions** | PEA si éligible et plus favorable au coût complet, sinon CTO ; SRD selon R13 | Réglementation existante |
| 2 | **ETF** (UCITS) | PEA si éligible, sinon CTO | Univers = liste datée d'ETF (indices, secteurs, régions, thèmes, matières premières via ETC) ; pas de catalyseur de société, blocs C et F adaptés (flux, contexte) ; famille C |
| 3 | **Cryptos** | **ETP ou ETN cotés** au CTO, si disponibles chez Saxo (recherche d'instrument en lecture seule) | Signal calculé sur l'actif sous-jacent (données gratuites 24 h / 24), fiche sur le produit coté ; frais de gestion et écart avec le sous-jacent affichés. Aucun compte sur une plateforme d'échange de cryptos (pas de connecteur) : à décider plus tard. |
| 4 | **Dérivés** (turbos, warrants, mini-futures ; acheteur seulement) | CTO | Signal sur le **sous-jacent**. La fiche donne les **critères du produit** : barrière désactivante sous le stop du sous-jacent, levier maximal ; la perte maximale = la prime, comptée intégralement dans le risque. Les cours des produits ne sont pas disponibles gratuitement de façon fiable : prix et produit « à vérifier chez l'émetteur ». Phase tardive. |

La vente à découvert reste exclue. docs/00 §0.4 est mis à jour en conséquence.

## 22.6 Marchés, dans l'ordre de l'utilisateur

| Étape | Marchés | Compte | Prérequis |
|---|---|---|---|
| M1 | **Éligibles au PEA** : Paris, Amsterdam, Bruxelles, Lisbonne, Xetra, Milan, Madrid, Stockholm, Copenhague, Helsinki, Oslo, Dublin, Vienne, Varsovie | PEA / CTO / SRD Paris | Calendriers des places (fichier daté), capitalisation convertie en euros au taux BCE stocké (correctif E2), correspondance du screener, taxes étrangères sur transactions |
| M2 | **Europe hors PEA** : Londres, Suisse | CTO | Droit de timbre britannique, change GBP / CHF, calendriers |
| M3 | **États-Unis** : NYSE, Nasdaq | CTO | Calendrier NYSE, fuseau New York, change USD, cours différés de 15 min suffisants pour des ordres préparés ; certificat W-8BEN chez Saxo (dividendes) |
| M4 | **Reste du monde** : Japon, Hong Kong, Canada, Australie… **d'abord via ETF**, actions en direct ensuite | CTO | Calendriers, change, disponibilité chez Saxo |

À chaque étape, la performance visée est calculée au **coût complet** (R11) : courtage, taxes de la place, change aller et retour, impôt de l'enveloppe, portage SRD. Le classement compare donc les marchés entre eux à coût égal.

## 22.7 Données : gratuites ou déjà payées d'abord (Q2)

| Besoin | Source gratuite ou déjà payée | Remarques |
|---|---|---|
| Cours de fin de journée, monde (actions, ETF, indices, change) | **EODHD**, déjà payé (environ 70 places) ; yfinance en secours | Déjà en place |
| Screening multi-pays (Europe, États-Unis, Royaume-Uni, Suisse, Japon…), secteurs, performances, **cryptos** | **tradingview-screener** (gratuit, différé) | Étendre la correspondance des places ; champs fondamentaux et recommandations d'analystes à **qualifier** (A0.7) |
| Fondamentaux, **objectif moyen des analystes**, nombre d'analystes, date des prochains résultats | **Yahoo Finance via yfinance** (gratuit, non officiel, fragile) ; champs du screener TradingView | Source et date affichées ; « non disponible » si absent ; quota bas et cache d'une journée |
| Calendrier des résultats | yfinance, TradingView, flux Nasdaq (États-Unis), lettres Zonebourse | Croisement de deux sources si possible |
| **Calendrier macro** (BCE, Fed, inflation, emploi) | Flux hebdomadaire public de **ForexFactory** (JSON) ; calendriers officiels BCE et Fed | Fichier daté en repli |
| **Tableau macro** : Brent, or, VIX, euro-dollar, taux 10 ans US / Allemagne / France, écart OAT-Bund, MSCI World | yfinance (contrats et indices), **BCE Data Portal** et **Banque de France Webstat** (taux, gratuits), **FRED** (États-Unis, clé gratuite), EODHD si disponible dans le forfait | Alimente le régime, le disjoncteur Brent (R12), la clause d'accélération (R17) et la rubrique 1 du rapport |
| Nouvelles | Flux RSS (Boursorama, Zonebourse, BFM Bourse, Investir / Les Échos, Reuters et CNBC si disponibles, MarketWatch, Yahoo Finance), communiqués (Actusnews, GlobeNewswire, Business Wire), revue de presse Momentum (docs/21), lettres | Titres et extraits ; aucun contenu payant contourné |
| Liste des sociétés soumises à la TTF française | Liste officielle annuelle (BOFiP), gratuite | Fichier daté (R11) |
| Instruments disponibles chez le courtier (ETF, ETP crypto, dérivés) et cours différés | **Saxo OpenAPI**, déjà ouverte en lecture seule : recherche d'instruments et prix différés | Lecture seule (règle 1) |
| Cryptos (sous-jacent) | API publique **CoinGecko** (clé gratuite) ou données publiques des plateformes d'échange | 24 h / 24 : statut de marché propre |
| ETF (liste, éligibilité PEA, frais) | Listes publiques des émetteurs et d'Euronext, fichier de référence daté | Mise à jour mensuelle |

**Règle de dépense** (`data.paid_upgrade_rule`) : un abonnement payant n'est proposé que si le retour d'expérience (docs/18) montre, sur au moins 3 mois, que la donnée manquante coûte plus que l'abonnement. Exemples : bloc F « non attribué » sur les opportunités gagnantes, objectif non disponible. Critère par défaut : coût annuel ≤ `data.paid_max_cost_pct_of_net_gains` (10 %) des gains nets annualisés. La décision reste à l'utilisateur, avec un ADR.

**Robustesse des sources gratuites** : chaque source a son statut (`data_status`), son âge de marché et un test de vraisemblance (R19). Une source muette ou incohérente rend la donnée « indisponible », jamais estimée. Aucun basculement silencieux (`data.silent_source_switch_allowed: false`).

## 22.8 Rôle de Claude (LLM) : rédacteur contrôlé

- **Entrée** : un dossier de faits numérotés, construit en Python. Il comprend :
  - la fiche calculée et les indicateurs ;
  - les nouvelles, articles et avis des lettres ;
  - le tableau macro et le calendrier ;
  - l'historique comparable et les leçons retenues.
- **Sortie** : schéma `ThesisOut`, à température 0. Champs :
  - `arguments[]` : texte d'au plus 200 caractères, identifiants des faits utilisés, nature ∈ {fait, analyse, opinion} ;
  - `risques[]` ;
  - `pourquoi_maintenant` ;
  - `incertitudes` ;
  - `ce_qui_invaliderait`.
- **Garde-fous** :
  - tout chiffre doit figurer dans le dossier ;
  - tout identifiant de fait doit exister ;
  - un argument sans fait est écarté ;
  - les extraits de lettres payantes sont synthétisés, jamais recopiés longuement (référentiel §1.2).
- **Assistant** : il ne **crée** pas de recommandation. Il explique, compare et challenge les fiches du moteur, par exemple « pourquoi X avant Y ? ». Il rappelle les règles du référentiel quand une demande les contourne : baisser un stop, annuler un stop déclenché, recourir au levier (référentiel §11.3).
- **Coût** : avec Sonnet, une thèse par opportunité du haut du classement, plus une revue hebdomadaire. Cela reste dans le budget de 50 $ par mois ; le plafond journalier s'applique.

## 22.9 Revue de portefeuille (le travail fait jusqu'ici avec Claude)

- **Quotidien** : rapport en 10 rubriques (référentiel §1.5 et docs/09). Le classement du jour alimente la rubrique 4 ; le tableau macro alimente les rubriques 1, 2 et 10.
- **Hebdomadaire** (samedi) et **à la demande** : pour chaque position, les éléments suivants.
  - Thèse d'origine et ce qui a changé depuis.
  - Règles de sortie évaluées : stop, déclencheur publié, thèse, événement R14, meilleure opportunité.
  - Cliquet R3.
  - Verdict HOLD / RENFORCER (jamais une ligne perdante) / ALLÉGER / VENDRE.
- **Synthèse** de la revue :
  - plafonds de risque A et B ;
  - exposition par compte, pays, secteur et devise ;
  - trésorerie (R20) ;
  - poche sanctuarisée (R17) ;
  - positions fiscales (R18) ;
  - carnet d'ordres préparés (R7) ;
  - **décisions en attente** (référentiel §12.4).
- **Journal des erreurs et des règles**, sur le modèle du référentiel §11 : chaque règle ajoutée est datée avec son cas d'origine, dans `docs/ADR` ou dans la page Apprentissage (docs/18).

## 22.10 Feuille de route révisée (acceptée le 22/10/2026)

État au 22/10/2026 (code, en attente de déploiement sur le PC) : phase 2 faite ; phase 3 faite (fondamentaux gratuits, D7, ETF, rotation) ; phase 4 prête, désactivée ; cryptos (ETP) et dérivés prêts, désactivés ; phase 5 (ouverture D1) non commencée — elle attend la validation du flux temps réel (A0.7).

| Phase | Contenu |
|---|---|
| **2 — Moteur de propositions, actions M1** | Règles R1 à R8 et R10 à R15 dans `params.yaml` et docs/06 et 07 ; détection des zones de support et de résistance ; familles A (D2, D3, D4) et D recalculée ; coût complet et classement ; fiche §22.2 avec thèse ; carnet d'ordres préparés ; revue de portefeuille ; tableau macro gratuit et calendrier macro ; correctif des capitalisations hors euro et calendriers M1 |
| **3 — Swing B, ETF et rotation** | Famille B (D7) avec données gratuites qualifiées ; ETF (liste datée) ; famille C (D8) ; poche sanctuarisée (R17), positions fiscales (R18) |
| **4 — Europe hors PEA et États-Unis** | M2 puis M3 : calendriers, change, taxes, fuseau, W-8BEN |
| **5 — Ouverture (D1)** | Inchangé : après la validation du flux temps réel |
| **6 — Cryptos (ETP), puis reste du monde via ETF** | M4, sous-jacents crypto |
| **7 — Dérivés** | Sélection de produits sur le sous-jacent ; cours à vérifier chez l'émetteur |
| En continu | KPI P&L et fiscal, journal et post-mortem (anciennes phases 4 et 5), au fil des besoins |

## 22.11 Critères d'acceptation (reportés dans docs/15 après validation)

- **A22.1** La fiche affiche toutes les rubriques de §22.2. Le gain net, la perte nette et le ratio gain / risque net de chaque compte sont recalculés à l'identique par un test, à partir des paramètres datés. Aucun chiffre de la thèse n'est absent du dossier.
- **A22.2** Sur une semaine de données figées, des opportunités sont produites **sans aucune lettre**. Elles sont classées, et chaque refus porte son motif chiffré.
- **A22.3** Les cas du référentiel sont rejoués en test sur fixtures :
  - stop dans la zone refusé (R1) ;
  - prix maximal net de frais (R4) ;
  - TTF selon le siège (R11) ;
  - « P_max qui passe pour la mauvaise raison » (R5) ;
  - priorité invariant > placement > cliquet (R2, R3) ;
  - plafond par ligne et budget réservé (R8).
- **A22.4** Une valeur suédoise, puis britannique, puis américaine traverse l'univers avec une capitalisation en euros convertie au taux stocké. Elle est routée vers le bon compte et sa fiche intègre les taxes et le change de sa place.
- **A22.5** Les données gratuites indisponibles sont affichées « non disponible ». Aucun objectif ni aucun chiffre n'est produit par le LLM.
- **A22.6** La revue hebdomadaire couvre 100 % des positions ; aucun stop proposé n'est inférieur au stop précédent, hors détachement de dividende.
