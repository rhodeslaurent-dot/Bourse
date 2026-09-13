# 01 — Benchmark des démarches similaires et enseignements retenus

Benchmark réalisé le 9 septembre 2026 (sites spécialisés, forums francophones et anglophones, dépôts GitHub, blogs de traders swing/momentum, documentation fournisseurs). Reddit et Hacker News étaient inaccessibles depuis l'environnement de recherche ; le forum Boursorama bloque les robots. Les éléments ci-dessous sont donc issus de sources vérifiées (URL en fin de document) ; ce qui n'a pas pu être vérifié est signalé.

## 1.1 Outils et démarches comparables (faits)

| Outil / démarche | Ce qu'il fait | Ce qu'on en retient |
|---|---|---|
| **claude-trading-skills** (tradermonty, GitHub) | 50+ « skills » Claude Code : régime de marché (breadth, distribution days), screeners VCP/CANSLIM, *position sizer*, *pre-trade discipline gate*, *drawdown circuit breaker*, *trader-memory*, *signal postmortem*, digest hebdo. Routine annoncée : 15 min/jour. Données FMP/yfinance. | Le plus proche de notre cible (décision humaine, Claude Code). Découpage en modules indépendants + portes de discipline codées + post-mortem des signaux. |
| **ai-trading-bot** (bendagan85, GitHub) | Gap-up pré-market US ; règles affinées par Claude sur 90+ cycles ; modèle XGBoost filtrant les signaux ; analyse SHAP : **volume relatif** et **taille du gap** = variables les plus prédictives. | Le volume relatif est le filtre n°1 d'un scan d'ouverture. REX : sur-apprentissage initial, « backtesting is not trading ». |
| **stock-screener** (RyanJHamby, GitHub) | Minervini Trend Template, RS ≥ 70, filtre de régime, stop = max(ATR, plus bas swing), R/R ≥ 2, cache différencié (fondamentaux 7 j en saison de résultats / 90 j sinon : −74 % d'appels API), résultats historisés dans Git. | Architecture minimaliste et robuste, historisation gratuite, broker en lecture seule. |
| **TradingView-Screener** (shner-elmo, PyPI, MIT) | Wrapper Python du screener TradingView : marchés `france`, `germany`, `italy`, `netherlands`, `belgium`, `spain`, `portugal`… ; 3 000+ champs (`gap`, `relative_volume_10d_calc`, `change_from_open`…) ; données différées 15 min sans cookie de session. | Le moyen le plus simple de scanner **toutes les places PEA** à 9h15 sans scraping. Endpoint non officiel → prévoir une bascule. |
| **Journaux open source** : RR Metrics, Journedge/tradello, TradeNote, TradeTally | R-multiples, expectancy, profit factor, MAE/MFE, heatmap heure×jour, calendrier P/L, checklist pré-marché, adhérence au playbook, détection de « revenge trading ». | Liste de métriques de référence pour docs/12. Les projets **sans infrastructure lourde** sont ceux qui durent. |
| **Fil « Programmeurs Python parmi les IH »** (investisseurs-heureux.fr) | Screeners perso (Tkinter+PostgreSQL, Django), REX : blocages IP, légalité du scraping, **versionner les sorties de parseurs**, assertions sur le HTML, formats numériques FR (10 000,00), erreurs yfinance. | Robustesse des parseurs = sujet de premier ordre (docs/14). |
| **Fil ProRealCode « screener point d'entrée momentum et gap dans range »** | Discussion de screener séparant sélection des valeurs, configuration et déclenchement. | Rendre chaque condition explicite ; ne pas fusionner « valeur intéressante » et « moment d'entrer » (repris dans docs/07 §7.6). |
| **Fil « Création screener d'actions »** (Andlil) | Screener ProRealTime swing d'un particulier : volume > 1,3× moyenne, cassure 52 périodes, MM10 > MM50, RSI > 70. | Critères simples, testés par un particulier français. |
| **Routine d'un swing trader** (debuter-en-swing-trading.com) | 15–30 min le soir (mesuré < 22 min en moyenne) : indices → positions → 4 screeners = 4 stratégies → money management → ordres du lendemain. | Preuve qu'une routine EOD tient en 30 min. « **4 screeners = 4 stratégies** », chacun avec son score. |
| **Études Opening Range Breakout** (Zarattini et al., via danfin.net / concretumgroup) | ORB 5 min sur les **20 titres au plus fort volume relatif à l'ouverture**, stop = 10 % de l'ATR 14 j, risque 1 %/trade. ORB non filtré : Sharpe 0,48 ; filtré par volume relatif : 2,81. | « Le volume anormal à l'ouverture est le filtre critique ». Caveats : US, levier, coûts. |
| **Stockbee « momentum burst »**, **Qullamaggie « episodic pivot »** | Burst : clôture/clôture−1 > 1,04, volume > veille, consolidation 3–20 j avant, sortie J3–J5, stop sous la bougie de cassure. Episodic pivot : gap ≥ 10 % + volume moyen quotidien atteint en 15–20 min, entrée sur plus haut de l'opening range, stop = plus bas du jour. | Règles chiffrées réutilisables pour les détecteurs D1 et D3 (docs/06). |
| **Minervini screener** (fabtrader.in) | RS = 0,4·r12m + 0,2·r6m + 0,2·r3m + 0,2·r1m, rang percentile ≥ 70 ; RVOL ≥ 1,2 ; pivot 60 j. | Formule de force relative pour le classement de l'univers. |
| **Scanner temps réel de Tom Pounders** (oldschool-engineer.dev) | Dashboard gappers/gainers avec 3 services FastAPI, RabbitMQ, Redis ; données 200 $/mois. | Contre-exemple : sur-ingénierie pour un besoin quotidien. « Not all gappers are gainers ». |
| **PKScreener**, **Ghostfolio** (GitHub) | Scans planifiés + bot Telegram ; suivi de portefeuille Docker/Postgres. | Planification externe + Telegram = combinaison éprouvée. Fragilité = sources gratuites (Yahoo). |
| **API info-financière AMF** (data.gouv.fr) | Information réglementée archivée, sans clé, 10 000 appels/IP/jour, mise à jour quotidienne. | Utile en archive, pas pour le scan 7h–9h. |

## 1.2 Bonnes idées retenues (et où elles atterrissent dans la spec)

1. **Filtre de régime avant tout scan** (feu vert / orange / rouge sur CAC 40, STOXX 600, % de l'univers au-dessus de la MM50) qui bloque ou réduit la taille → docs/06 §6.2.
2. **Volume relatif (RVOL) comme filtre n°1** des scans d'ouverture ; ne remonter un gap que si RVOL ≥ seuil → docs/06 D1.
3. **Portes de discipline codées** : checklist bloquante avant fiche d'ordre, coupe-circuit après X R de perte hebdo/mensuelle, interdiction 48 h avant publication → docs/07.
4. **Sizing par le risque avec double stop** (max(ATR, structure)) et garde-fou ADR → docs/07.
5. **Cache « cache-first » différencié** (EOD quotidien, fondamentaux 7/90 j, intraday à la demande) → docs/03, docs/14.
6. **Historisation de tous les signaux** (pris ou non) et réconciliation J+5/J+20 → docs/12 (« signal postmortem »).
7. **Journal riche** : R-multiples, expectancy, profit factor, MAE/MFE, heatmap heure×jour, performance par détecteur/source → docs/12.
8. **Mémoire des trades exploitée par le LLM** pour la revue hebdomadaire (jamais pour produire des cours) → docs/12.
9. **4 détecteurs = 4 stratégies** avec score et statistiques séparés → docs/06.
10. **Parseurs robustes** : sorties versionnées, assertions, tests sur formats FR → docs/14.
11. **Univers PEA/SRD versionné** (listes datées, diff mensuel) → docs/05.
12. **Scan des communiqués pré-ouverture** par RSS + classification LLM (Haiku, coût ≈ 0,05–0,10 $/jour) → docs/06 D6.
13. **Mesure du « gain de précocité »** : pour chaque signal issu d'une newsletter, enregistrer le prix à 9h00, le prix à l'heure de la newsletter et le prix d'entrée obtenu → docs/12. C'est la métrique qui valide (ou non) l'investissement dans l'outil.

## 1.3 Pièges signalés par les communautés (et parades)

| Piège | Parade dans la spec |
|---|---|
| yfinance cassé en 2025 (refonte Yahoo, quotas 429), dividendes/fondamentaux peu fiables, blocages IP | yfinance = **secours EOD uniquement** ; source primaire payante (EODHD) ; abstraction `Provider` avec bascule et statut de donnée (docs/04). |
| Screeners différents = résultats différents sur les mêmes filtres | Chaque indicateur a une **définition écrite et un test** (docs/06 annexe). |
| Biais de survivance (backtest NDX : 664 k$ avec constituants actuels vs 266 k$ avec délistés) | Pas d'optimisation de paramètres ; validation par signaux historisés en conditions réelles (docs/12). |
| Sur-optimisation, « feel-good systems » à fort taux de réussite mais espérance négative | Suivi de l'**expectancy** avant le win-rate ; paramètres figés par version ; changement = ADR. |
| LLM mauvais sur les chiffres, biais look-ahead, hallucination de cours | Règle n°3 de CLAUDE.md : le LLM ne calcule pas ; sortie JSON validée ; les chiffres sont injectés. |
| Sur-ingénierie (microservices, brokers de messages) | Un conteneur applicatif + Postgres existant ; scission worker/web seulement si nécessaire (docs/03). |
| Fatigue des alertes | Déduplication par (instrument, type, jour), niveaux d'alerte, quotas par créneau (docs/09). |
| Faux signaux en range (croisements de moyennes), « not all gappers are gainers » | Filtre de régime + RVOL ; catalyseur requis pour les gaps (D1) seulement ; gap sans catalyseur = WATCH au mieux. |
| Stress / coût d'opportunité pour un particulier salarié | Créneaux fixes, tout le reste en asynchrone ; l'outil doit pouvoir être ignoré une journée sans dommage (stops en place chez le courtier). |

## 1.4 Fonctionnalités « en plus » jugées à forte valeur (intégrées)

- Détection comportementale simple (entrées après une perte, dépassement de taille, trades hors créneau) → docs/12.
- Digest hebdomadaire et revue mensuelle des règles → docs/12.
- Calendrier des publications de résultats intégré au risque (aucune source RSS/export gratuite vérifiée : calendrier EODHD `earnings` + saisie manuelle de secours) → docs/04, docs/07.
- Export de la watchlist au format TradingView pour inspection visuelle → docs/09.
- Suivi des transactions de dirigeants (déclarations AMF) en signal secondaire → docs/15 phase 6.
- Sources allemandes de communiqués (EQS Newswire) pour l'univers Xetra → docs/04 §4.2.
- Backtest léger différé : préférer la réconciliation des signaux historisés ; Backtrader en option phase 6.

## 1.5 Sources consultées (sélection)

github.com/tradermonty/claude-trading-skills · github.com/bendagan85/ai-trading-bot · github.com/RyanJHamby/stock-screener · github.com/xang1234/stock-screener · github.com/shner-elmo/TradingView-Screener · github.com/Eleven-Trading/TradeNote · rrmetrics.com · github.com/TheQuantum-Dev/tradello · github.com/GeneBO98/tradetally · github.com/pkjmesra/PKScreener · github.com/ghostfolio/ghostfolio · investisseurs-heureux.fr (fils t15040, t29847, t31721) · andlil.com/forum (t24479, t18761) · debuter-en-swing-trading.com/routine-swing-trader · danfin.net/opening-range-breakout-research · concretumgroup.com · stockbee.blogspot.com · qullamaggie.com · fabtrader.in · oldschool-engineer.dev · crackingmarkets.com/survivorship-bias · deepcharts.substack.com (yfinance) · twoquants.substack.com (R-multiples) · quantvps.com/blog/algorithmic-trading-with-llm · elitetrader.com (screeners inconsistants) · data.gouv.fr/dataservices/api-info-financiere · atlasflux.saynete.com (annuaire RSS bourse).
