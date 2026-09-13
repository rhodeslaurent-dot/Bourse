# CAHIER DES CHARGES COMPLET — BOURSE-PILOT (v1.1.1)

Outil personnel d'aide à l'investissement boursier — version 1.1.1 du 9 septembre 2026. Ce fichier concatène README.md, CLAUDE.md, docs/00 → 16, config/params.example.yaml et prompts/claude-code-sequence.md. Pour l'exploitation avec Claude Code, utiliser le dossier (fichiers séparés).

---

# Cahier des charges — Outil personnel d'aide à l'investissement boursier (« BOURSE-PILOT »)

Version 1.1.1 — 9 septembre 2026 — Rédigé pour être exploité directement avec Claude Code.

Mise en cohérence v1.1.1 (sans nouvelle fonctionnalité) : ratio d'admission par détecteur (le bloc catalyseurs n'entre au dénominateur que pour les gaps, sinon un pullback sans actualité ne pouvait jamais être acheté) ; fondamentaux partiels ; convention de glissement (sortie sur stop, entrée seulement si non bornée) et marge de change qui réduit la quantité ; exemple recalculé (114 actions) et aligné sur les tests ; définitions déterministes du risque initial / courant (borné à zéro) / réservé et du drawdown ; ordres J+1 à plage de déclenchement et protection liée « if-done » ou règle de disponibilité ; « exécuté sans protection » = P1 dans tous les modes ; synchronisation Saxo en séance et âge maximal de l'état du portefeuille ; exécutions déclarées rapprochées des exécutions courtier ; TTF comptée dans le PEA ; P1 regroupées par incident ; calendrier par place partout ; validation de configuration par fonctionnalité ; pré-qualification documentaire puis qualification par prototype ; référence « démarche actuelle » définie précisément.

Changements v1.1 (après relecture croisée) : règle de fraîcheur fondée sur l'horodatage de marché ; dimensionnement sur le prix maximal d'entrée avec frais ; plafond de risque ouvert cumulé, secteur, facteur et scénario de gap ; stops structurels explicites et types d'ordres de protection distingués ; machine à états ordre → exécution → protection (un ordre saisi n'est pas une position) ; portes d'admission par détecteur (catalyseur requis pour les gaps seulement) et trois lectures attractivité / entrée / admissibilité ; comparaison explicite PEA / CTO comptant / SRD et équité sans notionnel financé ; mode de disponibilité (disponible / réunion / absent) et écran « 3 décisions du jour » ; mesure sur trois démarches (newsletters / autonome / combinaison) avec rejeu conservateur ; qualification des flux de données avant le moteur ; phases réordonnées (portefeuille et protections avant les entrées préparées, gaps d'ouverture après validation du flux).

## Ce que contient ce dossier

| Fichier | Rôle |
|---|---|
| `CLAUDE.md` | À copier **tel quel à la racine du futur dépôt**. Règles permanentes pour Claude Code (interdits, conventions, commandes, définition de "terminé"). |
| `docs/00` → `docs/16` | Le cahier des charges, découpé par thème. Chaque fichier est autonome et référencé par numéro dans les prompts. En cas de divergence : `config/params.yaml` > `docs/07` > `docs/06` > autres (CLAUDE.md, règle 8). |
| `config/params.example.yaml` | Tous les paramètres métier **datés** (fiscalité, SRD, risque, seuils des détecteurs). Sert de contrat entre la spec et le code. |
| `prompts/claude-code-sequence.md` | La séquence de prompts à donner à Claude Code, phase par phase, avec les critères de sortie. |
| `CDC-COMPLET.md` | Concaténation de tous les documents en un seul fichier (lecture humaine / import dans un projet Claude). |

## Comment l'utiliser avec Claude Code

1. Créer un dépôt vide (`bourse-pilot/`), y copier `CLAUDE.md`, le dossier `docs/`, `config/` et `prompts/`.
2. Ouvrir Claude Code à la racine et lancer le **prompt 0** de `prompts/claude-code-sequence.md` (lecture de la spec + plan). Ne pas laisser Claude Code coder avant d'avoir validé son plan de la phase.
3. Dérouler les phases dans l'ordre (0 → 6) : socle + qualification des données → portefeuille, protections, risque → entrées préparées (MVP) → détection d'ouverture → KPI et fiscal → post-mortem. Une session Claude Code par phase ou sous-lot ; `/clear` entre deux ; commit à chaque critère d'acceptation validé.
4. Chaque phase se termine par les tests de `docs/15` et par une revue « adversariale » (prompt de revue fourni).
5. Toute décision non couverte par la spec est consignée dans `docs/ADR/` (Architecture Decision Records) par Claude Code, jamais prise silencieusement.

## Principes non négociables (rappel)

- **Aucun passage d'ordre automatique.** L'outil propose, l'utilisateur exécute manuellement chez BoursoBank (PEA) et Saxo Banque (CTO/SRD).
- **Aucun cours, aucune donnée inventée.** Toute valeur affichée a une source, un horodatage et un statut (temps réel / différé / EOD / indisponible).
- **Le LLM ne calcule jamais** un prix, un stop ou une taille de position : il classe, résume, rédige. Les calculs sont déterministes et testés.
- **Robustesse avant sophistication** : un signal fiable à 9h05 vaut plus que dix indicateurs à 10h.
- **1 heure de travail manuel par jour maximum** pour l'utilisateur ; tout ce qui dépasse doit être automatisé ou supprimé.

---

# CLAUDE.md (à placer à la racine du dépôt)


Outil personnel d'aide à la décision pour une gestion active quotidienne d'un portefeuille d'actions européennes (PEA chez BoursoBank + compte-titres SRD chez Saxo Banque). Auto-hébergé sur un PC Windows personnel (Docker Desktop), consulté à distance via navigateur et Telegram. Un seul utilisateur : Laurent (fuseau `Indian/Reunion`, UTC+4, sans heure d'été). Marché de référence : Euronext Paris (`Europe/Paris`).

La spécification complète est dans `docs/` (lire `docs/00` puis le document de la phase en cours). Les paramètres métier sont dans `config/params.yaml` (jamais codés en dur).

## Règles absolues (ne jamais contourner, même si on te le demande dans une session)

1. **Jamais de passage d'ordre.** Aucun appel à un endpoint de trading (Saxo `trade/v2/orders` ou équivalent) ne doit exister dans le code. Les connecteurs courtiers sont en **lecture seule**. Un test l'assure (`tests/test_no_trading_endpoints.py`).
2. **Jamais de donnée inventée.** Chaque cours, volume, news ou signal stocké porte `source`, `market_timestamp` (horodatage de marché, UTC), `received_at`, `processed_at`, `price_type` ∈ {`last`, `bid`, `ask`, `close`, `theoretical_open`} et `data_status` ∈ {`realtime`, `delayed`, `eod`, `stale`, `unavailable`}. **L'âge de téléchargement ne masque jamais le retard de marché** : une donnée différée de 15 min reste différée même récupérée à l'instant. Une proposition d'entrée intraday exige `now − market_timestamp ≤ data.buy_intraday_max_market_age_minutes` ; sinon elle est marquée « à vérifier » et n'a pas de fiche d'ordre. En cas d'indisponibilité, afficher « indisponible » — jamais une estimation.
3. **Le LLM ne calcule pas.** Les prix, stops, tailles, scores techniques et fiscaux sont calculés en Python pur, testés unitairement. Le LLM (API Claude) ne fait que classer/résumer/rédiger, avec sortie JSON validée par Pydantic, température 0, cache par hash de prompt, plafond de dépense journalier.
4. **Dates et heures** : stockage en UTC (`timestamptz`), planification en `Europe/Paris` (sauf jobs déclarés dans un autre fuseau), affichage en `Indian/Reunion`. Tout job marqué `market_days_only: true` vérifie le calendrier de marché (`market_calendar`) avant de tourner ; les jobs système (`backup`, `universe_refresh`) tournent tous les jours.
5. **Paramètres datés** : fiscalité, coûts SRD, seuils de risque et de détection viennent de `config/params.yaml` avec une date d'effet. Aucun pourcentage fiscal en dur dans le code.
6. **Secrets** : uniquement dans `.env` (gitignoré). Jamais dans le code, les logs, les tests ou les messages Telegram.
7. **Risque** : la taille de position est calculée sur le **prix maximal d'entrée** (prix limite, non dépassable) et sur le **stop de sortie glissé** (`stop × (1 − risk.slippage_pct)` pour un stop à seuil) : `shares = floor((capital × risk.max_risk_per_trade_pct − frais_estimés) / R_par_action_eur)` avec `R_par_action_eur = (prix_max − stop_exec) × taux_de_change × (1 + risk.fx_haircut si devise ≠ EUR)` — la marge de change **réduit** la quantité, jamais le budget de risque. Recalcul au prix réellement exécuté. Le risque **courant** (borné à zéro par position) plus le risque **réservé** des ordres d'entrée en attente ne dépasse jamais `risk.max_open_risk_pct` (définitions déterministes : docs/07 §7.3). L'exposition et le levier SRD sont plafonnés par `params.yaml`. Une proposition qui viole une règle est affichée avec la mention `BLOQUÉ : <règle>`.
8. **Une source unique par règle** : les valeurs numériques vivent dans `config/params.yaml` ; les documents les citent par clé. En cas de divergence entre documents, l'ordre de priorité est `config/params.yaml` > `docs/07` > `docs/06` > autres, et la divergence est **signalée** dans `docs/15-status.md`, jamais résolue silencieusement. La validation de configuration est **bloquante par fonctionnalité** : un groupe de paramètres manquant désactive la fonctionnalité concernée (ex. `tax.*` absent → page fiscale désactivée) sans empêcher le suivi des positions et des stops ; les groupes requis au démarrage sont ceux de `config_validation.required_groups` (`timezones`, `capital`, `risk`, `accounts`, `brokers`, `notifications`, `data`, `jobs`).
9. **Un ordre saisi n'est pas une position.** Deux états indépendants par position : **exécution** (`order_entered` → `filled_partial` → `filled`) et **protection** (`unprotected` → `partially_protected` → `protected`), suivis par quantité. Une position n'existe qu'après confirmation d'exécution. Une déclaration manuelle crée une exécution **provisoire** (`declared_uid`) ; l'import courtier crée l'exécution **confirmée** (`broker_fill_id`) ; les deux sont **rapprochées** (même compte, même instrument, quantité exacte, prix ± 0,5 %, ±10 min) et conservées avec leurs deux références — jamais fusionnées par simple égalité d'identifiant. **Un changement de mode de disponibilité n'annule jamais un ordre chez le courtier** : l'outil suit les ordres et rappelle leur annulation.
10. **Instantané de décision** : toute proposition stocke les données exactes utilisées (`decision_snapshots`) ; les post-mortems se font sur cet instantané, jamais sur des données corrigées rétrospectivement.

## Stack (recommandée dans docs/03 — tu peux proposer mieux, en le justifiant dans un ADR avant de coder)

- Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, PostgreSQL 16 (instance Docker existante), APScheduler, httpx, pandas, Pydantic v2.
- UI serveur : Jinja2 + HTMX + Tailwind (CDN) + lightweight-charts. Mobile-first.
- Notifications : Telegram Bot API (python-telegram-bot) + SMTP Gmail.
- Accès distant : Cloudflare Tunnel + Cloudflare Access (aucun port ouvert).
- Tests : pytest, pytest-asyncio, fixtures OHLC figées dans `tests/fixtures/`. Lint : ruff. Types : mypy (strict sur `app/domain`).

## Commandes

```
docker compose up -d --build          # démarrer toute la pile
docker compose logs -f app            # logs applicatifs
uv run pytest -q                      # tests (obligatoire avant tout commit)
uv run ruff check . && uv run mypy app/domain
uv run alembic upgrade head           # migrations
uv run python -m app.cli run-job <job_name> --date 2026-09-09   # rejouer un job
uv run python -m app.cli backfill --provider eodhd --from 2016-01-01
```

## Structure du dépôt

```
app/
  domain/        # logique métier pure (univers, détecteurs, scoring, risque, fiscal) — sans I/O
  data/          # providers (eodhd, saxo, tradingview_screener, yfinance, rss, imap) derrière une interface commune
  scheduler/     # jobs APScheduler, calendrier, watchdog
  llm/           # prompts versionnés, schémas de sortie, cache, budget
  notify/        # telegram, email ; abstraction notify(event, level)
  api/           # routes FastAPI (JSON)
  web/           # templates Jinja2/HTMX, static
  cli.py
config/params.yaml
docs/            # cahier des charges + ADR/
tests/{unit,integration,fixtures}/
scripts/         # check.ps1, backup.ps1, smoke_remote.ps1
```

## Conventions

- Code, identifiants, commits : anglais. UI, rapports, messages Telegram, docs : français.
- Une fonction de domaine = entrée/sortie typées, pas d'accès réseau ni base. Les providers retournent des DTO Pydantic.
- Chaque détecteur de signal est une classe avec `name`, `params`, `detect(df, ctx) -> list[Signal]` et un test sur fixture.
- Chaque job planifié écrit une ligne dans `jobs_runs` (start, end, status, error, rows) et pinge le watchdog.
- Migrations Alembic pour tout changement de schéma. Jamais de `DROP` sans ADR.
- Messages Telegram ≤ 12 lignes, boutons pour les actions (Vu / Watch / Ordre saisi / Exécuté / Ignorer). Jamais de spam : déduplication par (`instrument`, `compte`, `signal_type`, `jour`) — **jamais appliquée entre incidents P1 distincts** (seuil de stop franchi, quantité exécutée non protégée, couverture, catalyseur négatif sur position) ; un même incident est regroupé (première alerte, puis rappel toutes les `notifications.p1_repeat_minutes`).
- Calendrier **par place** (`market_calendar` par `mic`) : un job qui traite un instrument utilise le calendrier de sa place, pas celui d'Euronext Paris.
- Mode de disponibilité (`disponible` / `reunion` / `absent`) lu avant toute alerte ou proposition (docs/02 §2.3).

## Définition de « terminé » pour une tâche

1. Tests unitaires verts + test d'intégration du job concerné sur fixture.
2. `ruff` et `mypy` propres.
3. Le comportement est visible : soit une page UI, soit un message Telegram de test, soit une ligne de rapport.
4. `docs/ADR/` mis à jour si une décision a été prise ; `config/params.example.yaml` mis à jour si un paramètre a été ajouté.
5. Le critère d'acceptation correspondant de `docs/15` est coché dans `docs/15-status.md`.

## Ce que tu dois faire en début de session

1. Lire `CLAUDE.md`, `docs/00`, `docs/15-status.md`, puis le document de la phase demandée.
2. Proposer un plan court (fichiers touchés, tests prévus, risques) et attendre validation.
3. Coder par petits incréments, lancer les tests, committer avec un message explicite.
4. En fin de session : résumer ce qui est fait / non fait / à décider dans `docs/15-status.md`.

---

# 00 — Vision, objectifs, périmètre

## 0.1 Point de départ (ce qui existe et fonctionne)

Aujourd'hui, la gestion quotidienne repose sur un projet Claude (« Investissement Bourse ») qui applique chaque jour une mécanique stable :

- une grille de **scoring sur 100** (Catalyseurs & news 0–30 ; Technique/momentum 0–25 ; Fondamentaux 0–20 ; Risque/volatilité/agenda 0–15 ; Contexte secteur/macro 0–10) ;
- des règles d'interprétation : **BUY** ≥ 75 avec risque faible/moyen, catalyseur clair et entrée cohérente ; **WATCH** 60–74 ; **SELL/REDUCE** sur thèse invalidée, stop cassé, risque excessif ou meilleure opportunité ; **HOLD** ; **NO TRADE** ;
- une gestion du risque : **0,75 % du capital pilote par trade** (300 € pour 40 000 €), taille = risque / distance entrée–stop, 5 à 15 positions, exposition 60–80 %, cash conservé en cas d'incertitude, mention explicite du cash additionnel (jusqu'à 90 000 €) pour les opportunités exceptionnelles ;
- un **rapport quotidien en 10 rubriques** (macro, marchés, portefeuille, BUY, ventes/allègements, watchlist, valeurs à éviter, plan d'action, ordres manuels, risques globaux) ;
- des sources de signaux : **newsletters Zonebourse (~10h Paris)** et **lettre Momentum de Capital (~13h Paris)**.

Cette mécanique est conservée. L'outil l'industrialise et l'enrichit ; il ne la remplace pas. Les horizons du projet (intraday, quelques jours, swing 2–6 semaines, moyen terme) sont conservés dans la nomenclature (`days`, `swing_2_6w`, `medium`) ; l'intraday pur n'est pas ciblé en v1 (incompatible avec 1 h/jour), mais un D1 sorti le jour même reste possible.

## 0.2 Les deux problèmes à résoudre

1. **Rareté des opportunités sur le CAC 40** par périodes → élargir l'univers à **toutes les places éligibles au PEA** (Euronext Paris/Amsterdam/Bruxelles/Lisbonne/Milan/Oslo/Dublin, Xetra, Madrid, Vienne, Nasdaq Nordic, Varsovie en option) avec un filtre de liquidité strict (voir docs/05).
2. **Signaux trop tardifs** : les newsletters arrivent à 10h et 13h, alors que le gap haussier s'est souvent formé à 9h00 et est en partie absorbé → l'outil doit **détecter lui-même** les opportunités dès la pré-ouverture (communiqués 7h–9h) et dans les premières minutes après 9h00, et proposer des **points d'entrée sur les valeurs momentum déjà sous surveillance**. Les newsletters deviennent une source de **confirmation et de watchlist**, plus le seul déclencheur.

## 0.3 Objectifs (dans l'ordre)

| # | Objectif | Comment l'outil y contribue | Indicateur de succès (mesuré par l'outil) |
|---|---|---|---|
| 1 | Maximiser les gains | Détection précoce, univers élargi, sélection par score, entrées sur pullback des leaders momentum | Espérance par trade (R), profit factor, performance vs CAC 40 / STOXX 600, **gain de précocité** (écart entre le prix d'entrée obtenu et le prix au moment de la newsletter) |
| 2 | Minimiser les pertes | Stops systématiques, taille par le risque, filtre de régime, coupe-circuit, interdiction de trade avant publication | Perte moyenne en R ≤ 1,1 R, drawdown max, % de trades sans stop = 0 |
| 3 | ≤ 1 h de travail manuel/jour | Disponibilité de l'utilisateur intégrée au moteur (modes disponible / réunion / absent), écran « 3 décisions du jour », entrées préparées la veille (ordres à plage de déclenchement), fiches d'ordre prêtes à saisir, déclaration des exécutions en 2 clics | Temps déclaré par créneau (saisie optionnelle), nombre d'actions manuelles/jour, propositions par jour ≤ 3 |
| 4 | Utilisable à distance | Web mobile-first derrière Cloudflare Access + bot Telegram ; PC pro sans droits admin = navigateur uniquement | Disponibilité ≥ 99 % en heures de marché, temps de chargement mobile < 2 s |
| 5 | Pas de passage d'ordre automatique | Connecteurs courtiers en lecture seule, fiches d'ordre manuelles | Test automatisé « aucun endpoint de trading » |
| 6 | Gestion des risques par les stops | Stop initial obligatoire sur toute proposition, suivi du stop, alerte de franchissement, rappel de saisie du stop chez le courtier | 100 % des positions ont un stop enregistré et un stop « courtier » confirmé |
| 7 | SRD pour l'efficacité de trésorerie, PEA pour la fiscalité | Routage de chaque opportunité vers le compte le plus adapté (docs/08), suivi du calendrier de liquidation, coût du SRD intégré au calcul | Coût SRD/an, part des gains réalisés en PEA, taux de couverture jamais < seuil |

## 0.4 Périmètre fonctionnel, par priorité d'évolution

1. **P1 — Aide à l'investissement et identification d'opportunités** (docs/04 à 09) : univers, données, détecteurs, scoring, risque, comparaison des comptes, fiches d'ordre, rapports, alertes. **C'est le MVP** — livré en deux temps (docs/15) : d'abord le portefeuille opérationnel, les stops et le risque, puis les entrées préparées (pullbacks et cassures, ordres J+1 à plage de déclenchement) ; la détection d'ouverture (gaps) n'arrive qu'après validation des flux temps réel, et seulement en mode « disponible ».
2. **P2 — KPI de suivi des gains et pertes** (docs/10) : import des positions/exécutions, P&L réalisé/latent par compte, stratégie, source, période ; coûts (courtage, CRD, TTF) ; comparaison aux indices.
3. **P3 — KPI de suivi fiscal** (docs/11) : PMP, plus/moins-values CTO, moins-values reportables, estimation d'impôt paramétrée, PEA (ancienneté, versements, 5 ans), fait générateur SRD.
4. **P4 — Analyse et retour d'expérience** (docs/12) : journal, R-multiples, MAE/MFE, expectancy par détecteur/source/heure, post-mortem des signaux non pris, revue hebdomadaire assistée par LLM.

Hors périmètre (v1) : produits dérivés, crypto, vente à découvert, passage d'ordre, multi-utilisateurs, hébergement cloud.

## 0.5 Utilisateur et contraintes

- Un seul utilisateur, à La Réunion (UTC+4, pas d'heure d'été). Paris = UTC+2 (été) / UTC+1 (hiver) → l'ouverture de 9h00 Paris tombe à **11h00 (été) / 12h00 (hiver) heure Réunion**, la clôture 17h30 Paris à **19h30 / 20h30**. Wall Street ouvre à 15h30 Paris = 17h30/18h30 Réunion.
- Travail en journée ; PC professionnel **sans droits administrateur** → accès uniquement par navigateur (et sous réserve du proxy d'entreprise), smartphone personnel pour Telegram.
- Hébergement sur le PC personnel Windows (Docker Desktop déjà utilisé, PostgreSQL et n8n déjà en place), allumé toute la journée. Pas de VPS (coût, bugs vécus).
- Comptes : **PEA chez BoursoBank**, **compte-titres avec SRD chez Saxo Banque**.
- Budget : abonnements données/newsletters acceptés sans plafond strict si la valeur est démontrée (docs/04 justifie chaque ligne).
- Développement : Claude Code, par phases, avec tests.

## 0.6 Ce que l'outil n'est pas

- Pas un robot de trading : il n'exécute rien, il n'a pas accès aux ordres.
- Pas un oracle : il sépare **faits** (données sourcées), **sources externes** (newsletters, analystes), **analyse** (calculs), **opinion** (rédaction LLM) et **incertitudes** (données manquantes, publication imminente).
- Pas un backtester institutionnel : la validation se fait par **historisation de tous les signaux** et réconciliation à J+5/J+20 (docs/12), pas par optimisation de paramètres.
- Pas un outil qui pousse à investir : 5 positions et 60 % d'exposition sont des repères, jamais des objectifs ; l'absence de proposition est un résultat normal.

---

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

---

# 02 — Journée type, créneaux utilisateur et planning des jobs

Toutes les heures sont données en **heure de Paris** (planification) puis en **heure Réunion** (été / hiver). L'outil affiche toujours les deux dans l'UI et dans Telegram.

## 2.1 Cartographie horaire des sources (jour ouvré Euronext)

| Paris | Réunion été / hiver | Événement | Source (canal) |
|---|---|---|---|
| 05:00–06:00 | 07:00 / 08:00 | Analyses techniques de la veille au soir (Tradosaure, blogs) | Blog/RSS |
| 06:00 | 08:00 / 09:00 | Briefing marchés allemands (morningcrunch), agenda du jour | E-mail |
| 07:00–08:30 | 09:00 / 10:00 → 10:30 / 11:30 | **Communiqués de sociétés avant bourse** (résultats, contrats, guidance) | RSS ActusNews, GlobeNewswire, Euronext, Boursorama « Communiqués » |
| 07:15 | 09:15 / 10:15 | Ouverture de la pré-ouverture Euronext (carnet, prix théorique d'ouverture) | Flux primaire (Saxo si abonné) |
| 08:16 | 10:16 / 11:16 | ABC Bourse « Les actions à suivre » | RSS |
| 08:17–08:30 | | Bourse Direct « Ce Matin », Morning Meeting | Web/e-mail |
| 08:30 | | Décision Bourse (lettre quotidienne), notes macro | E-mail |
| 08:37 | | ABC Bourse « CAC 40 attendu » (pré-ouverture) | RSS |
| 08:46 | | TradingSat/BFM Bourse recommandations d'analystes | RSS |
| 08:50–09:00 | | Enchère d'ouverture Xetra | Flux |
| **09:00** | **11:00 / 12:00** | **Fixing d'ouverture Euronext Paris, début du continu** | Flux temps réel (EODHD WS / Saxo) |
| 09:02–09:10 | | Boursorama/AOF « Valeurs à suivre à Paris » | Web |
| 09:05 / 09:15 / 09:30 | 11:05 / 12:05 … | **Scans d'ouverture de l'outil** (gap, RVOL, opening range) | Outil |
| 09:26 | | Premières recommandations de brokers relayées (ABC Bourse) | RSS |
| ~10:00 | 12:00 / 13:00 | Newsletter Zonebourse (constat utilisateur, non vérifié officiellement) | E-mail (IMAP) |
| ~13:00 | 15:00 / 16:00 | Lettre Momentum de Capital (constat utilisateur) | E-mail (IMAP) |
| 14:30 | 16:30 / 17:30 | Statistiques US | RSS |
| 15:30 | 17:30 / 18:30 | Ouverture de Wall Street | Flux |
| 17:30–17:35 | 19:30 / 20:30 | Pré-clôture puis **fixing de clôture** Euronext | Flux |
| 17:35–17:40 | | Trading at last | Flux |
| 18:00–18:30 | 20:00 / 21:00 | Lettres du soir (Décision Bourse 18:30, Börse-Intern 18:00) | E-mail |
| 22:00 | 00:00 / 01:00 | Clôture US | Flux |

## 2.2 Les trois créneaux utilisateur (≤ 1 h cumulée)

| Créneau | Paris | Réunion | Durée cible | Ce que l'utilisateur fait | Ce que l'outil a préparé |
|---|---|---|---|---|---|
| **C1 — Brief pré-ouverture** | 08:45 | 10:45 / 11:45 | 10 min | Lit le brief Telegram/e-mail, valide la « liste d'ouverture » (valeurs à surveiller à 9h00), ajuste les stops si demandé | Brief : régime, positions et distance aux stops, catalyseurs du matin classés, valeurs candidates avec niveaux d'entrée/stop/taille pré-calculés, agenda (résultats, liquidation SRD) |
| **C2 — Ouverture** (uniquement en mode `disponible`, phase 3) | 09:05–09:45 | 11:05 / 12:05 → 11:45 / 12:45 | 15 min (fractionné) | Réagit aux alertes BUY/WATCH (fiche d'ordre prête), saisit éventuellement un ordre chez le courtier, confirme « Ordre saisi » puis « Exécuté » avec quantité et prix | Alertes à 09:05 / 09:15 / 09:30 (gap + RVOL + catalyseur), niveaux d'entrée (cassure de l'opening range), prix max, stop, taille, comparaison des comptes |
| **C3 — Revue du soir** (créneau principal) | 17:45–18:30 | 19:45 / 20:45 → … | 20–30 min | Lit l'écran « 3 décisions du jour », puis le rapport (10 rubriques) en second niveau ; saisit les ordres du soir (stops, **entrées préparées pour le lendemain à plage de déclenchement** : D2/D3/D4), renseigne le journal | Rapport complet, réconciliation newsletters ↔ signaux propres, mise à jour des stops (trailing), ordres à plage de déclenchement proposés pour J+1 avec prix max, positions à alléger, agenda J+1 |
| **Hebdo (samedi)** | — | 30–45 min | 1×/semaine | Revue de la semaine, décisions sur les règles | Digest hebdo : KPI, expectancy par détecteur/source, signaux ratés, comportement, propositions d'ajustement (ADR) |

Si l'utilisateur est indisponible sur C2 (réunion), les alertes restent consultables et les propositions **expirent automatiquement** 30 minutes après leur scan et au plus tard à 11:00 Paris (paramètres) : une entrée sur gap au-delà de ce délai n'est plus proposée comme BUY (le gap est absorbé), elle bascule en WATCH avec niveau de pullback.

## 2.3 Mode de disponibilité (règle du moteur, dès le MVP)

L'utilisateur travaille en journée : la disponibilité est une **donnée d'entrée du moteur**, pas une hypothèse. Le mode courant est fixé par un planning hebdomadaire par défaut (`availability.schedule`, en heure Réunion) et modifiable à tout moment par Telegram (`/mode disponible|reunion|absent [durée]`) ou depuis le dashboard.

| Mode | Comportement du moteur | Alertes |
|---|---|---|
| `disponible` | Toutes les propositions, y compris celles exigeant une décision immédiate (D1 à l'ouverture, phase 3) ; au plus **3 propositions prioritaires** par créneau, classées par qualité d'entrée | P1 + P2 (quota `availability.quotas.disponible`) + P3 groupé |
| `reunion` | Surveillance et scénarios de repli seulement : aucune proposition exigeant une réaction rapide ; les candidats D1 sont enregistrés (post-mortem) et convertis en WATCH « pullback » ; les ordres d'entrée déjà saisis chez le courtier **restent actifs** (le mode ne les annule pas) et sont suivis : une exécution sans protection est une **P1 dans tous les modes** (docs/07 §7.4) | P1 en temps réel (dont « exécuté sans protection ») ; P2 différées au prochain créneau |
| `absent` (dont vacances) | Gel des nouvelles propositions ; suivi des risques, des échéances (liquidation SRD, résultats) et des protections ; rappels de stops | P1 uniquement ; digest quotidien e-mail |

Le mode `absent` prolongé remplace le « mode vacances » ; les stops restent chez le courtier, l'outil ne fait que surveiller. **Ordres d'entrée préparés pour J+1 et disponibilité** : la règle unique est en docs/07 §7.4 — entrée préparée comme ordre seulement avec un ordre à plage de déclenchement ou un ordre lié ; protection liée acceptable dans tous les modes, sinon ordre proposé seulement si le mode `disponible` est prévu sur la fenêtre d'exécution ou si l'utilisateur accepte explicitement le risque (tracé) ; à défaut, alerte « à saisir manuellement ». **Heure d'été** : l'ouverture de Paris tombe à 11:00 Réunion en été et 12:00 en hiver ; le planning par défaut contient un créneau **relatif à l'ouverture** (`ref: market_open`) à confirmer par l'utilisateur ; s'il n'est pas confirmé, les scans D1 d'ouverture sont volontairement hors créneau en été (choix explicite, affiché sur `/sante`). La page principale répond à une seule question : **« Quelles sont les trois décisions utiles aujourd'hui ? »** (docs/09 §9.5) ; le rapport en dix rubriques reste accessible en second niveau. Les détecteurs préparés la veille (D2/D3/D4, ordres J+1 à plage de déclenchement) sont privilégiés ; la détection d'ouverture (D1) n'apporte un avantage que si l'entrée reste exécutable dans ces contraintes, d'où son activation en phase 3, en mode `disponible` seulement.

## 2.4 Planning des jobs (planificateur en `Europe/Paris`, jours ouvrés de la place concernée)

| Heure Paris | Job | Contenu | Sortie |
|---|---|---|---|
| 06:30 (lundi, tous les lundis même fériés) | `universe_refresh` | Recalcul de l'univers, éligibilité PEA/SRD, liquidité, index membership, secteurs, prochaines dates de résultats | Table `instruments`, diff versionné |
| 06:45 | `eod_backfill_check` | Charge l'EOD **officiel** de la veille (EODHD) pour tout l'univers, remplace les clôtures provisoires, recalcule `features_daily` si écart, relance si trou | `prices_eod`, `jobs_runs`, alerte si trou |
| 07:00 → 09:00 (toutes les 5 min), puis 09:00 → 19:00 (toutes les 15 min) | `news_scan` | RSS communiqués et recos (ActusNews, GlobeNewswire, ABC Bourse, TradingSat…) + classification LLM (type, sens, ampleur, surprise) ; couvre les communiqués du soir (17:40–19:00) pour le brief du lendemain | `news_items` classés, `premarket_watch` |
| 07:30 | `daily_regime` | Régime de marché (indices, breadth), volatilité (VSTOXX si dispo), futures | `market_regime` |
| 07:30 → 19:00 (toutes les 10 min) | `imap_poll` | Lecture de la boîte dédiée : lettres du matin (Décision Bourse 08:30, Bourse Direct, ABC Premium), Zonebourse (~10:00), Momentum Capital (~13:00), lettres du soir (18:30) ; extraction → signaux externes ; heure de réception, `p_open`, `p_recv`, `drift_since_open` | `newsletter_items`, `signals(detector=external)` |
| 08:30 | `zonebourse_lists_daily` (si Premium) | Lecture 1×/jour des listes Momentum Europe/USA → watchlist momentum (source tracée) | `watchlist` |
| **08:45** | **`brief_premarket`** | Backfill REST intraday 5 min (référence RVOL 20 j) pour la liste d'ouverture ; brief C1 : régime, positions/stops, catalyseurs, liste d'ouverture (≤ 15 valeurs) avec niveaux pré-calculés, agenda | Telegram + e-mail + page `/brief` |
| 08:55 | `intraday_watch_start` | Démarre les souscriptions temps réel (WebSocket) sur : positions + liste d'ouverture + watchlist momentum (≤ 50 × n connexions, ≤ 150 symboles) | Flux `prices_intraday` |
| 09:05, 09:15, 09:30, 10:00, 10:30 | `open_scan` (**phase 3**, mode `disponible` seulement) | Détecteurs **D1** (gap+catalyseur), **D2 intraday** (cassure de pivot), **D4 reprise** (cassure du plus haut de la veille) — candidats sur l'univers via screener différé (≤ 6 requêtes, statut « à vérifier »), fiches d'ordre uniquement sur cotation temps réel (liste d'ouverture) ; scoring ; risque ; alerte immédiate, argumentaire LLM ajouté ensuite | Alertes BUY/WATCH Telegram, page `/opportunites` |
| 09:00 → 17:40 (toutes les 1 min sur positions, 5 min sur watchlist) | `position_monitor` | Distance au stop, seuil franchi (P1 « vérifier l'exécution », puis SELL au marché si non confirmée sous 5 min), objectif atteint (alerte REDUCE/trailing), volume anormal sur position | Alertes, `stops` |
| 09:00 → 17:40 (toutes les 5 min) + après chaque déclaration + avant toute proposition | `portfolio_sync_intraday` | Saxo (lecture) : positions, ordres en attente (états, exécutions partielles, stops), cash, engagements → `portfolio_state` daté | `portfolio_state`, rapprochements provisoires |
| 12:00 | `midday_digest` (silencieux si rien) | Synthèse des alertes du matin non traitées, positions | Telegram si événement |
| 15:35 | `us_open_scan` (phase 6) | Scan US sur watchlist restreinte | Alertes |
| **17:50** | **`eod_pipeline`** | Clôtures **provisoires** (screener 17:45 + REST différé, statut D15, réconciliées à 06:45) → détecteurs D2/D3/D4 EOD → scoring → SELL/REDUCE (thèse invalidée, trailing) → routage compte → plan J+1 → rapport 10 rubriques (rédaction LLM sur données calculées) | Telegram (résumé) + e-mail (rapport) + page `/rapport` |
| 19:00 | `portfolio_sync` | Saxo OpenAPI (lecture) : rapprochement complet du jour (exécutions déclarées ↔ confirmées, stops, cash) ; rappel d'import CSV BoursoBank si > 7 jours | `positions`, `trades`, `cash_snapshots` |
| 23:00 **Réunion** (tous les jours) | `backup` | `pg_dump` + export CSV des tables métier vers dossier synchronisé | Fichier daté |
| Samedi 08:00 | `weekly_review` | KPI, expectancy, post-mortem des signaux, comportement, digest LLM | E-mail + page `/revue` |
| Chaque job | `watchdog_ping` | Ping healthchecks.io / Uptime Kuma ; alerte si un job attendu n'a pas tourné | Telegram + e-mail |

Règles : les jobs marqués `market_days_only: true` vérifient le calendrier **de la place de chaque instrument traité** (un scan tourne pour Paris et saute Xetra le 24/12 ; un job sans instrument, comme `brief_premarket`, tourne si au moins une place P0 est ouverte) ; `backup`, `universe_refresh`, `weekly_review` tournent toujours ; les demi-séances (24 et 31 décembre) décalent `eod_pipeline` à 14:20 et arrêtent `position_monitor` à 14:10 ; un job manqué (PC redémarré) est rejoué s'il est encore utile (`misfire_grace_time` par job : 10 min pour `open_scan`, 4 h pour `eod_pipeline`).

## 2.5 Ce que l'utilisateur saisit (et rien d'autre)

1. **Ordres et exécutions** : bouton « Ordre saisi » (l'ordre existe chez le courtier, non exécuté) puis « Exécuté » → quantité et prix (pré-remplis depuis la fiche d'ordre, modifiables) ; ou synchronisation Saxo (en séance toutes les 5 min, après chaque déclaration, rapprochement le soir) ; ou import CSV BoursoBank. Une position n'est créée qu'à l'exécution confirmée.
2. **Protection** : confirmation « Stop saisi chez le courtier » (type, niveau, quantité) — toute quantité exécutée non protégée = P1 immédiate dans tous les modes, rappelée toutes les 15 min.
3. **Décisions** sur les propositions : Vu / Watch / Ignorer (avec motif optionnel, 1 clic) — alimente le journal et les statistiques de comportement.
3 bis. **Mode de disponibilité** : `/mode` Telegram ou dashboard (§2.3).
4. **Cash disponible** par compte, si l'import automatique n'est pas possible (BoursoBank).
5. **Événements exceptionnels** : blocage temporaire (« pas de nouvelles entrées cette semaine »), capital pilote, cash additionnel autorisé.

---

# 03 — Architecture cible (contraintes imposées, recommandation argumentée)

L'utilisateur laisse Claude Code choisir la stack. Ce document fixe donc les **contraintes** (obligatoires) et donne une **architecture de référence** (recommandée, à retenir sauf meilleure proposition consignée dans un ADR).

## 3.1 Contraintes imposées

| # | Contrainte | Justification |
|---|---|---|
| A1 | Hébergement sur le PC personnel Windows 10/11 avec Docker Desktop (WSL2), PostgreSQL déjà présent en conteneur | Pas de VPS (coût, bugs vécus) ; existant réutilisé |
| A2 | Accès distant **sans installation côté client** (navigateur seul sur PC pro sans droits admin ; smartphone) ; **aucun port ouvert** sur la box ; authentification avant d'atteindre l'application | Sécurité, CGNAT possible chez les FAI réunionnais, PC pro verrouillé |
| A3 | Notifications : Telegram (smartphone) + e-mail (repli lisible depuis le PC pro) | Choix utilisateur |
| A4 | Connecteurs courtiers **en lecture seule** ; aucun endpoint de trading dans le code | Objectif 5 |
| A5 | Toute logique métier (détecteurs, scoring, risque, fiscal) en **code pur testable**, indépendant de l'UI et des providers | Testabilité avec Claude Code, robustesse |
| A6 | Providers de données derrière une **interface unique** avec statut de donnée et bascule ; aucune source non officielle en dépendance critique | Fragilité constatée (yfinance, scraping) |
| A7 | Fuseaux : stockage UTC, planification `Europe/Paris`, affichage `Indian/Reunion` ; calendrier de marché en base | Utilisateur à La Réunion, marché à Paris |
| A8 | Reprise automatique après redémarrage du PC ; watchdog externe ; sauvegarde quotidienne | Objectif 4 (disponibilité) |
| A9 | LLM : API Claude (Anthropic), sorties structurées, cache, budget ; pas de dépendance à un GPU local | Coût faible (≈ 4–5 $/mois estimés), fiabilité |
| A10 | Un seul dépôt, un seul `docker compose`, démarrage en une commande | Maintenabilité par un non-développeur assisté |

## 3.2 Architecture de référence

```
[Smartphone / PC pro : navigateur]  ──HTTPS──▶  Cloudflare Edge (Access : OTP e-mail + Google, 1 seule adresse autorisée)
[Smartphone : Telegram]             ◀──────────  Telegram Bot API (long polling sortant, pas de webhook entrant)
                                                          │ connexion sortante uniquement
PC Windows personnel — Docker Desktop — docker compose (réseau interne, restart: unless-stopped)
 ├─ cloudflared   : tunnel nommé  → http://app:8000   (aucun port publié sur l'hôte)
 ├─ app (Python)  : FastAPI (API JSON + pages Jinja2/HTMX) + APScheduler dans le même processus (lifespan)
 │                  ├─ /                 dashboard mobile-first
 │                  ├─ /brief /opportunites /positions /rapport /journal /kpi /fiscal /parametres /sante
 │                  ├─ /api/…           JSON (Pydantic), OpenAPI auto
 │                  └─ /health /jobs    watchdog, état des jobs, fraîcheur des données
 ├─ postgres (existant) : schéma `bourse` (voir docs/13)
 ├─ n8n (existant, optionnel) : uniquement ingestion IMAP/RSS → POST /api/ingest ; aucune logique métier
 └─ uptime-kuma (optionnel) ou healthchecks.io (externe, gratuit 20 checks) : surveillance des jobs
Externe : EODHD (REST + WebSocket), Saxo OpenAPI (lecture), TradingView screener (différé), RSS, IMAP Gmail,
          API Anthropic (Haiku pour classer, Sonnet pour rédiger), SMTP Gmail.
```

### Choix argumentés

- **FastAPI + Jinja2/HTMX + Tailwind (CDN) + lightweight-charts** plutôt que Streamlit (relance tout le script à chaque interaction, lourd sur mobile derrière un tunnel), NiceGUI (valable, mais moins standard pour les tests HTTP) ou React/Next (second écosystème à maintenir). Un seul langage, pages légères en 4G, testable avec `TestClient`. lightweight-charts (Apache 2.0) exige l'attribution TradingView sur les graphiques.
- **APScheduler (jobstore Postgres) dans le processus `app`** plutôt que Celery/RabbitMQ/Redis : un seul worker suffit ; scinder `worker`/`web` en deux conteneurs uniquement si les scans ralentissent l'UI (même image, commande différente).
- **n8n conservé mais cantonné à l'ingestion** (IMAP, webhooks) : il fait bien ce travail et existe déjà ; la logique de scoring reste en Python testable. Alternative : `imaplib`/`aioimaplib` directement dans `app` (plus simple à tester) — au choix de Claude Code, à consigner en ADR.
- **Cloudflare Tunnel + Access** (plan Zero Trust gratuit ≤ 50 utilisateurs, vérifié 09/2026) sur **un domaine personnel** (`bourse.<mondomaine>`) : zéro port ouvert, indépendant du CGNAT, authentification en amont (OTP e-mail 10 min), jeton `Cf-Access-Jwt-Assertion` vérifié côté FastAPI. Les sous-domaines fournisseurs (`*.trycloudflare.com`, `*.ts.net`, `*.ngrok.io`) sont plus souvent filtrés par les proxys d'entreprise : **test préalable obligatoire depuis le PC pro** (phase 0). Plan B : Tailscale Funnel + authentification applicative (TOTP).
- **Telegram en long polling** (connexion sortante) : pas de webhook entrant, donc pas d'exposition supplémentaire. Boutons inline pour Vu / Watch / Ordre saisi / Exécuté / Stop saisi / Ignorer → écriture en base, jamais d'ordre.
- **PostgreSQL** (existant) pour tout ; Parquet dans un volume pour les historiques intraday volumineux si nécessaire.

## 3.3 Modules applicatifs (correspondance avec les documents)

| Module (`app/…`) | Rôle | Spec |
|---|---|---|
| `data/providers/` | `MarketDataProvider` (EOD, intraday, temps réel), `NewsProvider`, `NewsletterProvider`, `BrokerReadProvider` ; implémentations `eodhd`, `saxo`, `tradingview_screener`, `yfinance`, `rss`, `imap`, `csv_boursobank` | docs/04 |
| `domain/universe` | Construction de l'univers, éligibilité PEA/SRD, liquidité, indices | docs/05 |
| `domain/indicators` | Définitions testées : ATR, ADR, RVOL, RS, MM, opening range, gap… | docs/06 annexe |
| `domain/detectors` | D1…D6 + détecteurs de sortie | docs/06 |
| `domain/scoring` | Grille /100, mapping BUY/WATCH/… | docs/06 |
| `domain/regime` | Feu tricolore de marché | docs/06 |
| `domain/risk` | Sizing, stops, exposition, levier SRD, coupe-circuit, portes de discipline | docs/07 |
| `domain/accounts` | Comparaison PEA / CTO comptant / SRD, coûts SRD, calendrier de liquidation, équité avec engagements SRD | docs/08 |
| `domain/orders` | Fiches d'ordre, machine à états ordre → exécution → protection, expiration, idempotence des exécutions | docs/07, 09 |
| `domain/availability` | Mode de disponibilité (planning + overrides), effets sur alertes et détecteurs | docs/02 §2.3 |
| `domain/portfolio` | Positions, exécutions, P&L, coûts | docs/10 |
| `domain/tax` | PMP, plus/moins-values, estimation d'impôt paramétrée, PEA | docs/11 |
| `domain/journal` | R-multiples, expectancy, MAE/MFE, post-mortem, comportement | docs/12 |
| `llm/` | Prompts versionnés (`app/llm/prompts/*.md`, distincts du dossier `prompts/` de la spec), schémas, cache, budget | docs/06, 09, 12 |
| `scheduler/` | Jobs, calendrier, watchdog, rejeu | docs/02, 14 |
| `notify/` | Telegram, e-mail, déduplication, quotas | docs/09 |
| `web/`, `api/` | UI et API | docs/09 |

## 3.4 Flux de données principal (du signal à la décision)

1. **Ingestion** : cours (EOD nocturne + temps réel sur liste restreinte), news (RSS 5 min), newsletters (IMAP), portefeuille (Saxo API en séance toutes les 5 min + rapprochement le soir, CSV BoursoBank hebdo).
2. **Enrichissement** : indicateurs calculés et stockés (`features_daily`, `features_intraday`), news classées par LLM (`news_items.classification`).
3. **Détection** : détecteurs → `signals` (un signal = instrument + type + horodatage + niveaux + preuves).
4. **Scoring** : `scores` (5 sous-scores + total + confiance + risque).
5. **Risque et routage** : `proposals` (action, compte recommandé, entrée, stop, objectifs, taille, nombre d'actions, blocages éventuels).
6. **Diffusion** : alertes (Telegram/e-mail), pages, rapport.
7. **Décision utilisateur** : Vu / Watch / Ordre saisi / Ignorer → `decisions` ; puis Exécuté → `trades` (déclaré, rapproché ensuite avec l'import courtier) et `positions` (protection `unprotected`) ; puis Stop saisi → `partially_protected` / `protected`. Un instantané de décision est figé à l'étape 5.
8. **Suivi** : `position_monitor` (stops, objectifs), `stops` (historique), alertes SELL/REDUCE.
9. **Apprentissage** : réconciliation J+5/J+20 des `signals` (pris ou non), KPI, revue hebdo.

## 3.5 Alternative minimaliste (si la phase 0 doit tenir en un week-end)

Un seul conteneur `app` (FastAPI + APScheduler), Postgres existant, Telegram + e-mail, Cloudflare Tunnel. Pas de n8n, pas d'Uptime Kuma (healthchecks.io gratuit suffit). EODHD seul provider payant au départ ; tradingview-screener et yfinance en secours ; Saxo API en lecture dès que la clé live est obtenue.

---

# 04 — Sources de données, API, newsletters : sélection et intégration

Tarifs et caractéristiques relevés le **9 septembre 2026** sur les pages officielles (sauf mention « à vérifier »). Tous les tarifs sont des **paramètres** (`config/params.yaml › subscriptions`) à revalider à chaque renouvellement. Principe : **un socle payant fiable + des sources gratuites en complément + des sources non officielles uniquement en secours**.

## 4.1 Données de marché (cours, volumes, historiques)

| Fournisseur | Couverture utile | Délai | Ce qu'on en tire | Prix (09/2026) | Accès | Décision |
|---|---|---|---|---|---|---|
| **EODHD — plan « EOD + Intraday »** | 70 bourses dont Paris, Amsterdam, Bruxelles, Lisbonne, Milan, Oslo, Xetra/Francfort, Madrid, Vienne, Stockholm, Copenhague, Helsinki, Varsovie, Londres | **WebSocket temps réel sur 18 marchés européens** (source Cboe Europe, annonce du 26/08/2026 : ~9 400 symboles, barres 1 min, statut de suspension, backfill REST) ; API REST « live » différée 15–20 min ; EOD | EOD historique depuis 2000 (scoring, RS, ATR) ; intraday 5 min depuis 2020 ; **flux temps réel** pour positions + liste d'ouverture ; splits/dividendes | **29,99 $/mois** (≈ 299,90 $/an) ; All-in-One 99,99 $/mois si fondamentaux + news + calendrier résultats voulus | REST + WebSocket, 100 000 appels/j, 64 connexions/token, **50 symboles par connexion WS** | **Socle primaire (P0)**. Démarrer en EOD+Intraday ; passer All-in-One en phase 3 si le calendrier de résultats et les fondamentaux justifient +70 $/mois |
| **Saxo OpenAPI** (compte CTO existant) | Euronext (L1 7 €/mois, **remboursé si ≥ 4 transactions/mois/bourse**), Xetra L1 7 €, Nasdaq Nordic 7 € (tarifs particuliers Saxo) | **Temps réel flux primaire** si abonné (champ `DelayedByMinutes`) ; sinon différé. **Aucune donnée de marché en environnement de simulation** (doc Saxo) : les tests de prix se font sur le compte réel, en lecture | **Portefeuille en lecture** (positions, ordres en attente, cash, exécutions) ; prix temps réel Euronext incluant carnet et **pré-ouverture** (prix théorique d'ouverture, à valider empiriquement) ; graphiques intraday | Abonnement données 0–7 €/mois selon activité ; API gratuite | REST + streaming ; application « usage personnel » à créer sur developer.saxo (démo d'abord, puis demande de clé live, compte financé requis, conditions à accepter) ; OAuth ; rate limits documentés | **P0 pour le portefeuille CTO, P1 pour les prix temps réel Paris**. Ne demander que les permissions de lecture ; ne jamais implémenter `trade/…/orders` |
| **tradingview-screener** (lib Python, MIT ; TradingView indique officiellement ne pas fournir d'API publique de données : le wrapper ne doit jamais être indispensable) | Marchés `france`, `germany`, `italy`, `netherlands`, `belgium`, `spain`, `portugal`, `sweden`, `denmark`, `finland`, `norway`, `austria`, `poland`… ; 3 000+ champs (`gap`, `relative_volume_10d_calc`, `change_from_open`, `premarket_change`, `Perf.3M`, `sector`, `earnings_release_next_date`…) | Différé ~15 min sans cookie de session | **Scan de tout l'univers PEA en ≤ 6 requêtes** (une par marché, `limit` ≥ 1 000 lignes) à 9h05/9h15/9h30 (gap, RVOL approx., cassures) ; classement RS quotidien ; secteur ; prochaine date de résultats | Gratuit | Endpoint non officiel, risque de bannissement si abus (≤ 6 requêtes par scan, ≥ 60 s entre scans) | **P0 en complément** (large et gratuit) ; **repli obligatoire** : EODHD REST différé sur l'univers (≈ 900 appels par scan, dans le quota) si l'endpoint casse |
| **yfinance** | Toutes les places PEA (différé 15–20 min ; Nordic temps réel) | EOD fiable, intraday limité | Backfill EOD gratuit, contrôle croisé de l'EOD EODHD | Gratuit | Lib non officielle, épisodes 429 (2025), « personal use only » | **Secours EOD uniquement** |
| **Euronext (site, Live)** | Tous marchés Euronext | Différé (usage interne autorisé) | Calendrier officiel (jours fériés, demi-séances), horaires, composition des indices (PDF), avis SRD, communiqués | Gratuit | Pages/PDF, JSON non documenté | Référentiel (calendrier, listes) |
| **ABC Bourse Premium** | Paris temps réel (source SIX), autres places différées | Temps réel Paris | Alertes pré-ouverture, « propositions d'opérations avant l'ouverture », Scoring Pro, exports CSV historiques intraday 1 min CAC 40 / 5 min SRD (abonnement annuel), 10 alertes SMS/mois | 19,90 €/mois en annuel (238,80 €/an, incohérence 228 € sur une page) ; 29 €/mois sans engagement | **Pas d'API** : e-mail/SMS parsables, fichiers CSV par e-mail | **P1 — test 1 mois** (voir §4.3) : utile pour les propositions matinales (source éditoriale) et l'historique intraday pour tests ; pas pour le flux temps réel de l'outil |
| **TradingView Essential/Plus** | Mondial | Différé sauf package bourse payant | **Alertes → webhook** (20 alertes Essential, 100 Plus) : les critères de l'utilisateur déclenchent un POST vers l'outil dès 9h00 | 12,95 $/mois (Essential, annuel) ; 29,95 $ (Plus) | Webhook HTTP (via Cloudflare Tunnel, route dédiée avec secret) | **P2 optionnel** (phase 6) |
| Twelve Data, FMP, Marketstack, Alpha Vantage, Tiingo, Polygon/Massive, Databento, dxFeed | Europe absente, EOD seulement, ou trop cher (Twelve Data Euronext = EOD ; FMP Europe = Ultimate 149 $/mois ; Databento Euronext « en développement ») | — | — | — | — | **Écartés** (réévaluer Databento en 2027) |
| Interactive Brokers (bundle Euronext 3 €/mois, API TWS) | Excellent flux primaire | Temps réel | Pré-ouverture, carnet | 3 €/mois | Nécessite un compte IBKR et TWS allumé | **Non retenu** (pas de compte ; Saxo couvre le besoin) |

## 4.2 News, communiqués, calendriers

| Source | Contenu | Heure | Canal | Décision |
|---|---|---|---|---|
| **ActusNews** (RSS) | Communiqués réglementés des sociétés françaises (mis à jour toutes les 15 min) | 07:00–08:30 surtout, et 17:40–19:00 | RSS (403 constaté sans User-Agent standard : utiliser un UA de navigateur, cadence ≥ 5 min) | **P0** |
| **EQS Newswire** (Allemagne, `eqs.com`) | Communiqués réglementés des sociétés allemandes (Xetra) | 07:00–08:30 | À qualifier (RSS / page ; conditions d'accès automatisé à vérifier) | P1 (si l'univers Xetra est activé) |
| **GlobeNewswire** (RSS par sujet/pays), **Business Wire** | Communiqués Europe (Euronext Amsterdam/Bruxelles, Nordic…) | Idem | RSS | **P0** |
| **Boursorama « Communiqués »** (`?filter=report`) | Agrégat Actusnews/AFP/Zonebourse horodaté à la minute | Continu | HTML (scraping léger, 1 requête/5 min, usage perso) | P1 (secours) |
| **ABC Bourse RSS** | « Les actions à suivre » 08:16, « CAC 40 attendu » 08:37, recommandations d'analystes 09:26, macro | Matin | RSS public | **P0** |
| **TradingSat / BFM Bourse RSS** | Recommandations d'analystes 08:46, ouverture 09:00, AT valeurs, clôture 17:59 | Matin/soir | RSS public | **P0** |
| **Bourse Direct « Ce Matin » / Morning Meeting** | Asie, agenda, valeurs, flash small/mid | 08:17–08:30 | HTML stable + e-mail | P1 |
| **Boursorama/AOF « Valeurs à suivre à Paris »** | Liste des valeurs avec news | 09:02–09:10 | HTML | P1 (confirmation post-ouverture) |
| **Dates de résultats** : P0 = champ `earnings_release_next_date` du screener TradingView + `yfinance.calendar` (recoupement) ; P2 = **EODHD Calendar API** (`earnings`, `before_after_market`) et **News API** (sentiment) | Indispensable pour la règle « pas d'entrée 48 h avant » ; date inconnue → confiance −1 et mention | Quotidien (17:50) | Screener / lib / REST (All-in-One) | **P0 dégradé, P2 complet** ; saisie manuelle possible pour les positions ouvertes |
| **API info-financière AMF** (data.gouv) | Information réglementée archivée ; déclarations de dirigeants | Quotidien (rétrospectif) | REST sans clé, 10 000 appels/j | P3 (signal secondaire « transactions de dirigeants ») |
| **Euronext** | Avis d'admission/retrait SRD, calendrier, indices | — | Pages/PDF | P0 (référentiel) |
| Marketaux (news API, 29 $/mois Basic) | News multi-langues avec entités | — | REST | Optionnel si RSS insuffisant |

## 4.3 Newsletters et sources expertes (signaux externes)

| Source | Contenu | Heure Paris | Prix (09/2026) | Exploitation | Décision |
|---|---|---|---|---|---|
| **Zonebourse (Surperformance)** — newsletters + Premium (portefeuilles Europe/USA, listes « Momentum » Europe/USA ~50 valeurs, alertes d'arbitrage e-mail/SMS temps réel, Top listes « mises à jour chaque matin ») | Sélections momentum, arbitrages | ~10:00 (newsletter, constat utilisateur) ; alertes ponctuelles ; listes le matin | Access 17 €/mois (promo, 29 € plein), Premium 29 € (49 € plein), Expert 149 € | **IMAP** pour newsletters et alertes (facile) ; listes = pages authentifiées **sans export ni API** : lecture 1×/jour maximum pour usage strictement personnel, jamais de redistribution (CGV : abonnement nominatif, usage personnel, reproduction interdite) | **P0 (existant)** — devient source de confirmation + watchlist ; **abonnement Premium recommandé** pour les alertes d'arbitrage temps réel et les listes Momentum Europe (base momentum européenne la plus large) |
| **Capital — lettre « Momentum »** (N. Gallant, CFTe) | AT + analyse, scénarios CAC 40 et actions, niveaux d'entrée/stops | ~13:00 (constat utilisateur) | Non vérifié (capital.fr inaccessible depuis l'environnement) | IMAP | **P0 (existant)** — confirmation/watchlist ; mesure de précocité |
| **ABC Bourse — flux RSS gratuits** | Actions à suivre 08:16, pré-ouverture 08:37, recos 09:26 | Matin | Gratuit | RSS | **P0** — la source structurée la plus précoce trouvée |
| **TradingSat / BFM Bourse RSS** | Recos 08:46, ouverture, AT | Matin | Gratuit | RSS | **P0** |
| **Décision Bourse — « La Lettre de la Bourse »** | Conseils FR/étranger, **lettres quotidiennes à 08:30 et 18:30** | 08:30 / 18:30 | 29 €/mois sans engagement (formules 3/6/12 mois affichées 139/249/429 € — à vérifier, l'annuel affiché dépassant 12 × 29 €) | IMAP | **P1 — test 1 mois** : seule lettre payante trouvée avec un envoi quotidien avant l'ouverture ; sérieux à évaluer par le post-mortem des signaux (docs/12) avant de prolonger |
| **ABC Bourse Premium** | Propositions « avant l'ouverture », alertes pré-ouverture SMS/e-mail, Scoring Pro | Avant 09:00 | 19,90 €/mois (annuel) | E-mail/SMS parsables | **P1 — test 1 mois** (précocité), même évaluation |
| **Investir (Les Échos) Privilège** | Conseil quotidien, newsletter du matin, portefeuilles | Matin (heure non vérifiée) | 16,90 €/mois | IMAP | P2 optionnel |
| **Bourse Direct Morning Meeting / Ce Matin** | Marché, valeurs | 08:17–08:30 | Gratuit | E-mail/HTML | P1 |
| **Tradosaure** | AT quotidienne CAC 40/valeurs, pédagogie | ~05:00 | Gratuit | RSS/Atom | P2 (contexte technique) |
| **ChartMill** | Screener technique Europe (FR/BE/NL/DE…), alertes | EOD | 34,97 $/mois | Alertes e-mail (site JS) | P3 optionnel (couverture Europe) |
| **Stockopedia** (StockRanks Momentum, 7 500 actions EU, EOD hors UK) | Rang momentum quotidien | Nuit | ~60–80 €/mois (non affiché) | Web | P3 optionnel |
| **Börsenmedien (Der Aktionär DAILY, Börse Online)**, **morningcrunch (06:00)**, **finanzen.net** | Allemagne/Xetra | Matin | Gratuit | IMAP | P2 (si l'univers Xetra produit des signaux) |
| bourse-portefeuille-conseil.fr, « C'est votre Argent » (BFM) | Arbitrages de gérants | Vendredi | Gratuit | Web/podcast | P3 (source du projet actuel, faible précocité) |
| Bourse.fr/N. Miguet, Bourse Ensemble, chaînes Telegram de « signaux », services affichant des taux de réussite invérifiables | — | — | — | — | **À éviter** (antécédent AMF, avis négatifs, risque de pump) |

Ce qu'aucune newsletter ne remplace : **le scan propre de 7h00–9h30** (communiqués + gap + RVOL). C'est lui qui résout le problème de précocité ; les lettres servent à confirmer, à alimenter la watchlist et à mesurer l'écart.

## 4.3 bis Fiche de qualification d'un fournisseur (obligatoire avant intégration)

Chaque source du dépôt a une fiche `docs/providers/<nom>.md` en deux temps : **pré-qualification documentaire** (avant tout code : couverture annoncée, délai annoncé, champs, CGU, coût daté, repli prévu) puis **qualification par prototype** (script `scripts/qualify_providers.py`, 3 séances, A0.7) qui complète les mesures. Contenu : fonction attendue ; marchés réellement couverts (liste des MIC testés) ; délai mesuré (écart `market_timestamp` vs horloge, sur 3 séances) ; champs disponibles et leur sémantique (dernier échange / bid / ask / clôture officielle / premier échange observé) ; périmètre des volumes (marché primaire ou consolidé Cboe) ; droits d'usage automatisé (CGU, robots.txt) ; coût vérifié et daté ; **test d'acceptation** (comparaison à l'interface du courtier sur 10 valeurs FR/DE/NL : cours, volumes, horodatages, comportement à la déconnexion/reprise, trous) ; solution de repli. Règle : **un indicateur ne mélange jamais deux périmètres** (ex. RVOL = volume Cboe du jour / moyenne Cboe des 20 jours, ou volume primaire / moyenne primaire — jamais l'un sur l'autre) et **ne change jamais de source silencieusement** en cours de calcul (le changement produit un statut `source_switched` et une alerte P4). L'API historique intraday EODHD livre des données différées et finalisées après la clôture : elle ne répare pas un trou du flux temps réel en séance (le trou est marqué, pas rebouché).

## 4.4 Budget recommandé (par paliers, à activer progressivement)

| Palier | Contenu | Coût mensuel indicatif | Quand |
|---|---|---|---|
| **P0 — Démarrage** | EODHD EOD+Intraday (29,99 $ ≈ 27 €) + Saxo données Euronext L1 (7 €, remboursé si ≥ 4 transactions) + Zonebourse (existant) + Momentum Capital (existant) + RSS gratuits + tradingview-screener + Cloudflare gratuit + domaine (~1 €/mois) + API Anthropic (~4–5 $) | **≈ 40 € + abonnements existants** | Phases 0–2 |
| **P1 — Précocité** | + **une seule nouvelle newsletter à la fois** (Décision Bourse 29 € puis ABC Bourse Premium 19,90 €), **en test 1 mois**, prolongée seulement si le post-mortem montre un apport (docs/12 §12.5) ; + Zonebourse Premium (29 € promo) pour les alertes d'arbitrage et listes Momentum Europe | ≈ +30 à 60 € | Phase 2–3 |
| **P2 — Confort** | EODHD All-in-One (+70 $) pour calendrier de résultats, fondamentaux, news+sentiment ; TradingView Essential (12,95 $) pour webhooks ; Saxo Xetra L1 (7 €) | ≈ +90 € | Phase 6 si les statistiques le justifient |

Chaque abonnement est enregistré dans `subscriptions` (coût, date de début, date de revue, KPI d'utilité : nombre de signaux issus de la source, expectancy des signaux, précocité). La revue mensuelle propose de résilier ce qui n'apporte rien.

## 4.5 Intégration technique (par type de source)

- **REST EODHD** : client `httpx` avec retry/backoff, quota journalier suivi en base, EOD nocturne pour tout l'univers (1 appel bulk par bourse : `eod-bulk-last-day`), intraday 5 min à la demande pour les candidats, `splits`/`dividends` hebdo.
- **WebSocket EODHD (`ws/eu`)** : un client par groupe de 50 symboles (positions + liste d'ouverture + watchlist momentum ≤ 150 → 3 connexions), reconnexion automatique ; le rattrapage des ticks manqués n'est fait **que** par l'endpoint de rattrapage du flux temps réel annoncé par EODHD, **s'il est confirmé en qualification (A0.7)** — jamais par l'API historique intraday (différée, finalisée après la clôture) ; à défaut, le trou est marqué (`complete_bar = false`) et les indicateurs qui le traversent portent le statut `gap_in_data` ; agrégation en barres 1 min stockées (`prices_intraday`), statut `realtime`. Attention : cours consolidés Cboe (BXE/CXE/DXE), **pas le fixing Euronext** → le prix d'ouverture officiel est pris dans l'EOD/Saxo ; le gap est calculé au premier trade Cboe puis corrigé.
- **Saxo OpenAPI** : application en environnement de simulation d'abord, puis demande de clé live (« usage personnel ») ; OAuth Authorization Code (refresh token stocké chiffré) ; endpoints **lecture seule** : `port/v1/positions`, `port/v1/orders`, `port/v1/balances`, `port/v1/closedpositions`, `trade/v1/infoprices` (+ streaming), `chart/v1/charts` ; respecter les rate limits documentés ; jamais `trade/v2/orders`. Test automatisé qui échoue si une URL contenant `/orders` est appelée en POST/PUT/DELETE.
- **tradingview-screener** : une requête par marché avec `limit` ≥ 1 000 (≤ 6 requêtes par scan) à 09:04, 09:14, 09:29, 09:59, 10:29, 17:45 ; champs : `name, close, open, gap, change, change_from_open, volume, relative_volume_10d_calc, average_volume_10d_calc, market_cap_basic, Perf.1M, Perf.3M, Perf.6M, Perf.Y, SMA20, SMA50, SMA200, ATR, High.All, price_52_week_high` ; statut `delayed` ; cache 5 min ; désactivable par paramètre.
- **RSS** : `feedparser`, User-Agent navigateur, cadence 5 min (7h–9h) puis 15 min, déduplication par GUID/URL, horodatage de publication conservé, texte intégral stocké (usage personnel), classification LLM (docs/06 D6).
- **IMAP Gmail** : adresse dédiée (`bourse.<nom>@gmail.com`) où sont redirigées toutes les newsletters ; libellés par expéditeur ; lecture toutes les 10 min de 07:30 à 19:00 ; parseur par source (HTML → texte → extraction des valeurs/ISIN/niveaux par regex puis LLM) ; **heure de réception enregistrée** (mesure de latence) ; contenu intégral conservé en privé, jamais affiché in extenso dans l'UI (résumé + lien).
- **CSV BoursoBank** : import de l'export « portefeuille » et « historique des opérations » (déposé dans un dossier surveillé ou via formulaire `/import`) ; format à valider sur un vrai fichier en phase 3 ; test avec fichier anonymisé.
- **Webhooks TradingView (phase 6)** : route `/api/webhooks/tradingview` protégée par secret, payload JSON normalisé → `signals(source=tradingview)`.

## 4.6 Statut de donnée et affichage

Chaque valeur affichée porte un badge : **RT** (temps réel, source + horodatage de marché), **D15** (différé), **EOD** (clôture), **STALE** (> seuil sans mise à jour), **N/A**. Une fiche d'ordre intraday n'est émise que sur une cotation dont l'**horodatage de marché** a moins de `data.buy_intraday_max_market_age_minutes` (3 min) — une donnée différée, quelle que soit l'heure de son téléchargement, ne produit qu'un candidat « à vérifier » (docs/07 §7.2). Un ordre préparé pour J+1 (D2/D3/D4) est calculé sur la clôture EOD officielle (ou la clôture provisoire du screener à 17:45, marquée `provisional` et réconciliée le lendemain à 06:45).

## 4.7 Cadre d'usage des sources

Usage strictement personnel sur la machine de l'utilisateur ; aucune redistribution, aucun affichage à des tiers ; respect des `robots.txt` et des CGU (Investing.com interdit l'usage automatisé → non utilisé ; Zonebourse : pas de scraping massif des listes, alertes e-mail privilégiées) ; conservation des newsletters payantes en privé, résumés dans l'outil, pas de reproduction longue. Les licences de données courtiers (Saxo) sont des licences d'affichage personnelles : le flux ne sort pas de l'outil.

---

# 05 — Univers d'investissement et éligibilité PEA / SRD

## 5.1 Places et segments couverts

| Place | Code EODHD | Indices de référence | Priorité | Remarques |
|---|---|---|---|---|
| Euronext Paris | PA | CAC 40, SBF 120, CAC Mid 60, CAC Small | **P0** | Seule place où le SRD existe ; univers historique du projet |
| Euronext Amsterdam | AS | AEX, AMX | P0 | Sociétés NL éligibles PEA (siège UE) |
| Euronext Bruxelles | BR | BEL 20, BEL Mid | P0 | |
| Xetra / Francfort | XETRA | DAX, MDAX, SDAX, TecDAX | **P0** | Deuxième profondeur d'univers ; enchère d'ouverture 08:50–09:00 |
| Euronext Milan | MI | FTSE MIB, Mid Cap | P1 | |
| Madrid | MC | IBEX 35, Medium | P1 | |
| Nasdaq Stockholm / Copenhague / Helsinki | ST / CO / HE | OMXS30, OMXC25, OMXH25 | P1 | Devises SEK/DKK → risque de change (docs/07) ; Norvège (Oslo, OL) éligible PEA (EEE) |
| Euronext Lisbonne, Dublin ; Vienne (VI) ; Varsovie (WAR) | LS / IR / VI / WAR | PSI, ISEQ, ATX, WIG20 | P2 | Liquidité plus faible → risque « small cap » relevé |
| Suisse (SIX), Royaume-Uni (LSE) | — | — | **Exclus du périmètre PEA** (hors UE/EEE) ; possibles en CTO uniquement, phase 6 | |
| États-Unis (NYSE, Nasdaq) | US | S&P 500, Nasdaq 100 | Phase 6 (CTO Saxo uniquement) | Gestion du change EUR/USD |

## 5.2 Règles d'éligibilité (à encoder, sources docs/16)

- **PEA** : société ayant son siège dans l'UE ou l'EEE (27 États membres + Islande, Liechtenstein, Norvège) et soumise à l'IS ; ni Suisse ni Royaume-Uni (titres UK inéligibles depuis le 01/01/2021). Plafond de versements 150 000 € (PEA-PME 225 000 €, cumul ≤ 225 000 €). **Pas de SRD, pas de levier, pas de vente à découvert dans le PEA.**
  - Détermination automatique : `country_of_domicile` (EODHD fundamentals / référentiel) → présomption ; **préfixe ISIN** en secours (FR, NL, BE, DE, IT, ES, SE, DK, FI, NO, PT, IE, AT, PL, LU…) ; **confirmation par le drapeau courtier** (badge « PEA » sur la fiche Boursorama, filtre `peaEligibility=1` de la liste Boursorama). Cas pièges : sociétés cotées à Paris mais domiciliées à Jersey/Guernesey/Bermudes/Suisse/UK, ADR. Statut stocké avec `source`, `checked_at`, `confidence` ; une valeur `unknown` n'est jamais proposée pour le PEA. Ce calcul ne couvre que l'éligibilité **réglementaire** ; l'accessibilité effective chez BoursoBank et la disponibilité des types d'ordres sont deux drapeaux distincts confirmés par l'utilisateur (docs/08 §8.4 bis).
- **PEA-PME** (option) : < 5 000 salariés et (CA ≤ 1,5 Md€ ou bilan ≤ 2 Md€), ou capitalisation < 2 Md€ (ou l'ayant été à la clôture d'un des 4 exercices précédents) ; éligibilité appréciée à la date d'acquisition ; listes Euronext/Easybourse des sociétés déclarées. Non prioritaire (v1 : information seulement).
- **SRD** (Euronext Paris, compte-titres uniquement) : SRD « complet » = capitalisation ≥ 1 Md€ et volume quotidien ≥ 1 M€ ; « SRD long seulement » = volume ≥ 100 k€ ; tous les ETF Paris en long-only. Liste officielle Euronext (PDF, versions anciennes en ligne ; entrées/sorties par avis Euronext, mise à jour observée en fin d'année) → **liste maintenue en base** (`srd_eligibility`) à partir de : filtre `market=SRD` de Boursorama, page abcbourse `marches/cotation_srdlo`, badge « SRD » des fiches, et **vérification finale dans l'interface Saxo** (l'outil affiche « SRD selon Boursorama/ABC — à confirmer chez Saxo » tant que l'utilisateur n'a pas coché la confirmation). Diff mensuel notifié.

## 5.3 Filtres de liquidité et de qualité (paramètres `universe.*`)

| Filtre | Valeur par défaut | Raison |
|---|---|---|
| Volume moyen quotidien en € (20 j) | ≥ 1 000 000 € pour le CTO/SRD ; ≥ 500 000 € pour le PEA | Exécution des stops sans slippage excessif |
| Capitalisation | ≥ 300 M€ (≥ 150 M€ toléré) ; le niveau de risque « élevé » (taille × 0,5, docs/07) s'applique sous `universe.small_cap_threshold_eur` (1 Md€) | Réduction du risque d'illiquidité |
| Prix | ≥ 1 € | Exclusion des penny stocks |
| Historique | ≥ 250 séances | Calcul RS/ATR/MM200 |
| Statut | Pas en suspension, pas d'OPA en cours (sauf détecteur dédié), pas de « SRD long seulement » pour des positions vendeuses (hors périmètre de toute façon) | |
| Exclusions manuelles | Liste `universe.blacklist` (valeurs jugées ininvestissables par l'utilisateur) | |

Univers attendu : ~600–900 valeurs (Paris ~200, Xetra ~150, Amsterdam/Bruxelles ~80, Milan ~80, Madrid ~50, Nordics ~150, autres ~50). Le scan EOD couvre tout l'univers ; le temps réel ne couvre que **positions + liste d'ouverture + watchlist momentum** (≤ 150 symboles).

## 5.4 Sous-univers dynamiques (recalculés chaque soir)

- **Leaders momentum** : rang RS (docs/06) ≥ 80e percentile de l'univers, au-dessus de MM50 et MM200, MM50 > MM200 → « watchlist momentum » (≤ 100 valeurs) sur laquelle les détecteurs de **point d'entrée** (D4 pullback, D3 range expansion) tournent en priorité et qui est suivie en temps réel.
- **Liste d'ouverture** (chaque matin à 08:45) : valeurs ayant un catalyseur classé pertinent depuis 17:40 la veille + valeurs des lettres du matin + valeurs momentum proches d'un pivot (≤ 3 % sous le plus haut 60 j) → ≤ 15 valeurs suivies en temps réel dès 08:55.
- **Univers SRD** : intersection univers × `srd_eligibility=complet` → seul sous-univers routable vers le CTO avec levier.
- **Univers PEA** : intersection univers × `pea_eligible=true (confidence ≥ 0.8)`.

## 5.5 Référentiel instrument

Champs : `isin`, `ticker_local`, `ticker_eodhd`, `ticker_saxo (Uic)`, `ticker_tv`, `name`, `exchange`, `mic`, `currency`, `country_of_domicile`, `sector`, `industry`, `index_memberships[]`, `market_cap_eur`, `adv_eur_20`, `pea_eligible`, `pea_confidence`, `pea_source`, `srd_status ∈ {complet, long_only, none, unknown}`, `srd_confirmed_by_user`, `earnings_next_date`, `earnings_source`, `active`, `updated_at`. Toute correspondance de tickers entre providers est stockée et testée (les tickers Xetra/EODHD/Saxo diffèrent).

## 5.6 Versionnement

Chaque `universe_refresh` produit un instantané (`universe_snapshots`) ; les entrées/sorties sont listées dans le digest hebdo. Les signaux historisés référencent l'instantané utilisé, ce qui évite le biais de survivance dans les statistiques rétrospectives.

---

# 06 — Moteur de signaux : régime, détecteurs, scoring

Principe : **4 détecteurs d'entrée = 4 stratégies** (chacune avec ses paramètres, ses statistiques et son post-mortem), + 1 détecteur de catalyseurs pré-ouverture, + 1 intégrateur de signaux externes (newsletters), + des détecteurs de sortie. Tous les seuils sont dans `config/params.yaml › detectors.*` ; toutes les définitions d'indicateurs sont en annexe et testées sur fixtures.

## 6.1 Objets

- `Signal` : `instrument`, `detector`, `ts_utc`, `timeframe ∈ {premarket, intraday, eod}`, `direction ∈ {long}`, `entry_zone (low, high)`, `stop_initial`, `targets[]`, `horizon ∈ {days, swing_2_6w, medium}`, `evidence{}` (valeurs des indicateurs ayant déclenché), `data_status`, `catalyst_id?`, `external_refs[]`.
- `Score` : sous-scores C (0–30), T (0–25), F (0–20), R (0–15), M (0–10), `total`, `confidence (1–5)`, `risk_level ∈ {faible, moyen, élevé}`, `explanations[]`.
- `Proposal` (docs/07 et 08 ajoutent taille, compte, blocages).

## 6.2 Régime de marché (calculé à 07:30 et 17:50, `domain/regime`)

| Indicateur | Vert | Orange | Rouge |
|---|---|---|---|
| CAC 40 et STOXX Europe 600 vs MM50 | Les deux au-dessus | Un seul | Les deux en dessous |
| Breadth univers : % de valeurs au-dessus de la MM50 | ≥ 50 % | 35–50 % | < 35 % |
| Distribution days (séances de baisse ≥ 0,2 % avec volume > veille) sur 25 séances, CAC 40 | ≤ 3 | 4–5 | ≥ 6 |
| Volatilité (VSTOXX ou ATR14/close du STOXX 600 en percentile 1 an) | < 60e pct | 60–85e | > 85e |

Agrégation (paramètres `regime.*`) : **rouge** si ≥ `regime.red_min_components` (2) composantes rouges ; **orange** si ≥ 1 composante orange ou 1 rouge ; **vert** sinon. Effets : **Vert** = tailles normales ; **Orange** = taille × 0,5, BUY seulement si ratio d'admission ≥ `scoring.buy_min_ratio_orange` ; **Rouge** = pas de nouvelle entrée (WATCH seulement), rappel de resserrer les stops. Le régime est affiché en tête de chaque brief/rapport avec ses composantes.

## 6.3 Détecteurs d'entrée

### D1 — Gap + catalyseur à l'ouverture (« episodic pivot »)
- Fenêtre : 09:00–10:30 Paris, scans à 09:05, 09:15, 09:30, 10:00, 10:30. **Au scan de 09:05, seules 5 minutes existent : on utilise `OR5` et `RVOL_5` ; à partir de 09:15, `OR15` et `RVOL_15`.**
- Conditions : `gap_open = open / close_prev − 1 ≥ +3 %` (large caps, ADV ≥ 5 M€) ou **≥ +5 %** (autres) ; **RVOL_5 ≥ 2,0 (09:05) puis RVOL_15 ≥ 2,0** — référence 20 j calculée à 08:45 par backfill REST intraday 5 min pour les valeurs de la liste d'ouverture ; pour le reste de l'univers (screener différé), approximation `volume cumulé / (ADV20 × fraction de séance écoulée) ≥ 2,0`, statut `approx` dans `evidence` ; **catalyseur identifié** (D6 : communiqué, résultats, contrat, relèvement de guidance, reco de broker) classé `positif`, ampleur `forte` ou `moyenne` ; prix ≥ `OR5_high` au moment du scan ; pas de publication de résultats **à venir** dans les 48 h (des résultats publiés le matin même sont le catalyseur, pas un blocage) ; pas dans un régime rouge.
- Niveaux : zone d'entrée = cassure de `OR_high` jusqu'à `OR_high × 1,01` (**prix max**), où `OR_high` = `OR5_high` au scan de 09:05 et `OR15_high` ensuite ; stop initial **structurel** = `day_low − 0,25 × ATR14` (repli `entrée − 1,0 × ATR14` seulement si `day_low` indisponible) ; si la distance dépasse `1,5 × ADR20` sous le prix max → signal rejeté (WATCH « stop trop large ») ; objectif 1 = prix max + 2 R ; objectif 2 = +3 R ou plus haut 52 s ; horizon : `days` (peut évoluer en swing si la position progresse). **Cotation requise** : horodatage de marché < 3 min (temps réel) ; sur donnée différée, le D1 est un candidat « à vérifier », sans fiche d'ordre.
- Gap sans catalyseur (ou catalyseur `neutre`) → WATCH seulement. Gap ≥ 15 % → risque `élevé`, taille × 0,5 (paramètre).
- Expiration : chaque proposition D1 expire à `min(heure du scan + 30 min, 11:00)` ; passé ce délai, elle devient WATCH « pullback vers OR15_high / VWAP ».
- Latence : l'alerte est envoyée dès le calcul (< 60 s) avec les niveaux ; l'argumentaire LLM est ajouté **ensuite** (mise à jour du message / de la page), jamais attendu.

### D2 — Cassure momentum (breakout de leader)
- Univers : watchlist momentum (docs/05 §5.4) + tout l'univers en EOD.
- Conditions EOD (17:50) ou intraday (09:30, 10:30 : candidat sur donnée différée, fiche d'ordre sur cotation temps réel seulement) : clôture (ou cours) > plus haut des 60 séances (pivot) **ou** > plus haut 52 semaines ; RVOL_jour ≥ 1,5 ; rang RS ≥ 80 ; MM50 > MM200 et cours > MM50 ; extension limitée : cours ≤ MM20 + 1,5 × ATR14 (sinon WATCH « trop étendu ») ; consolidation préalable : amplitude (max−min) des 20 séances précédentes ≤ 15 % (paramètre) ; pas de résultats < 48 h.
- Niveaux : entrée = pivot + 0,3 % (ordre à plage de déclenchement pour J+1 : seuil = pivot + 0,3 %, limite = prix max), prix max = pivot + 1,0 % ; stop **structurel** = plus bas des 10 séances − 0,25 × ATR14 (repli `entrée − 1,5 × ATR14` si structure indisponible) ; rejet si distance > 2,5 × ADR20 ; objectifs 2 R / 3 R ; horizon `swing_2_6w`. Catalyseur non requis (bonus C s'il existe).

### D3 — Expansion de range (« momentum burst »)
- Conditions EOD : `close/close_prev ≥ 1,04` ; volume > volume veille et RVOL ≥ 1,3 ; bougie fermant dans le tiers haut de sa plage ; les 3–20 séances précédentes en consolidation (variation quotidienne moyenne < 2 %, pas de +4 % dans les 5 jours) ; TI65 : moyenne(close, 7) ≥ 1,05 × moyenne(close, 65) ; rang RS ≥ 70.
- Niveaux : entrée J+1 = au-dessus du plus haut de la bougie de cassure (ordre à plage de déclenchement : seuil = plus haut + 0,1 %, limite = prix max = plus haut + 0,5 %) ou sur pullback à mi-bougie ; stop **structurel** = plus bas de la bougie de cassure − 0,25 × ATR14 ; rejet si distance > 2,5 × ADR20 ; sortie temps : J+5 si l'objectif 1 (2 R) n'est pas atteint ; horizon `days`. Compte : comparaison PEA / CTO comptant / SRD (docs/08 §8.2), sans préférence par défaut.

### D4 — Pullback sur leader momentum (point d'entrée sur valeur sous surveillance)
- Objectif explicite de l'utilisateur : entrer sur les valeurs momentum « sous surveillance » (listes Zonebourse, Momentum, watchlist propre) sans courir après le gap.
- Conditions : valeur dans la watchlist momentum (rang RS ≥ 80, tendance haussière) ; repli de 3 à 10 % depuis le plus haut 20 j ; cours revenu au contact de la MM10 ou MM20 (± 1 × ATR) **ou** du niveau de l'ancienne cassure ; volume en baisse pendant le repli (volume moyen 3 j < 0,8 × ADV20) ; **signal de reprise** : cours repasse au-dessus du plus haut de la veille (intraday) ou bougie de retournement en clôture ; RSI(14) entre 40 et 60 pendant le repli (paramètre indicatif) ; pas de résultats < 48 h.
- Niveaux : entrée = plus haut de la veille + 0,2 % (prix max = + 0,8 %) ; stop **structurel** = plus bas du repli − 0,25 × ATR14 ; rejet si distance > 1,5 × ADR20 ; objectif 1 = plus haut 20 j (R/R ≥ 2 exigé, sinon WATCH) ; objectif 2 = +3 R ; horizon `swing_2_6w`. **Aucun catalyseur requis.** Compte : comparaison des trois options (docs/08 §8.2).
- Alimentation de la watchlist momentum « sous surveillance » : (1) calcul propre (rang RS, docs/05 §5.4) ; (2) valeurs citées par les lettres (D5) ; (3) **listes Momentum Europe/USA de Zonebourse Premium** : lecture authentifiée **une fois par jour** (job `zonebourse_lists_daily` 08:30, usage personnel, conforme à docs/04 §4.7) ou, à défaut, import manuel (coller la liste dans `/watchlist`) ; chaque entrée garde sa source pour le post-mortem.

### D5 — Intégrateur de signaux externes (newsletters, analystes)
- Chaque valeur extraite d'une lettre (Zonebourse, Momentum Capital, Décision Bourse, ABC Premium, recos de brokers RSS) devient un `signal(detector=external, source=…)` avec : sens, niveaux cités s'ils existent, **heure de réception**, cours d'ouverture officiel (`p_open`), cours à l'heure de réception (`p_recv`, horodatage de marché), `gap_open = p_open / close_prev − 1` (le gap proprement dit) et `drift_since_open = p_recv / p_open − 1` (la dérive depuis l'ouverture, c'est-à-dire ce que la lettre « rate »).
- Règles de fusion : un signal externe **confirme** un signal propre (D1–D4) émis le même jour → +5 points en C (max 30) et confiance +1 ; un signal externe **seul** sur une valeur momentum → déclenche un D4 « à l'affût » (watch pullback avec niveaux calculés) ; un signal externe seul sans configuration technique → WATCH.
- Aucun BUY n'est émis **uniquement** sur un signal externe si `drift_since_open ≥ 2 %` (`detectors.d5_external.drift_since_open_max_for_buy`) : c'est précisément le problème à éviter. Le post-mortem (docs/12) mesure la valeur de chaque source.

### D6 — Catalyseurs pré-ouverture (07:00–08:55) et intra-séance
- Sources : RSS (ActusNews, GlobeNewswire, ABC Bourse, TradingSat, Boursorama communiqués), EODHD news (si All-in-One).
- Pipeline : déduplication → rattachement à l'instrument (ISIN/nom via table d'alias, LLM en secours) → **classification LLM (Haiku)** avec schéma JSON : `type ∈ {résultats, guidance, contrat, M&A, dividende, augmentation_capital, gouvernance, reco_broker, réglementaire, autre}`, `direction ∈ {positif, négatif, neutre}`, `magnitude ∈ {forte, moyenne, faible}`, `ampleur_annoncée (0–1)` (d'après le texte de l'émetteur : « supérieur aux attentes », relèvement de guidance…), `résumé ≤ 240 caractères`, `confiance (0–1)`. Une **surprise** chiffrée (écart au consensus) n'est calculée **que** si un consensus daté est disponible (EODHD estimates, plan All-in-One) ; sans consensus, le champ est `null` et seule l'ampleur annoncée est utilisée, avec moins de points. Prompt versionné ; sortie validée ; température 0 ; cache par hash ; 50 items max par cycle.
- Sortie : `premarket_watch` (valeurs à surveiller à 09:00 avec direction attendue) ; alimente D1 (catalyseur requis) et le score C ; les catalyseurs `négatif/forte` sur une **position** déclenchent une alerte immédiate (S4, §6.4).
- Le scan tourne aussi en séance (toutes les 15 min) et de 17:40 à 19:00 (communiqués du soir), afin que le brief du lendemain couvre « les catalyseurs depuis 17:40 la veille ».

## 6.4 Détecteurs de sortie / allègement (sur positions ouvertes)

| Détecteur | Condition | Action proposée |
|---|---|---|
| S1 Seuil franchi | cours (temps réel) ≤ stop courant | **P1 « seuil franchi — vérifier l'exécution chez le courtier »** ; si aucune exécution confirmée (import ou déclaration) sous 5 min en séance → proposition **SELL au marché** ; « seuil franchi » et « stop exécuté » restent deux états distincts (docs/07 §7.4) |
| S2 Objectif 1 atteint | cours ≥ entrée + 2 R | REDUCE 50 % + stop remonté au prix d'entrée (paramètre) |
| S3 Trailing | clôture > entrée + 1 R : stop = max(stop, plus bas 5 j − 0,5 ATR) ; > 2 R : stop = max(stop, MM10 − 0,5 ATR) | Mise à jour du stop (rappel de modification chez le courtier) |
| S4 Thèse invalidée | Clôture < MM20 deux jours consécutifs (D2/D4) ; clôture sous la bougie de cassure (D3) ; catalyseur négatif fort ; rang RS < 50 | SELL/REDUCE selon score |
| S5 Sortie temps | D3 : J+5 sans objectif 1 (`detectors.d3_range_expansion.time_stop_sessions`) ; D1 : J+10 sans progression > 1 R (`detectors.exits.d1_time_stop_sessions`) ; toute position : J+40 sans objectif (`detectors.exits.time_stop_sessions_default`) | REDUCE/SELL |
| S6 Publication imminente | Résultats dans ≤ 48 h et gain < 1 R | REDUCE 50 % ou SELL (paramètre `risk.earnings_policy_on_position`) |
| S7 Liquidation SRD | Position SRD à J−3 de la liquidation (`detectors.exits.srd_liquidation_alert_days_before`) | Décision demandée : solder, proroger (coût affiché), ou transformer en comptant (si cash) |
| S8 Régime rouge | Passage en rouge | Rappel de resserrer les stops (stop = max(stop, MM20)) |
| S9 Meilleure opportunité | Nouveau BUY avec ratio d'admission ≥ `scoring.exceptional_min_ratio` alors que le nombre max de positions est atteint | Proposer l'allègement de la position au ratio courant le plus faible |

## 6.5 Grille de scoring (identique au projet actuel, rendue calculable)

| Bloc | Points | Sous-critères calculables (points) | Part LLM |
|---|---|---|---|
| **C — Catalyseurs & news** | 0–30 | Catalyseur D6 : forte +15 / moyenne +9 / faible +4 ; direction positive requise (négatif = 0) ; surprise vs consensus > 0,6 : +5 (sans consensus : ampleur annoncée > 0,6 : +3) ; reco broker positive < 24 h : +4 ; confirmation externe (D5) : +5 ; catalyseur > 3 jours : −50 %. **Requis (≥ `scoring.d1_min_c`) pour D1 seulement** ; bonus plafonné (`scoring.c_bonus_cap`) pour D2/D3/D4, jamais requis | Classification du catalyseur (type, direction, ampleur) |
| **T — Technique / momentum** | 0–25 | Rang RS ≥ 90 : +8, ≥ 80 : +6, ≥ 70 : +3 ; structure (au-dessus MM50 & MM200, MM50 > MM200) : +5 ; cours > MM20 et MM20 croissante : +2 ; RVOL ≥ 2 : +5, ≥ 1,5 : +3 ; qualité du déclencheur (cassure nette de pivot / OR15 / bougie de retournement) : +5 ; extension excessive (> MM20 + 1,5 ATR) : −4 ; proximité d'une résistance majeure < 3 % : −3 (borné à [0, 25]) | Aucune |
| **F — Fondamentaux** | 0–20 | Croissance CA (dernier exercice/trimestre) > 10 % : +5 ; marge opérationnelle > 10 % : +4 ; révisions de BPA 3 mois positives : +5 ; dette nette/EBITDA < 2 : +3 ; valorisation non extrême (PER < 2 × médiane sectorielle) : +3. Sources : EODHD fundamentals (plan All-in-One, P2) ; en P0, `yfinance.info` en secours (statut dégradé) et champs fondamentaux du screener TradingView. **Si aucune donnée : F n'est pas attribué** ; le score est présenté sur le total atteignable (« 68/80 — fondamentaux non disponibles »), la complétude est affichée, confiance −1 | Aucune |
| **R — Risque / volatilité / agenda** | 0–15 | Distance au stop ≤ 1,5 ADR : +5, ≤ 2,5 ADR : +3, sinon 0 ; pas de résultats sous 10 jours : +4 (date inconnue : +0 et mention) ; liquidité ADV ≥ 5 M€ : +3, ≥ 1 M€ : +2 ; capitalisation ≥ 1 Md€ : +3 ; gap ≥ 15 % : −4 ; devise ≠ EUR : −2 (borné à [0, 15]) | Aucune |
| **M — Contexte secteur / macro** | 0–10 | Régime vert +5 / orange +2 / rouge 0 ; secteur : RS sectoriel ≥ 60e pct +3 ; pas d'événement macro majeur le jour (Fed/BCE/CPI/NFP) +2 ; corrélation au portefeuille (même secteur déjà ≥ 2 positions) −3 (borné à [0, 10]). Secteur : champ `sector`/`industry` du screener TradingView (P0), confirmé par EODHD en P2 | Résumé macro du jour (texte, hors score) |

**Deux scores, un seul calcul.** Le **score global /100** (grille ci-dessus) est conservé pour information et continuité avec le projet actuel. Le **score d'admission** est propre à chaque détecteur : il ne met au dénominateur que les blocs **applicables** à la stratégie.

| Détecteur | Blocs au dénominateur | Bloc C | Ratio d'admission |
|---|---|---|---|
| D1 gap + catalyseur | C + T + R + M (+ F si disponible) | Requis : C ≥ `scoring.d1_min_c` | (C+T+R+M+F) / (30+25+15+10+F_max) |
| D2 cassure, D3 expansion | T + R + M (+ F si disponible) | **Bonus** : min(C, `scoring.c_bonus_cap` = 5) ajouté au numérateur seulement | (T+R+M+F+bonus) / (25+15+10+F_max), plafonné à 1 |
| D4 pullback | T + R + M (+ F si disponible) | Bonus (même règle) ; jamais requis | idem |

**Fondamentaux partiels** : F est calculé sur les sous-critères disponibles ; `F_max` = somme des maxima des sous-critères disponibles si ≥ 2 sur 5 le sont, sinon F est exclu du dénominateur ; la complétude (« F : 3/5 critères ») est toujours affichée et la confiance est réduite de 1 sous 3/5.

Interprétation : **BUY** si ratio d'admission ≥ `scoring.buy_min_ratio` (0,75 ; 0,80 en régime orange), risque faible/moyen, portes du détecteur franchies (docs/07 §7.5), entrée cohérente (stop structurel ≤ distance max, R/R ≥ 2), régime ≠ rouge ; **WATCH** 0,60–0,74 ; **HOLD** position existante sans renfort ; **SELL/REDUCE** via S1–S9 ; **NO TRADE** sinon. Test de référence : un D4 sans aucune actualité, avec T = 23, R = 15, M = 10 et F indisponible, obtient 48/50 = 0,96 → BUY ; le même avec T = 15, R = 10, M = 5 obtient 0,60 → WATCH. Les trois lectures (attractivité / qualité de l'entrée / admissibilité, docs/07 §7.6) sont dérivées de la même grille et affichées avec les deux scores. **Confiance /5** = f(data_status, complétude des blocs, concordance détecteur + externe + catalyseur) ; **risque** = f(distance stop, ADV, capitalisation, gap, devise, small cap).

Chaque score est stocké avec ses sous-scores et ses `explanations[]` (« RS rang 87 → +6 », « résultats dans 6 jours → 0 ») pour que le rapport et le post-mortem soient traçables.

## 6.6 Rôle du LLM (Claude API) dans ce module

- Classification des catalyseurs (D6) et extraction structurée des newsletters (D5) — **Haiku**, JSON strict.
- Rédaction de l'argumentaire synthétique et des « points de vigilance » de chaque proposition, à partir des `explanations[]` et des données — **Sonnet**, sans chiffre non fourni (test : tout nombre présent dans le texte doit exister dans les données d'entrée, à ±0,5 %).
- Jamais : calcul d'indicateur, de niveau, de score, de taille.

## Annexe — Définitions d'indicateurs (toutes testées dans `tests/unit/test_indicators.py`)

- `ATR14` : moyenne de Wilder du True Range sur 14 séances. `ADR20` : moyenne sur 20 séances de (high/low − 1), en %.
- `RVOL_jour` : volume du jour / moyenne des volumes sur 20 séances (hors jour courant). `RVOL_5` / `RVOL_15` : volume cumulé des 5 / 15 premières minutes / moyenne de cette même fenêtre sur 20 séances, calculée à partir des barres 1 min stockées ou du backfill REST 5 min (3 barres = 15 min) fait à 08:45 pour la liste d'ouverture ; à défaut, approximation `volume cumulé / (ADV20 × fraction de séance écoulée)` marquée `approx`.
- Unités : `ATR14` est en devise (€) ; `ADR20` est en % ; une borne « 1,5 ADR sous l'entrée » se lit `entrée × (1 − 1,5 × ADR20)`.
- `RS` (force relative, Minervini) : `0,4·r12m + 0,2·r6m + 0,2·r3m + 0,2·r1m` (rendements) → rang percentile dans l'univers du jour (`rs_rank` 0–100). Variante « vs indice » stockée aussi : `r3m − r3m(STOXX 600)`.
- `MM10/20/50/200` : moyennes mobiles simples des clôtures. `TI65` : `SMA(close,7) / SMA(close,65)`.
- `OR5_high/low`, `OR15_high/low` : plus haut/bas des 5 et 15 premières minutes de la séance continue (09:00:00–09:04:59 / 09:14:59 Paris).
- `gap_open` : `open / close_prev − 1` (open = premier cours officiel Euronext si disponible, sinon premier trade RT, corrigé à l'EOD).
- `pivot_60` : plus haut des 60 séances précédentes (hors jour courant). `consolidation_20` : (max(high,20) − min(low,20)) / min(low,20).
- `distribution_day` : clôture indice ≤ −0,2 % et volume > veille. `breadth_mm50` : part des instruments de l'univers avec close > MM50.
- `R` : (entrée − stop) en € par action ; les objectifs et le P&L sont exprimés en multiples de R.

---

# 07 — Gestion du risque, dimensionnement, stops, portes de discipline

Tout est paramétré dans `config/params.yaml › risk.*` ; toute proposition qui viole une règle est affichée `BLOQUÉ : <règle>` (jamais masquée : l'utilisateur doit voir pourquoi). Ce document est la **référence** pour toute règle de risque ; en cas de divergence avec un autre document, il prime (après `params.yaml`).

## 7.1 Capital et risque par trade (règles du projet actuel, conservées)

- `capital.capital_pilote_eur` (modifiable dans `/parametres`, historisé) ; `capital.cash_additionnel_max_eur` mobilisable pour les opportunités exceptionnelles (ratio de score ≥ `scoring.exceptional_min_ratio`, confiance ≥ 4, risque faible) — toute proposition utilisant ce cash le mentionne explicitement.
- **Base de capital** : le risque par trade et les plafonds sont calculés sur le capital pilote, pas sur la valeur liquidative du jour (`risk.capital_base: pilote`). Le capital pilote est revu manuellement, jamais recalculé automatiquement après gains ou pertes.
- **Risque par trade = 0,75 % du capital pilote** (`risk.max_risk_per_trade_pct`), soit 300 € pour 40 000 €.

## 7.2 Dimensionnement (sur le prix maximal d'entrée)

```
prix_max        = prix limite de l'ordre d'entrée (non dépassable par construction : ordre limite ou à plage)
                  ; pour une entrée au marché / à seuil simple (D1 intraday), prix_max = niveau × (1 + risk.slippage_pct)
stop_exec       = stop_initial × (1 − risk.slippage_pct)          # sortie sur stop à seuil : exécution sous le seuil
frais_estimés   = courtage aller + courtage retour + TTF éventuelle, calculés sur le notionnel de la taille
                  candidate et itérés jusqu'au point fixe (≤ 3 itérations : frais → taille → frais) ;
                  la CRD, qui dépend de la durée, entre dans le coût d'horizon (docs/08) et la comparaison
                  des comptes, pas dans le dimensionnement
R_par_action    = (prix_max − stop_exec) × taux_de_change_EUR × (1 + risk.fx_haircut si devise ≠ EUR)
                  # la marge de change augmente le R par action, donc RÉDUIT la quantité ; le budget (300 €) ne bouge pas
shares          = floor((risque_eur − frais_estimés) / R_par_action)
plafonds        : shares × prix_max ≤ risk.max_position_pct_of_capital × capital
                  shares × prix_max ≤ risk.max_position_pct_of_adv × ADV20_eur
```

Convention de glissement (`risk.slippage_pct`, 0,2 %) : elle s'applique **toujours** à la sortie sur stop à seuil (`stop_exec`) et à l'entrée **seulement** si l'ordre d'entrée ne borne pas le prix (marché, à seuil simple). Un ordre limite ou à plage de déclenchement n'a pas de glissement d'entrée : son prix limite est le prix max.

- Le **prix maximal d'entrée** est affiché sur toute fiche d'ordre ; au-delà, l'ordre ne doit pas être passé (la fiche le dit).
- Après exécution (partielle ou différente du scénario), le risque réel est **recalculé** sur le prix exécuté et la quantité détenue ; si le risque réel dépasse `risque_eur × 1,1`, l'outil propose un allègement ou un stop ajusté (jamais abaissé sous le stop structurel).
- Multiplicateurs : régime orange × 0,5 ; risque élevé (capitalisation < `universe.small_cap_threshold_eur`, gap ≥ 15 %, ADV < 1 M€) × 0,5 avec stop ≤ 1,5 ADR obligatoire.
- **Statut de donnée** : une proposition d'entrée intraday exige une cotation dont l'horodatage de marché a moins de `data.buy_intraday_max_market_age_minutes` (3 min) — en pratique du temps réel. Une donnée différée sert à détecter des candidats et préparer des scénarios (« à vérifier »), jamais à émettre une fiche d'ordre. Un ordre préparé pour J+1 (D2/D3/D4, à plage de déclenchement) est calculé sur la clôture EOD officielle (ou provisoire, marquée comme telle).
- Les exemples chiffrés de docs/09 §9.1 sont des **tests de référence** (`tests/unit/test_sizing_examples.py`).

## 7.3 Plafonds de portefeuille (calculés, pas déclaratifs)

| Plafond | Paramètre | Règle |
|---|---|---|
| Risque ouvert cumulé | `risk.max_open_risk_pct` (4 %) | `risque_courant_total + risque_réservé ≤ 4 % × capital pilote`, avec les définitions déterministes ci-dessous. Au-delà : BLOQUÉ. |
| Risque par secteur | `risk.max_sector_risk_pct` (2 %) | Même calcul par secteur (niveau 1) ; ≤ 2 positions par secteur (`risk.max_positions_per_sector`, 3 en régime vert si ratio d'admission ≥ `risk.sector_third_position_min_ratio` = 0,80) |
| Risque par facteur | `risk.max_factor_risk_pct` (3 %) | Groupes de corrélation configurables (ex. « défense », « semi-conducteurs », « luxe ») |
| Scénario de gap | `risk.gap_scenario_pct` (−10 %) | Perte simulée si toutes les positions ouvrent à −10 % sous le stop : affichée chaque soir ; alerte si > 2 × risque ouvert cumulé |
| Exposition | `risk.exposure.green/orange/red` | ≤ 80 % en vert, ≤ 50 % en orange, gel en rouge. **60 % et 5 positions sont des repères de diversification, jamais des objectifs** : l'outil n'incite jamais à investir faute de meilleurs signaux. |
| Nombre de positions | `risk.positions_max` (15) | Plafond ; pas de minimum |
| Levier SRD | `risk.srd_max_leverage` (2,0 ; absolu 2,5) | Notionnel SRD / valeur liquidative CTO ; couverture disponible ≥ 1,5 × requise (alerte) / ≥ 1,3 × (blocage) ; réserve de cash CTO ≥ 20 % |
| Devises | `risk.max_fx_exposure_pct` (30 %) | Exposition hors EUR / exposition totale |
| Place | `risk.max_exposure_per_non_paris_market_pct` (40 %) | Par place hors Paris |
| Drawdown | `risk.circuit_breaker` | Pertes **réalisées + latentes** de la semaine ≤ 3 R et du mois ≤ 6 R ; au-delà, pas de nouvelle entrée jusqu'au lundi / mois suivant, tailles × 0,5 la semaine de reprise |

Définitions (toutes en EUR, testées) :

- **Risque initial** d'une position = `qty_exécutée × (prix_exécuté − stop_exec_initial) + frais d'entrée + frais de sortie estimés`. Référence figée à l'ouverture ; sert au calcul des R réalisés, jamais aux plafonds courants.
- **Risque courant** d'une position = `max(0, qty_détenue × (prix_courant − stop_exec_courant)) + frais de sortie estimés` ; `prix_courant` = dernière cotation valide (ou clôture si hors séance) ; **borné à zéro par position** : une position dont le stop est au-dessus du prix courant… n'existe pas (le seuil est franchi), et une position gagnante avec stop remonté au-dessus du prix d'entrée contribue au plus ses frais de sortie, jamais un montant négatif qui compenserait une autre position. Quantité non couverte par un stop → risque courant = `qty_non_couverte × prix_courant` (perte totale possible, affichée en rouge).
- **Risque réservé** d'un ordre d'entrée en attente = `qty_non_exécutée × (prix_max − stop_exec_prévu) + frais`.
- **Risque cumulé** = Σ risques courants + Σ risques réservés. Le plafond de 4 % s'applique à ce total ; le scénario de gap (`gap_scenario_pct`) recalcule la perte si toutes les positions ouvrent à −10 % sous leur stop.
- **Drawdown** : (a) drawdown d'équité = équité / plus haut d'équité − 1, sur une équité TWR (versements et retraits neutralisés) ; (b) pour le coupe-circuit, pertes en R de la période (semaine / mois civils) = Σ R réalisés + Σ variation de R latent depuis le début de la période. Les deux sont affichés, le coupe-circuit n'utilise que (b).
- **Frais** : entrée déjà payée (incluse dans le PMP), sortie estimée (courtage + éventuelle CRD courue) comptée dans le risque courant.

## 7.4 Stops

- **Stop initial obligatoire** sur toute proposition ; jamais « à ajuster plus tard ».
- **Choix du niveau (explicite et testé)** : le stop est d'abord **structurel** — sous le plus bas de la structure de référence (plus bas du jour pour D1, plus bas des 10 séances pour D2, plus bas de la bougie de cassure pour D3, plus bas du repli pour D4) moins un tampon `risk.stop.buffer_atr_mult × ATR14` (0,25). Le stop de volatilité (`entrée − k × ATR14`) n'est utilisé **qu'en repli** si aucune structure n'est disponible. Si la distance du stop structurel dépasse `stop_max_adr_mult × ADR20` du détecteur (1,5 pour D1/D4, 2,5 pour D2/D3), le signal est **rejeté** (WATCH « stop trop large ») — on ne rapproche jamais un stop pour faire tenir la taille.
- **Type d'ordre de protection** : ordre **à seuil de déclenchement** (devient « au marché » au franchissement : exécution quasi garantie, prix non garanti) par défaut ; ordre **à plage de déclenchement** (devient limite : prix borné, exécution non garantie) seulement si la plage est ≥ 1 ADR et que l'utilisateur l'accepte explicitement. Les deux ne sont jamais traités comme équivalents ; la fiche d'ordre précise le type et ses limites (source : AMF, « choisir et passer un ordre de bourse »).
- **États d'exécution et de protection** (docs/09 §9.1, CLAUDE.md règle 9) : deux états indépendants suivis **par quantité** — exécution (`order_entered` → `filled_partial` → `filled`) et protection (`unprotected` → `partially_protected` → `protected`, `qty_protégée` vs `qty_détenue`). Toute quantité exécutée non protégée déclenche une **P1 immédiate dans tous les modes** (regroupée : une alerte, puis rappel toutes les 15 min tant que la quantité n'est pas couverte) ; le dashboard affiche en rouge toute quantité non protégée.
- **Suivi** : `position_monitor` compare le cours (temps réel) au stop courant ; **« seuil franchi »** (cours ≤ stop) et **« stop exécuté »** (confirmé par l'import courtier ou l'utilisateur) sont deux états distincts. Séquence unique (référence pour docs/06 S1 et docs/15 A1.7) : seuil franchi → **P1 immédiate** « seuil franchi — vérifier l'exécution chez le courtier » ; si aucune exécution confirmée après `risk.stop.unconfirmed_exit_minutes` (5 min) en séance → proposition **SELL au marché** (P1, même incident).
- **Stops inadaptés** : après une vente partielle, un stop portant sur l'ancienne quantité est signalé ; un stop expiré (validité jour chez certains courtiers) est rappelé chaque soir ; un stop rejeté par le courtier (hors seuils de réservation) doit être ressaisi.
- **Trailing** (S3, docs/06) : proposé le soir, jamais en baisse ; l'utilisateur confirme la modification chez le courtier, l'outil vérifie à l'import.
- **Gap sous le stop** : l'outil affiche la perte réelle attendue à l'ouverture et propose la sortie au marché ; le journal enregistre le slippage.
- **Ordres d'entrée préparés pour J+1** (D2/D3/D4 : « acheter si ≥ pivot ») : un ordre **à seuil de déclenchement** simple devient un ordre au marché au franchissement et **ne garantit pas le prix max** ; un ordre **limite** simple peut s'exécuter **avant** la cassure attendue si le cours est sous la limite à l'ouverture. Règle unique (reprise par docs/02 §2.3 et docs/15 A2.6) : (1) **entrée** : préparée comme ordre **seulement** avec un ordre à plage de déclenchement (seuil = niveau de cassure, limite = prix max ; `stop_limit` chez Saxo est l'équivalent) ou un ordre lié entrée + stop (« if-done ») — `accounts.<compte>.entry_order_types` / `linked_orders_if_done`, à confirmer par place ; sans l'un ni l'autre, l'entrée n'est **jamais** préparée comme ordre : elle devient une alerte « à saisir manuellement » pour le prochain créneau `disponible` ; (2) **protection** : ordre lié → acceptable dans tous les modes ; sinon, l'ordre d'entrée n'est proposé que si le planning prévoit le mode `disponible` sur la fenêtre d'exécution probable (ouverture), **ou** si l'utilisateur accepte explicitement le risque d'une exécution non protégée (case à cocher tracée) ; à défaut, alerte au lieu d'ordre. Dans tous les cas, « exécuté sans protection » est une P1 immédiate. Validité : jour (≤ 3 séances). Quand une proposition expire dans l'application, l'utilisateur est invité à **annuler l'ordre resté chez le courtier** (rappel jusqu'à confirmation) ; aucun changement d'état ou de mode côté outil n'est jamais réputé avoir annulé un ordre courtier. **« Exécuté sans protection » est une alerte P1 dans tous les modes** (docs/02 §2.3).

## 7.5 Portes de discipline (checklist bloquante avant fiche d'ordre)

Portes communes à tous les détecteurs :

1. Régime ≠ rouge (ou dérogation explicite tracée).
2. **Ratio d'admission du détecteur** ≥ `scoring.buy_min_ratio` (≥ `buy_min_ratio_orange` en orange) — dénominateur limité aux blocs applicables à la stratégie (docs/06 §6.5 : le bloc catalyseurs n'entre au dénominateur que pour D1) ; confiance ≥ `scoring.buy_min_confidence`.
3. Pas de publication de résultats dans les 48 h à venir (source docs/04 ; date inconnue → réserve affichée, confiance −1).
4. Taille calculée sur le prix max et le stop glissé, risque ≤ 300 €, plafonds de 7.3 respectés (risque courant + réservé, secteur, facteur, exposition, levier), **état du portefeuille frais** (cash, quantités, ordres en attente datant de moins de `accounts.portfolio_state_max_age_minutes` — sinon proposition « à vérifier »), cash disponible sur le compte cible (docs/08) ; sinon mention explicite du cash additionnel.
5. Coupe-circuit non déclenché.
6. Pas d'entrée sur la même valeur dans les 5 séances suivant une sortie sur stop (« revenge trade »), sauf nouveau catalyseur fort.
7. Heure : pas de nouvelle proposition intraday après 16:45 Paris (sauf ordres préparés pour J+1) ni dans les 3 premières minutes après 09:00.
8. Donnée : horodatage de marché < 3 min pour une entrée intraday ; EOD officielle ou provisoire marquée pour un ordre préparé J+1.
9. **Disponibilité** (docs/02 §2.3) : en mode `reunion`, aucune proposition exigeant une réaction rapide ; en mode `absent`, aucune proposition.

Portes **spécifiques au détecteur** (admissibilité) :

| Détecteur | Condition d'admission | Catalyseur |
|---|---|---|
| D1 gap + catalyseur | Gap, RVOL, prix ≥ OR high, catalyseur classé positif forte/moyenne | **Obligatoire** (C ≥ `scoring.d1_min_c`) |
| D2 cassure momentum | Structure (MM50 > MM200, cours > MM50), RVOL ≥ 1,5, pivot cassé, extension limitée, consolidation préalable | Facultatif (bonus C) |
| D3 expansion de range | Bougie ≥ +4 %, volume > veille, consolidation préalable, TI65 | Facultatif |
| D4 pullback sur leader | Tendance intacte (RS ≥ 80, > MM50), repli maîtrisé (3–10 %), volume en baisse, **reprise confirmée** | **Non requis** |

Chaque porte franchie/bloquée est enregistrée avec la proposition ; le journal calcule le taux de dérogations.

## 7.6 Trois lectures d'une proposition

Pour éviter de confondre « bonne valeur » et « bon moment », chaque proposition affiche trois appréciations dérivées de la grille /100 et des portes (docs/06 §6.5) :

| Lecture | Question | Calcul |
|---|---|---|
| **Attractivité de la valeur** | Mérite-t-elle d'être surveillée ? | RS, structure, fondamentaux disponibles, secteur (T hors déclencheur + F + M) → faible / moyenne / forte |
| **Qualité de l'entrée** | Le prix actuel permet-il une entrée cohérente ? | Distance au stop en ADR, extension vs MM20, R/R à l'objectif 1 ≥ 2, fraîcheur de la donnée, qualité du déclencheur → faible / moyenne / forte |
| **Admissibilité** | Le portefeuille, les données et ma disponibilité le permettent-ils ? | Portes 7.5 → OK / BLOQUÉ (+ règle) |

Une valeur attractive avec une entrée de faible qualité va en watchlist « à l'affût » avec ses niveaux ; elle n'est jamais présentée comme BUY.

## 7.7 Gestion des positions existantes (HOLD/REDUCE)

- Une position est HOLD tant qu'aucun détecteur de sortie ne se déclenche ; l'outil ne propose jamais de renforcer une position perdante.
- Avant toute proposition de vente ou d'allègement, l'outil vérifie la **quantité réellement détenue** (dernier import ou confirmation) et le stop en place.
- Renforcement possible uniquement si gain ≥ 1 R, stop de l'ensemble ≥ prix d'entrée initial, risque total de la position ≤ 300 €, risque ouvert cumulé respecté.
- Affichés par position : R en cours, distance au stop (% et ADR), jours détenus, état de protection, coût SRD cumulé, date de liquidation, quantité détenue vs quantité couverte par le stop.

## 7.8 Risques globaux (rubrique 10 du rapport)

Calculés chaque soir et rappelés dans le brief : risque ouvert cumulé et réservé, scénario de gap, exposition par compte/devise/secteur/facteur/place, levier SRD, couverture disponible, positions non protégées, positions avec résultats < 5 jours, événements macro J+1, liquidation SRD à venir, drawdown (réalisé + latent) vs coupe-circuit.

---

# 08 — Comptes (PEA BoursoBank, CTO/SRD Saxo) : routage, coûts, calendrier

## 8.1 Les deux comptes

| | PEA — BoursoBank | CTO avec SRD — Saxo Banque |
|---|---|---|
| Titres | UE/EEE éligibles PEA uniquement | Tout, dont SRD Paris, US, Suisse/UK (phase 6) |
| Levier | Aucun (comptant) | SRD : couverture 20 % espèces (levier ≤ 5 théorique, **≤ 2 dans l'outil**) |
| Fiscalité | Retrait > 5 ans : prélèvements sociaux seuls (18,6 % depuis 2026, à confirmer sur avis) ; < 5 ans : 31,4 % et clôture (sauf exceptions) | PFU 31,4 % (12,8 % IR + 18,6 % PS) ou barème ; moins-values imputables 10 ans |
| Coûts spécifiques | Courtage (grille BoursoBank, plafond 0,5 % en PEA) | Courtage Saxo (Classic 0,08 % min 2 €) ; **CRD 0,023 %/jour** sur le notionnel SRD (≈ 0,7 %/mois, ≈ 8,3 %/an) ; **prorogation 0,20 %, min 10 €** ; TTF 0,4 % sur achats nets fin de journée de valeurs françaises > 1 Md€ (depuis 01/04/2025) |
| Import | Export CSV/Excel manuel (hebdo) + saisie Telegram | OpenAPI lecture seule (en séance toutes les 5 min + rapprochement 19:00) |
| Types d'ordres stop | À seuil / à plage de déclenchement (à confirmer) | Stop, stop-limit, stop suiveur, OCO (à confirmer) |
| Usage typique (jamais imposé, cf. §8.2) | Swing 2–6 semaines, moyen terme | Trades courts quand la trésorerie PEA manque, opportunités hors PEA |

## 8.2 Comparaison des trois options de compte (`domain/accounts.compare`)

Le SRD réduit l'immobilisation initiale ; il ne rend pas une opération plus rentable. Il n'y a donc **pas de préférence par défaut** : pour chaque proposition, l'outil compare les trois options admissibles et affiche le tableau.

| Option | Admissible si | Coût estimé sur l'horizon | Contraintes affichées |
|---|---|---|---|
| **PEA** (BoursoBank) | `pea_eligible_regulatory` ∧ `pea_available_boursobank` ∧ `pea_order_types_ok` ∧ cash PEA ≥ montant (ou cash additionnel autorisé) | courtage A/R BoursoBank **+ TTF si due** (la TTF s'applique aussi dans le PEA : achat d'actions françaises > 1 Md€) | Fiscalité la plus favorable ; pas de levier ; retrait avant 5 ans = clôture |
| **CTO comptant** (Saxo) | cash CTO ≥ montant | courtage A/R Saxo + TTF si due | Aucun engagement de couverture |
| **SRD** (Saxo) | `srd_status = complet` ∧ levier après trade ≤ max ∧ couverture disponible ≥ 1,5 × requise ∧ réserve de cash ≥ 20 % ∧ coût ≤ 25 % du gain à l'objectif 1 | courtage A/R + CRD × jours estimés + prorogations probables + TTF si due | Trésorerie immobilisée = couverture seulement ; liquidation ; appel de marge sous un jour |

Critères de comparaison (dans cet ordre, tous affichés) : coût total estimé, trésorerie immobilisée, durée prévue vs cycle de liquidation, risque de couverture (scénario de gap), impact fiscal (PEA vs PFU). Une option non admissible est listée avec la raison. La sortie est une **comparaison** (`options[]`, chacune avec `admissible`, `cout`, `tresorerie`, `risques`), plus une suggestion argumentée ; l'utilisateur choisit dans la fiche d'ordre. Cas particuliers : titre non éligible PEA → CTO comptant ou SRD seulement ; horizon `medium` → SRD déconseillé (coût cumulé et prorogations) mais affiché.

## 8.3 Coût d'un trade SRD (affiché dans chaque proposition CTO)

`coût_total = courtage_aller + courtage_retour + CRD_jour × notionnel × jours_détenus_estimés + prorogations × (taux_prorogation × notionnel, min 10 €) + TTF (0,4 % × montant si valeur FR > 1 Md€ et position conservée au-delà de la journée)`.

Exemple (paramètres 09/2026) : notionnel 6 000 €, 15 jours, 0 prorogation, valeur FR > 1 Md€ : courtage 2 × 4,80 € + CRD 0,023 % × 6 000 × 15 = 20,70 € + TTF 24 € ≈ **54 €**, soit 0,9 % du notionnel — à comparer à l'objectif (2 R). L'outil refuse (BLOQUÉ) un trade SRD dont le coût estimé dépasse 25 % du gain à l'objectif 1. Le traitement exact de la TTF sur les achats SRD (au règlement ou non) est **à confirmer** sur un relevé Saxo (paramètre `tax.ttf_on_srd`) ; en attendant, l'outil retient l'hypothèse prudente (TTF due).

## 8.4 Calendrier SRD (table `srd_calendar`, source Boursorama/abcbourse/Bourse Direct/Saxo, concordants)

Liquidations 2026 (jour de liquidation → règlement) : 27→30 janv. ; 24→27 fév. ; 26→31 mars ; 27→30 avr. ; 26→29 mai ; 25→30 juin ; 28→31 juil. ; 26→31 août ; **25→30 sept.** ; 27→30 oct. ; 25→30 nov. ; 28→31 déc. Règle observée : liquidation = 4e dernier jour de bourse du mois ; **encoder les dates publiées** et calculer 2027 provisoirement (26 janv., 23 fév., 24 mars, 27 avr., 26 mai, 25 juin, 27 juil., 26 août, 27 sept., 26 oct., 25 nov., 28 déc. — à confirmer à parution ; passage européen à T+1 annoncé pour octobre 2027, à vérifier).

Comportement de l'outil : à J−3 de la liquidation, chaque position SRD reçoit une alerte « décision de liquidation » (solder / proroger avec coût affiché / passer au comptant) ; le brief de J−1 rappelle l'heure limite de prorogation Saxo (paramètre `srd.prorogation_deadline_local`, à renseigner). Par défaut, l'outil recommande de **ne pas proroger** une position sous 1 R de gain.

## 8.4 bis Éligibilité PEA en trois niveaux

`pea_eligible_regulatory` (siège UE/EEE, IS — règle service-public F2385) ≠ `pea_available_boursobank` (le titre est effectivement négociable dans le PEA BoursoBank : certaines places ou lignes ne le sont pas) ≠ `pea_order_types_ok` (les ordres nécessaires — limite, à seuil de déclenchement — sont disponibles sur cette place chez BoursoBank). Les trois drapeaux sont stockés avec source et date ; le premier est calculé (docs/05), les deux autres sont **confirmés par l'utilisateur** à la première proposition sur une place donnée (case à cocher, mémorisée par place/segment) et révisés si un ordre est refusé. Un titre n'est proposé pour le PEA que si les trois sont vrais.

## 8.5 Couverture et appel de marge

- Couverture requise = Σ (notionnel × taux selon nature de la couverture) ; couverture disponible = espèces + titres pondérés (paramètres) ; ratio affiché en permanence.
- Alerte si couverture disponible < 1,5 × requise ; BLOQUÉ pour toute nouvelle entrée si < 1,3 × ; rappel que la mise en demeure AMF exige une régularisation sous un jour de bourse.
- Simulation : « si le portefeuille baisse de 5 % / 10 % demain, couverture = ? ».

## 8.6 Synchronisation des comptes

- **Saxo** : (1) **en séance**, interrogation toutes les `accounts.cto.sync_intraday_minutes` (5 min, dans les limites de débit Saxo) des positions, ordres en attente (états, exécutions partielles, stops actifs/exécutés/rejetés), cash et engagements ; (2) **immédiatement** après toute déclaration Telegram/web et **avant toute nouvelle proposition** ; (3) rapprochement complet à 19:00. Rapprochement des exécutions déclarées (provisoires, `declared_uid`) avec les exécutions courtier (`broker_fill_id`) : même compte, même instrument, quantité exacte, prix ± 0,5 %, ± 10 min → liées, les deux références conservées ; sans correspondance sous 24 h → alerte « exécution déclarée non retrouvée » ; exécution courtier sans déclaration → créée et taguée `discretionary?`. Écarts listés dans le rapport.
- **BoursoBank (hebdo, ou après chaque opération)** : import CSV ; rappel Telegram le vendredi si aucun import depuis 7 jours ; saisie manuelle via les boutons « Ordre saisi » / « Exécuté » entre deux imports. L'état PEA est **déclaratif** entre deux imports : chaque proposition PEA affiche « état déclaratif — dernier import le <date> », et l'âge de l'état est soumis à `accounts.portfolio_state_max_age_minutes` (valeur propre au PEA, plus large : `accounts.pea.state_max_age_hours`).
- **Âge maximal de l'état du portefeuille** : une proposition n'est émise que si cash, quantités et ordres en attente du compte cible datent de moins de `accounts.portfolio_state_max_age_minutes` (10 min pour Saxo) ; sinon elle est « à vérifier ». Une cotation récente ne suffit pas si le cash ou les quantités sont périmés.
- Cash par compte : valeur Saxo automatique ; valeur PEA saisie/importée, avec date de dernière mise à jour affichée.
- **Valorisation avec SRD** : valeur liquidative CTO = cash + valeur des positions au comptant + **P&L latent des positions SRD** (jamais le notionnel financé, qui est un engagement, pas un actif payé) − CRD courue ; les engagements SRD (notionnel) et la couverture requise sont affichés à part. Cette formule sert à l'équité (docs/10), au levier et aux plafonds de risque.
- **Priorité 1** : la synchronisation opérationnelle (positions, cash, ordres en attente dont stops, exécutions) est livrée en **phase 1** (docs/15) ; les tableaux de performance viennent après. Sans cash, engagements et stops réels, le moteur de risque ne peut pas fonctionner.

---

# 09 — Rapports, alertes, fiches d'ordre, interface

## 9.1 Fiche de proposition (format unique, tout canal)

Pour chaque valeur analysée (BUY/WATCH/SELL/REDUCE/HOLD/NO TRADE), conformément au projet actuel, avec les trois lectures (docs/07 §7.6) :

```
[BUY] Nom (TICKER · ISIN) — Euronext Paris — 42,35 € (RT EODHD/Cboe · marché 09:14:05 Paris · reçu 09:14:06)
Détecteur : D1 gap+catalyseur · Catalyseur : résultats S1 > attentes, guidance relevée (ActusNews 07:32)
Score 81/100 (C 24 · T 19 · F 14 · R 14 · M 10) · Confiance 4/5 · Risque moyen · Régime VERT
Ratio d'admission D1 : 0,81 = (C 24 + T 19 + R 14 + M 10 + F 14) / 100 → BUY · Attractivité : forte · Qualité de l'entrée : moyenne (extension 1,2 ATR) · Admissibilité : 8/9 portes — BLOQUÉ (porte 4, ligne ci-dessous)
Entrée : 42,40 → prix max 42,90 = OR15_high 42,48 × 1,01 (ordre limite) · Stop : 40,60 à seuil (sortie glissée 40,52) · R = 2,38 €/action (prix max − stop glissé)
Obj 1 : 47,66 (2 R) · Obj 2 : 50,04 (3 R) · Horizon : jours → swing 2–6 sem.
Taille : (300 € − 27 € de frais A/R + TTF) / 2,38 € = 114 actions → max 4 891 € · Risque réel si exécuté à 42,40 : 214 € + 27 € de frais
Compte : SRD Saxo (coût est. 15 j : 44 € = 0,9 % du notionnel, 8 % du gain brut à l'obj 1 ; levier après trade 1,5)
         alt. PEA BoursoBank : cash insuffisant (1 200 €) · alt. CTO comptant : cash 4 100 €, taille réduite à 95 actions
Risque courant + réservé après trade : 1 640 € (4,1 %) ⚠ plafond 4 % → BLOQUÉ : réduire à 100 actions ou alléger une position
État du portefeuille : Saxo synchronisé 09:13:40 (OK) · Mode : disponible
Argumentaire : … (LLM, 3 lignes, chiffres issus des données)
Vigilance : résultats du concurrent X demain ; extension 1,2 ATR au-dessus MM20 ; volume à confirmer à 09:30
Invalidation : clôture < 41,60 (OR15 low) ; RVOL < 1,5 à 10:00 ; retrait de la guidance
Sources : ActusNews (07:32), EODHD RT (09:14:05), tradingview-screener (D15, 08:59), Zonebourse (non cité)
Expire : 09:45 Paris (scan 09:15 + 30 min)
[Ordre saisi] [Watch] [Ignorer] [Voir fiche d'ordre]
```

Vérification de l'exemple (test de référence `tests/unit/test_sizing_examples.py`, paramètres de `params.example.yaml`) : entrée par ordre **limite** au prix max 42,90 (pas de glissement d'entrée) ; stop à seuil 40,60 → sortie glissée `stop_exec` = 40,60 × (1 − 0,002) = 40,519 ; R par action = 42,90 − 40,519 = **2,381 €** ; frais de dimensionnement (courtage A/R + TTF, itérés jusqu'au point fixe : 125 actions → 30,0 € → 113 → 27,2 € → 114 → 27,4 € → 114) : notionnel ≈ 4 891 € → courtage 2 × 3,91 € + TTF 0,4 % × 4 891 = 19,6 € ≈ **27 €** ; (300 − 27) / 2,381 = 114,7 → **114 actions** ; notionnel max 114 × 42,90 = 4 891 € ; risque réel si exécuté à 42,40 = 114 × (42,40 − 40,519) = 214 € (+ 27 € de frais) ; objectifs = prix max + 2 R / 3 R = 47,66 / 50,04 ; coût SRD sur 15 j = courtage 7,8 € + CRD 4 891 × 0,00023 × 15 = 16,9 € + TTF 19,6 € ≈ **44 €** ; gain brut à l'objectif 1 = 114 × 4,76 = 543 € → coûts ≈ 8 %. La ligne « BLOQUÉ » illustre le plafond de risque courant + réservé : le blocage est affiché, jamais masqué.

Une **fiche d'ordre** (bouton « Voir fiche d'ordre ») donne exactement ce qu'il faut saisir chez le courtier : compte, libellé, ISIN, sens, quantité, type d'ordre (limite au prix max en intraday ; à plage de déclenchement pour J+1), validité, **puis** l'ordre de protection à saisir après exécution (type à seuil de déclenchement, niveau, quantité). 

**États** (docs/07 §7.4, CLAUDE.md règle 9) : « Ordre saisi » crée un `order_entered` (rien d'autre) ; « Exécuté » (quantité, prix, compte, horodatage) crée une exécution **provisoire** et la position (ou l'augmente), état de protection `unprotected` pour la quantité concernée ; l'import Saxo crée l'exécution **confirmée** et la rapproche de la provisoire ; « Stop saisi » (type, niveau, quantité) met à jour la quantité protégée (`partially_protected` / `protected`). Un ordre saisi non exécuté à la clôture est rappelé (« annuler ou reconduire ? »). Une proposition expirée avec un ordre encore chez le courtier déclenche un rappel d'annulation.

## 9.2 Rapport quotidien (17:50, les 10 rubriques du projet, inchangées)

1. Synthèse macro du jour (France, Europe, États-Unis) et catalyseurs — texte LLM sur données RSS + régime.
2. État des marchés (France puis US/EU) : indices, secteurs, volatilité — tableaux calculés.
3. Analyse du portefeuille (positions FR et étrangères) : R en cours, distance au stop, actions proposées.
4. Opportunités BUY prioritaires (fiches).
5. Positions à vendre ou alléger (S1–S9).
6. Watchlist (WATCH + valeurs « à l'affût » D4 avec niveaux).
7. Valeurs à éviter (catalyseurs négatifs, résultats imminents, régime).
8. Plan d'action du jour / J+1 (ordres d'achat, de vente/allègement, stops).
9. Synthèse des ordres manuels potentiels (tableau prêt à saisir).
10. Risques globaux du jour (macro, géopolitiques, devises, SRD, couverture).

Diffusion : Telegram (résumé ≤ 12 lignes + lien), e-mail (rapport complet HTML), page `/rapport/<date>` (archivée). Le rapport sépare visuellement **Faits / Sources externes / Analyse / Opinion / Incertitudes**.

## 9.3 Brief pré-ouverture (08:45)

Régime et composantes ; positions (cours veille, stop, R, alertes SRD) ; catalyseurs classés depuis 17:40 la veille (positifs/négatifs, forte/moyenne) ; **liste d'ouverture** (≤ 15 valeurs) avec niveaux d'entrée/stop/taille pré-calculés et compte cible ; signaux externes du matin (Décision Bourse, ABC, recos brokers) ; agenda (résultats, macro, liquidation) ; rappels (stops non confirmés, import BoursoBank). Longueur Telegram ≤ 15 lignes ; e-mail complet.

## 9.4 Alertes (Telegram + e-mail selon niveau)

| Niveau | Cas | Canaux | Quota |
|---|---|---|---|
| **P1 critique** | Seuil de stop franchi (puis SELL au marché si exécution non confirmée sous 5 min) ; quantité exécutée non protégée ; catalyseur négatif fort sur position ; couverture SRD < 1,3× ; job d'ouverture non exécuté | Telegram + e-mail immédiat | Regroupées par incident : première alerte immédiate, rappel toutes les 15 min tant que l'incident dure ; incidents distincts jamais fusionnés |
| **P2 action** | BUY (portes OK) ; REDUCE objectif atteint ; décision de liquidation SRD ; trailing à modifier | Telegram (+ e-mail si non lu en 10 min : bouton « Vu ») | Selon le mode (`availability.quotas`) : ≤ 3 propositions par créneau, ≤ 6 P2 par jour en mode `disponible` ; aucune P2 intraday en `reunion` ; aucune en `absent` |
| **P3 info** | WATCH ; signaux externes reçus ; digest de midi ; rappels d'import | Telegram groupé (1 message/heure max) ; e-mail dans le rapport | ≤ 3 messages groupés/jour |
| **P4 système** | Fraîcheur des données, quotas API, sauvegardes, coûts LLM | E-mail quotidien + page `/sante` ; Telegram seulement si bloquant | — |

Règles : déduplication par (`instrument`, `compte`, `type`, `jour`), **jamais sur les P1** ; regroupement par scan ; quotas selon le mode de disponibilité (docs/02 §2.3 : `disponible` ≤ 3 propositions prioritaires par créneau, `reunion` P1 seules en temps réel, `absent` P1 + digest) ; silence 22:00–06:30 Réunion sauf P1 ; rate limit Telegram 1 msg/s ; chaque message porte l'heure Paris et Réunion et l'horodatage de marché de la cotation citée ; boutons inline (Vu / Watch / Ordre saisi / Exécuté / Stop saisi / Ignorer / Snooze 30 min).

## 9.5 Interface web (mobile-first, derrière Cloudflare Access)

| Page | Contenu |
|---|---|
| `/` **Trois décisions du jour** | En tête : les 3 décisions utiles aujourd'hui (une entrée préparée, un stop à remonter, une position à alléger…), classées par qualité d'entrée et urgence, avec bouton d'action ; puis : mode de disponibilité (bouton), régime (feu + composantes), état des données (badges + horodatage de marché), positions (R, distance stop, état de protection), cash et exposition par compte, risque ouvert cumulé / plafond, scénario de gap, levier/couverture SRD, ordres saisis non exécutés, prochaine liquidation |
| `/brief` | Brief du matin (dernier) + liste d'ouverture avec niveaux, boutons de décision |
| `/opportunites` | Cartes BUY/WATCH du jour, filtres (détecteur, place, compte), fiche d'ordre, graphique lightweight-charts (bougies + niveaux entrée/stop/objectifs, volume, MM) |
| `/positions` | Tableau + graphique par position, état d'exécution (`order_entered` / `filled_partial` / `filled`) et de protection (`unprotected` / `partially_protected` / `protected`), quantité détenue vs couverte par le stop, historique des stops, coût SRD cumulé, jours restants avant liquidation, boutons (exécuté, stop saisi, stop modifié, réduire, clôturer) |
| `/rapport/<date>` | Rapport 10 rubriques archivé, navigation par date |
| `/watchlist` | Watchlist momentum et « à l'affût » D4 ; export TradingView (`EXCHANGE:TICKER` par ligne) ; ajout manuel |
| `/journal` | Trades clos, R-multiples, notes, tags, captures (upload), filtres |
| `/kpi` | P&L (docs/10) |
| `/fiscal` | KPI fiscaux (docs/11) |
| `/revue` | Digest hebdo / mensuel, post-mortem des signaux (docs/12) |
| `/parametres` | Capital pilote, cash additionnel, cash PEA, blocages temporaires, abonnements (revue), seuils (lecture seule sauf `overrides` tracés) |
| `/sante` | Jobs (dernier run, statut), fraîcheur par source, quotas API, coûts LLM du mois, sauvegardes, version des paramètres, tunnel |
| `/import` | Dépôt CSV BoursoBank, saisie manuelle d'exécutions |
| `/ecarts` (phase 2, période de fonctionnement en parallèle) | Chaque jour : valeurs proposées par l'outil (heure, prix) vs valeurs des newsletters (heure de réception, prix), recouvrement, précocité — sert à la validation technique A2.7 (pas de conclusion de rentabilité avant docs/12 §12.2) |

Exigences : temps de rendu < 2 s en 4G ; aucune donnée sans badge de statut ni horodatage de marché ; heures affichées en Réunion (Paris entre parenthèses) ; thème clair/sombre ; aucune action d'ordre (aucun bouton « acheter/vendre » — uniquement des déclarations : « Ordre saisi », « Exécuté », « Stop saisi »).

## 9.6 API (JSON, usage interne + n8n + webhooks)

`GET /api/health`, `GET /api/jobs`, `POST /api/ingest/newsletter`, `POST /api/ingest/rss`, `POST /api/webhooks/tradingview` (secret), `GET /api/opportunities?date=`, `GET /api/positions`, `POST /api/orders` (déclaration « ordre saisi »), `POST /api/executions` (déclaration d'exécution, idempotente par identifiant unique), `POST /api/protections`, `POST /api/decisions`, `POST /api/mode`, `GET /api/report/<date>`, `GET /api/instruments/<isin>` (fiche + statut d'éligibilité), `GET /api/prices/<isin>?tf=1m|5m|1d`. Authentification : jeton Cloudflare Access vérifié + jeton applicatif pour n8n/webhooks.

---

# 10 — Portefeuille et KPI de suivi des gains et pertes (priorité 2)

## 10.1 Données de base

- `accounts` : PEA BoursoBank, CTO Saxo (sous-comptes comptant / SRD), devise EUR.
- `orders` (ordres saisis chez le courtier, déclarés ou importés) : compte, instrument, sens, quantité, type, prix limite / seuil, validité, `broker_order_id?`, état ∈ {entered, partially_filled, filled, cancelled, expired, rejected}, `proposal_id?`.
- `trades` (exécutions) : `kind ∈ {declared, confirmed}` ; `declared_uid` (déclaration manuelle : hash `account+isin+ts+qty+price`, idempotent pour les doubles clics) et `broker_fill_id` (import courtier) ; une exécution déclarée est **rapprochée** d'une exécution confirmée (docs/08 §8.6) et les deux références sont conservées sur la même ligne — l'unicité d'un identifiant ne suffit pas à éviter les doublons entre Telegram et API, seul le rapprochement le fait ; compte, instrument, sens, quantité, prix, devise, taux de change, frais de courtage, TTF, horodatage, mode (`srd` / `comptant`), source (`telegram`, `saxo_api`, `csv_boursobank`, `manual`), `order_id?`, `proposal_id?`, `detector`, `external_sources[]`.
- `positions` : état d'exécution ∈ {filled_partial, filled, closed} et état de protection ∈ {unprotected, partially_protected, protected}, indépendants, suivis par quantité ; ouvertes (quantité détenue, quantité couverte par le stop, PMP d'entrée, stop courant et type, `protected_at`, R initial, date d'ouverture, mode, compte) et fermées (prix de sortie moyen, date, motif de sortie ∈ {stop, objectif, trailing, thèse, temps, résultats, liquidation, discrétionnaire}).
- `cash_snapshots` : cash par compte et par jour (Saxo auto, PEA saisi/importé).
- `costs` : courtage, CRD (calculée quotidiennement sur les positions SRD à partir de `params.srd.crd_daily_rate`, puis rapprochée des prélèvements Saxo à l'import), prorogations, TTF, abonnements (`subscriptions`).
- `prices_eod` pour la valorisation quotidienne ; `fx_rates` (EUR/SEK, EUR/DKK, EUR/NOK, EUR/PLN, EUR/USD) EOD.

## 10.2 KPI calculés (quotidiens, `kpi_daily`, et par période)

| Famille | KPI | Détail |
|---|---|---|
| Performance | P&L réalisé, latent, total (€ et % du capital pilote) ; par compte, par détecteur, par source externe, par place, par secteur, par horizon, par mode (SRD/comptant) | Après frais ; avant/après impôt estimé (docs/11) |
| Comparaison | Performance vs CAC 40 GR, STOXX Europe 600 NR, portefeuille « 60 % CAC + 40 % STOXX » ; alpha simple ; courbe d'équité | Depuis le début, YTD, 3 mois, 1 mois, semaine |
| Qualité | Taux de réussite ; gain moyen / perte moyenne (€ et R) ; **espérance par trade (R)** ; profit factor ; ratio gain/perte ; R-multiple moyen ; médiane | Global et par détecteur/source ; fiabilité annoncée selon le nombre de trades (< 30 : « indicatif ») |
| Risque | Drawdown d'équité (vs plus haut d'équité TWR, versements neutralisés) et pertes de période en R (coupe-circuit) ; volatilité de l'équité ; exposition moyenne ; levier SRD moyen et max ; couverture minimale observée ; nombre de positions ; pertes > 1,2 R (stops mal exécutés) ; slippage moyen à l'entrée et à la sortie | |
| Coûts | Courtage total, CRD, prorogations, TTF, abonnements ; coûts / P&L brut ; coût SRD annualisé | Par mois |
| Discipline | % de trades avec stop initial, délai exécution → protection (médiane, % sous 15 min, 100 % avant la clôture), dérogations aux portes, trades hors créneaux, trades sur proposition vs discrétionnaires | |
| Activité | Nombre de propositions BUY / prises / ignorées ; temps entre alerte et « Ordre saisi », puis entre « Exécuté » et « Stop saisi » ; nombre de trades par semaine | Alimente le « ≤ 1 h/jour » |

## 10.3 Valorisation et P&L

- P&L latent = (cours EOD − PMP) × quantité − frais estimés de sortie ; positions SRD : P&L calculé sur le notionnel, CRD cumulée déduite ; devise ≠ EUR : P&L de change isolé.
- **Équité** = cash + positions au comptant valorisées + P&L latent SRD − CRD courue (docs/08 §8.6) ; le notionnel SRD n'entre jamais dans l'équité. Engagements SRD, couverture requise et disponible sont des lignes séparées.
- Le P&L réalisé d'une position SRD est reconnu à la clôture de la position (et fiscalement au règlement, docs/11).
- Courbe d'équité = cash + valorisation, par compte et consolidée ; versements/retraits neutralisés (TWR) pour la comparaison aux indices.

## 10.4 Rapprochement

- Saxo : synchronisation en séance (5 min, après déclaration, avant proposition) et rapprochement complet du soir des exécutions confirmées avec les `trades` déclarés (tolérances docs/08 §8.6) ; les exécutions non déclarées sont créées et signalées (« trade discrétionnaire ? à documenter »).
- BoursoBank : import CSV → même logique ; entre deux imports, l'état PEA est marqué « déclaratif ».
- Écarts de cash > 1 % → alerte P3.

## 10.5 Pages et exports

`/kpi` : cartes (P&L YTD, espérance R, profit factor, drawdown, coûts), courbe d'équité vs indices, tableau par détecteur/source/compte, heatmap mensuelle P&L, distribution des R-multiples. Exports CSV/XLSX de toutes les tables (usage personnel). Digest hebdo (docs/12).

---

# 11 — KPI de suivi fiscal (priorité 3)

Avertissement à afficher dans l'UI : l'outil fournit des **estimations** à partir de paramètres datés ; la référence reste l'IFU du courtier et la déclaration. L'outil n'est ni un conseil fiscal ni un logiciel de déclaration.

## 11.1 Paramètres fiscaux datés (`config/params.yaml › tax`, sources docs/16)

| Paramètre | Valeur en vigueur (09/2026) | Date d'effet | Statut |
|---|---|---|---|
| Prélèvements sociaux sur revenus du capital | **18,6 %** (CSG 10,6 % + CRDS 0,5 % + prélèvement de solidarité 7,5 %) | 01/01/2026 (LFSS 2026) | Vérifié (service-public F21618, màj 15/04/2026) |
| PFU (« flat tax ») | **31,4 %** = 12,8 % IR + 18,6 % PS | 01/01/2026 | Vérifié |
| Option barème progressif | IR au barème + 18,6 % PS ; abattements pour durée de détention uniquement sur titres acquis avant 2018 ; révocabilité annuelle annoncée pour les revenus 2026 | — | À vérifier dans la loi |
| PEA > 5 ans | PS seuls (18,6 % à confirmer sur PEA) ; PEA < 5 ans : 31,4 % et clôture (sauf exceptions) | — | Taux PEA à confirmer |
| Report des moins-values | 10 ans, les plus anciennes d'abord | — | Vérifié |
| Méthode de prix de revient | **PMP** (prix moyen pondéré d'acquisition, art. 150-0 D CGI ; BOFiP BOI-RPPM-PVBMI-20-10-20-40) : inchangé par une cession partielle, recalculé à chaque achat | — | Vérifié |
| TTF | 0,4 % sur achats nets fin de journée, actions FR capitalisation > 1 Md€ | 01/04/2025 | Vérifié |
| Fait générateur SRD | Règle admise : date de dénouement / règlement-livraison (une vente SRD après la liquidation de décembre est imposable l'année suivante) | — | À vérifier (BOFiP BOI-RPPM-PVBMI-30-10-10) |
| Loi de finances 2026 | CDHR (RFR ≥ 250 k€/500 k€) ; pas de hausse du PFU ; PLF 2027 en discussion à l'automne 2026 | — | Surveiller |

Tout changement = nouvelle ligne avec date d'effet, jamais une modification en place ; les calculs utilisent le paramètre en vigueur à la date du fait générateur.

## 11.2 Calculs (par année civile, `domain/tax`)

**CTO Saxo**
- PMP par instrument et par compte, recalculé à chaque achat (frais inclus dans le prix de revient), historique conservé.
- Plus/moins-value par cession = (prix de vente net de frais − PMP) × quantité ; devise ≠ EUR : conversion au cours du jour de chaque opération (achat et vente).
- Positions SRD : plus-value reconnue à la date de règlement ; les prorogations ne sont pas des cessions ; CRD et frais de prorogation traités comme frais déductibles ? → **paramètre `tax.srd_costs_deductible` avec note « à confirmer avec le courtier/IFU »** ; l'outil affiche les deux calculs.
- Agrégats YTD : plus-values brutes, moins-values de l'année, moins-values reportables (stock par année d'origine, expiration à 10 ans), plus-value nette imposable, **impôt estimé au PFU** et **simulation au barème** (TMI saisie par l'utilisateur ; affichée comme simulation indicative, jamais comme recommandation : la comparaison réelle dépend de l'ensemble des revenus du foyer, de la CSG déductible et des autres revenus de capitaux), acomptes éventuels.
- Simulation : « si je clôture cette position aujourd'hui, impact fiscal = ? » ; « vendre en perte avant le 31/12 pour compenser ? » (liste des positions en moins-value latente avec l'économie estimée, sans recommandation automatique de vente : information seulement).
- Rapprochement avec l'IFU Saxo (import en janvier) : écarts listés.

**PEA BoursoBank**
- Date d'ouverture, ancienneté, **date des 5 ans**, versements cumulés vs plafond 150 000 €, retraits, valeur liquidative, gain latent total, prélèvements sociaux estimés en cas de retrait (18,6 % × gain), taux historiques si PEA ancien (option `tax.pea_historic_rates`, à renseigner).
- Alerte si un titre du PEA perd son éligibilité (changement de siège) ou si une proposition tente de router un titre non éligible.
- **TTF dans le PEA** : la taxe sur les transactions financières (0,4 %, actions françaises > 1 Md€) s'applique aussi aux achats en PEA ; elle est comptée dans les coûts et dans la comparaison des comptes (docs/08 §8.2).

## 11.3 Page `/fiscal` et exports

Cartes : plus-value nette YTD CTO, impôt estimé (PFU / barème), moins-values reportables par année, TTF payée, CRD payée, PEA (ancienneté, 5 ans, versements restants). Tableaux : cessions de l'année (format proche du 2074), positions ouvertes avec PMP. Export CSV « cessions 2026 » et « positions au 31/12 ». Rappels : import IFU (janvier), échéance déclarative (avril–juin), fin d'année (fenêtre de compensation des moins-values).

---

# 12 — Journal, retour d'expérience, post-mortem des signaux (priorité 4)

Objectif : savoir **ce qui marche** (détecteur, source, heure, place, compte) et **ce qui coûte** (retards, dérogations, comportements), pour ajuster les règles par décision explicite (ADR) et non par intuition.

## 12.1 Journal des trades

- Chaque position fermée devient une entrée de journal : détecteur, sources externes, score et sous-scores à l'entrée, régime, heure d'entrée (Paris), jours détenus, R initial, **R réalisé**, motif de sortie, slippage entrée/sortie, coûts, compte, mode, notes libres, tags (`plan_respecté`, `entrée_tardive`, `stop_trop_serré`, `sortie_anticipée`, `discrétionnaire`…), captures d'écran (upload).
- **MAE / MFE** (excursion adverse / favorable maximale, en R) calculés à partir des barres 1 min/5 min de la période ; efficacité d'entrée et de sortie.
- Trades **discrétionnaires** (sans proposition) sont acceptés mais tagués ; leur statistique est isolée.

## 12.2 Post-mortem des signaux (pris ou non)

Tous les `signals` et `proposals` sont historisés ; à J+1, J+5, J+20 l'outil calcule pour chacun : rendement depuis le niveau d'entrée proposé, stop touché (oui/non, quand), objectif 1/2 atteint (oui/non, quand), R théorique. On obtient :

- **Expectancy théorique par détecteur** (tous signaux) vs **expectancy réalisée** (signaux pris) → mesure du biais de sélection de l'utilisateur.
- **Valeur de chaque source externe** : expectancy des signaux issus de chaque newsletter, taux de recouvrement avec les signaux propres, et la **précocité** : `drift_since_open` moyen entre l'ouverture et l'heure de réception (à ne pas confondre avec `gap_open`, mesuré sur la clôture précédente), et écart entre le prix d'entrée obtenu grâce au scan propre et le prix au moment de la lettre (`gain_de_précocité`, en % et en €).
- **Comparaison de trois démarches sur la même période** (le vrai juge de paix, tenu dès la phase 1 pour l'enregistrement, exploité en phase 5) : (a) **la démarche actuelle**, définie précisément et non « les lettres telles quelles » : les valeurs citées par Zonebourse et Momentum, **filtrées par la grille /100 et les règles de risque du projet actuel** (BUY ≥ 75, 0,75 %/trade, 5–15 positions), entrées au cours à l'heure de réception + délai humain, stops et objectifs de la lettre ou de la grille — reconstituée par l'outil chaque jour à partir des signaux D5 et enregistrée comme portefeuille virtuel « référence », (b) détection autonome (D1–D4 sans les lettres), (c) combinaison. Pour chacune : résultat net des coûts, drawdown, risque mobilisé (R engagés), nombre d'occasions **réellement exécutables** dans les créneaux et le mode de disponibilité, temps consacré. Comparer uniquement les valeurs ensuite citées par les lettres favoriserait rétrospectivement l'outil : **tous** les signaux comptent, y compris ceux jamais cités, ceux dont le niveau d'entrée n'a jamais été atteint, ceux survenus pendant une indisponibilité, ceux qui ont expiré ou échoué.
- **Rejeu et hypothèses** : le rejeu applique les coûts (courtage, CRD, TTF), un délai humain (`review.human_delay_minutes`, 5 min) entre l'alerte et l'ordre, et la règle conservatrice **« stop d'abord »** quand une même barre touche le stop et l'objectif sans chronologie connue. Les résultats sont calculés sur l'instantané de décision (`decision_snapshots`), jamais sur des données corrigées après coup.
- **Horizon de validation** : deux semaines de fonctionnement réel valident la technique (alertes reçues, données cohérentes, aucune erreur de dimensionnement) ; le jugement sur l'apport de la démarche demande **≥ 3 mois et ≥ 60 signaux par détecteur**, avec l'intervalle de confiance affiché.
- **Analyse par heure de scan** (09:05 / 09:15 / 09:30 / 10:00 / EOD) : quel créneau produit les meilleurs signaux ; heatmap heure × jour de semaine.
- **Par place / secteur / capitalisation / régime** : où l'élargissement de l'univers apporte réellement des opportunités.
- **Portes de discipline** : performance des trades avec dérogation vs sans.

## 12.3 Comportement (détection simple, sans jugement)

Signaux relevés et affichés dans le digest : entrée dans les 5 séances suivant un stop sur la même valeur ; taille > taille proposée ; trade hors créneau ; proposition BUY ignorée puis prise plus haut le lendemain (« FOMO ») ; quantité exécutée restée non protégée > 15 min (délai exécution → protection) ; sortie avant objectif sans signal de sortie ; série de 3 pertes suivie d'une taille accrue.

## 12.4 Revue hebdomadaire (samedi 08:00) et mensuelle

- Contenu calculé : KPI de la semaine/mois (docs/10), post-mortem des signaux, signaux manqués (BUY ignorés ayant atteint 2 R), comportements, coûts, état des abonnements, changements d'univers.
- Rédaction LLM (Sonnet) d'un digest en français : ce qui a marché, ce qui n'a pas marché, **3 questions** à se poser, propositions d'ajustement **formulées comme des ADR à valider** (ex. « relever le seuil RVOL de D1 à 2,5 : +0,3 R d'expectancy sur 40 signaux, échantillon faible »). Le LLM reçoit uniquement des agrégats calculés ; il ne recalcule rien.
- Mémoire de trading (`trader_memory`) : notes de l'utilisateur et décisions passées, injectées dans le digest suivant pour la continuité (« la semaine dernière tu avais décidé de… »).

## 12.5 Boucle d'amélioration des règles

1. Toute modification de paramètre passe par `/parametres` (override tracé : ancienne valeur, nouvelle, date, motif) ou par une nouvelle version de `params.yaml` ; les signaux historisés gardent la version utilisée.
2. Une source externe est reconduite si, après 1 mois (≥ 20 signaux), elle apporte soit une expectancy théorique positive, soit ≥ 3 signaux uniques (non couverts par les détecteurs) à expectancy positive ; sinon l'outil propose la résiliation.
3. Un détecteur dont l'expectancy théorique est < 0 sur 60 signaux passe en mode « WATCH seulement » jusqu'à révision.
4. Aucune optimisation automatique de paramètres (anti-sur-apprentissage) ; les propositions sont humaines, tracées, réversibles.

## 12.6 Pages

`/journal` (liste, filtres, fiche trade avec graphique et niveaux, MAE/MFE), `/revue` (digests, post-mortem par détecteur/source/heure, précocité, comportement, propositions d'ADR avec boutons « accepter / refuser / plus tard »).

---

# 13 — Modèle de données (PostgreSQL, schéma `bourse`)

Conventions : clés `id` (bigint), horodatages `timestamptz` en UTC, montants `numeric(18,6)`, devise ISO, `created_at/updated_at`, migrations Alembic. Les tables marquées ★ sont nécessaires au MVP (phases 0–2).

## 13.1 Référentiel et calendrier

- ★ `instruments` (docs/05 §5.5) + `instrument_aliases` (noms/tickers par provider, alias texte pour rattacher les news).
- ★ `universe_snapshots` (date, version, liste d'ISIN, filtres appliqués, hash) ; `universe_members` (snapshot_id, isin, adv_eur, market_cap, rs_rank, flags).
- ★ `pea_eligibility` (isin, eligible, confidence, source, checked_at) ; ★ `srd_eligibility` (isin, status, source, checked_at, confirmed_by_user_at) — tables d'historique ; les champs `pea_eligible`, `pea_confidence`, `srd_status` de `instruments` (docs/05 §5.5) sont une **copie dénormalisée de la dernière ligne**, recalculée par `universe_refresh`.
- ★ `market_calendar` (mic, date, status ∈ {open, closed, half_day}, open_time, close_time, source) ; ★ `srd_calendar` (liquidation_date, settlement_date, source, confirmed) ; `tick_sizes` (liquidity_band, price_low, price_high, tick).
- ★ `params_versions` (version, effective_from, yaml, hash) ; `param_overrides` (key, old, new, at, reason).
- `subscriptions` (name, provider, monthly_cost, currency, started_at, review_at, status, notes).

## 13.2 Données de marché et news

- ★ `prices_eod` (isin, date, open, high, low, close, adj_close, volume, source, market_perimeter ∈ {primary, cboe_consolidated}, received_at, data_status, official ∈ {true, provisional}) — index (isin, date).
- ★ `prices_intraday` (isin, market_timestamp, o, h, l, c, v, tf ∈ {1m, 5m}, source, market_perimeter, received_at, data_status, complete_bar bool) — partitionnée par mois ; rétention 24 mois (Parquet au-delà).
- ★ `quotes_live` (isin, market_timestamp, received_at, processed_at, last, bid, ask, price_type, volume_cum, source, market_perimeter, data_status) — dernière valeur uniquement (upsert).
- ★ `features_daily` (isin, date, atr14, adr20, mm10/20/50/200, rvol, rs_1m/3m/6m/12m, rs_rank, pivot_60, high_52w, consolidation_20, ti65, …) ; `features_intraday` (isin, date, or5_high/low, or15_high/low, rvol_15, vwap, …).
- ★ `market_regime` (date, ts, cac_vs_mm50, stoxx_vs_mm50, breadth_mm50, distribution_days, vol_pct, regime, details json).
- ★ `news_items` (id, source, published_at, fetched_at, title, url, body, isin[] , classification json {type, direction, magnitude, surprise, summary, confidence}, llm_prompt_version, llm_cost).
- ★ `newsletter_items` (id, source, received_at, subject, body_text, parsed json {values[], levels[], sentiment}, llm_prompt_version) ; `newsletter_values` (item_id, isin, direction, levels json, p_open, p_recv, gap_open, drift_since_open).
- `fx_rates` (pair, date, rate, source) ; `earnings_calendar` (isin, date, when ∈ {bmo, amc, unknown}, source, confirmed).

## 13.3 Signaux, scores, propositions, décisions

- ★ `signals` (id, isin, detector, source (détecteur interne ou nom de la lettre/flux pour `external`), ts, timeframe, entry_low, entry_high, stop_initial, targets json, horizon, evidence json, data_status, catalyst_news_id, universe_snapshot_id, params_version, external_refs json).
- ★ `premarket_watch` (date, isin, news_id, expected_direction, magnitude, added_at, source) — liste des valeurs à surveiller à l'ouverture, produite par D6 et les lettres du matin.
- ★ `scores` (signal_id, c, t, f, r, m, total, f_completeness, admission_ratio, attainable_total, admission_blocks[], confidence, risk_level, explanations json).
- ★ `proposals` (id, signal_id, action, account_recommended, account_alt, entry_zone, stop, targets, horizon, size_eur, shares, risk_eur, cost_estimate json, leverage_after, gates json {passed[], blocked[]}, status ∈ {open, expired, taken, watch, ignored}, expires_at, sent_at, channels[]).
- ★ `decisions` (proposal_id, ts, decision ∈ {seen, watch, order_entered, ignored, snoozed}, reason, via ∈ {telegram, web}).
- ★ `decision_snapshots` (proposal_id, taken_at, quotes json, features json, regime json, portfolio_state json, params_version) — instantané figé des données utilisées ; jamais mis à jour.
- ★ `availability` (ts, mode ∈ {disponible, reunion, absent}, until, source ∈ {schedule, telegram, web}) ; `availability_schedule` (weekday, from_local?, to_local?, ref ∈ {clock, market_open}?, offset_min?, mode, confirmed).
- `signal_outcomes` (signal_id, horizon_days ∈ {1, 5, 20}, return_pct, stop_hit_at, t1_hit_at, t2_hit_at, r_theoretical, computed_at).

## 13.4 Portefeuille, ordres manuels, suivi

- ★ `accounts` (id, broker ∈ {boursobank, saxo}, type ∈ {pea, cto_cash, cto_srd}, currency, cash, cash_updated_at, cash_source).
- ★ `orders` (docs/10 §10.1 : ordres saisis, `order_type ∈ {limite, seuil, plage, lie_if_done}`, états, `qty_filled`) ; ★ `trades` (docs/10 §10.1 : `kind`, `declared_uid`, `broker_fill_id`, `matched_trade_id`) ; ★ `positions` (docs/10 : états d'exécution et de protection, `qty_held`, `qty_protected`) ; ★ `stops` (position_id, ts, level, order_type ∈ {seuil, plage}, qty_covered, kind ∈ {initial, trailing, manual, linked_if_done}, broker_confirmed_at, broker_status ∈ {active, executed, rejected, expired, unknown}) ; `costs` (position_id?, date, kind ∈ {commission, crd, prorogation, ttf, subscription}, amount, source).
- `cash_snapshots` (account_id, date, cash, source) ; `broker_sync_runs` (broker, ts, status, positions_count, diffs json).
- `watchlist` (isin, added_at, reason, source, active, levels json).

## 13.5 KPI, fiscal, journal

- `kpi_daily` (date, account_id?, equity, cash, exposure, leverage, pnl_realized, pnl_unrealized, drawdown, …) ; `benchmarks_eod` (index, date, close).
- `tax_lots` (account_id, isin, ts, qty, unit_cost_eur, fees, source_trade_id) ; `tax_pmp` (account_id, isin, ts, pmp, qty_after) ; `tax_disposals` (year, account_id, isin, ts_sale, ts_settlement, qty, proceeds_eur, cost_basis_eur, gain_eur, srd, fx json) ; `tax_carryforward` (origin_year, amount, remaining, expires_year) ; `pea_events` (type ∈ {opening, deposit, withdrawal}, date, amount).
- `journal_entries` (position_id, tags[], notes, screenshots[], mae_r, mfe_r, entry_efficiency, exit_efficiency) ; `behavior_flags` (ts, kind, position_id?, proposal_id?, details) ; `reviews` (period, kind ∈ {weekly, monthly}, computed json, llm_text, adr_proposals json) ; `trader_memory` (ts, note, source ∈ {user, review}).

## 13.6 Système

- ★ `jobs_runs` (job, scheduled_for, started_at, ended_at, status, rows, error, params_version) ; ★ `data_freshness` (source, last_ok_at, last_error, quota_used, quota_limit).
- ★ `alerts` (ts, level, kind, isin?, account_id?, proposal_id?, position_id?, incident_id?, channel, message_hash, sent_at, seen_at, repeat_count) — unique (kind, isin, account_id, day) pour la déduplication des P2/P3 ; pour les P1, **regroupement par incident** (`incident_id` = kind + position/compte) : première alerte immédiate, puis rappel au plus toutes les `notifications.p1_repeat_minutes` (15) tant que l'incident dure ; deux incidents distincts ne sont jamais fusionnés.
- ★ `llm_calls` (ts, model, prompt_version, prompt_hash, input_tokens, output_tokens, cost_usd, cached, purpose) ; `llm_cache` (prompt_hash, response json, created_at).
- `adr` stockés en fichiers `docs/ADR/NNN-titre.md` (pas en base).

## 13.7 Règles d'intégrité

- Une `proposal` référence toujours un `signal` et une `score` ; un `trade` marqué `taken` référence une `proposal` ou est tagué `discretionary`.
- `positions.stop_current` ≥ `stop_initial` pour un long (le stop ne baisse jamais) — contrainte applicative testée.
- Aucun `price` sans `source`, `market_timestamp`, `market_perimeter` et `data_status` (NOT NULL).
- Une `position` n'est créée que par un `trade` (déclaré ou confirmé) ; un `order` seul ne crée jamais de position ; un `trade` déclaré non rapproché sous 24 h génère une alerte.
- `portfolio_state` (account_id, cash, positions hash, orders hash, synced_at) : toute proposition référence l'âge de cet état.
- Suppression interdite sur `signals`, `proposals`, `trades` (soft delete uniquement).

---

# 14 — Exigences non fonctionnelles : sécurité, fiabilité, exploitation, coûts

## 14.1 Sécurité

- Accès web uniquement via Cloudflare Access (policy : une seule adresse e-mail autorisée, OTP + Google, session 24 h) ; l'application vérifie le JWT `Cf-Access-Jwt-Assertion` (audience, émetteur, expiration) sur chaque requête ; aucune route accessible sans jeton, sauf `/health` (réponse minimale) et `/api/webhooks/*` (secret HMAC dédié).
- Aucun port publié sur l'hôte Windows hors réseau Docker interne ; `cloudflared` sortant uniquement ; Telegram en long polling.
- Secrets dans `.env` (gitignoré) ; `.env.example` documenté ; jetons Saxo (refresh) chiffrés au repos (clé dans `.env`) ; rotation documentée dans `RUNBOOK.md`.
- Connecteurs courtiers en lecture seule (test `tests/test_no_trading_endpoints.py` : aucune chaîne `/orders` en méthode mutante, aucune permission de trading demandée).
- Telegram : le bot n'accepte que l'`user_id` de l'utilisateur ; toute autre commande est ignorée et journalisée.
- Journalisation sans secrets ni contenu intégral de newsletters payantes.
- Dépendances : `uv lock`, `pip-audit` mensuel via hook.

## 14.2 Fiabilité et exploitation (PC Windows)

- Docker Desktop : « démarrer à l'ouverture de session » activé ; compte Windows en ouverture de session automatique + verrouillage d'écran ; `restart: unless-stopped` sur tous les conteneurs.
- Alimentation : `powercfg /change standby-timeout-ac 0`, `hibernate-timeout-ac 0`, `powercfg /hibernate off` ; veille réseau de la carte désactivée (checklist `RUNBOOK.md`).
- **Test de reprise après incident** (phase 0, réel) : redémarrage du PC, coupure de la box pendant 10 min, arrêt de Docker Desktop — la pile revient seule, les jobs manqués sont rejoués ou marqués, le watchdog externe a alerté.
- Windows Update : heures d'activité **07:00–01:00 Réunion** (18 h max ; couvre les jobs de 05:00 Paris à la sauvegarde de 23:00 Réunion et la clôture US), `NoAutoRebootWithLoggedOnUsers=1`, redémarrage volontaire planifié le **samedi à 06:00 Réunion** (avant `weekly_review`) ; script au démarrage (Task Scheduler, différé 3 min) : `docker compose up -d` + heartbeat.
- **Validation de configuration par fonctionnalité** : groupes requis au démarrage = `config_validation.required_groups` (`timezones`, `capital`, `risk`, `accounts`, `brokers` — nécessaire aux frais estimés —, `notifications`, `data`, `jobs`) → refus de démarrer s'ils manquent ; groupes optionnels (`tax`, `llm`, `detectors.d1_gap_catalyst`, `availability.schedule`…) → fonctionnalité désactivée avec mention sur `/sante`, le reste continue (le suivi des stops ne dépend jamais d'un paramètre fiscal).
- Planificateur : jobstore Postgres, `coalesce=True`, `misfire_grace_time` par job (docs/02), `max_instances=1` ; un job manqué est rejoué s'il est encore utile ; changement d'heure (mars/octobre) testé.
- **Watchdog externe** : healthchecks.io est un service **hors du PC** (« dead man's switch ») : chaque job clé le pinge, et l'absence de ping déclenche l'alerte depuis l'extérieur — une panne complète du PC, de la box ou de Docker est donc détectée même si rien ne tourne localement. Uptime Kuma (local) ne sert qu'à l'affichage. Checks attendus jours ouvrés : `brief_0845`, `open_scan_0905` (phase 3), `eod_1750`, `portfolio_sync_1900`, `backup_2300` ; grâce 15 min → alerte Telegram + e-mail ; page `/sante` affiche le dernier run et la fraîcheur de chaque source.
- **Dégradation contrôlée** : perte du WebSocket → les propositions intraday passent en « à vérifier » (statut D15, aucune fiche d'ordre) + alerte P4 ; le trou de données est marqué, jamais rebouché avec des données finalisées après coup ; quota EODHD atteint → priorisation (positions > liste d'ouverture > watchlist) ; tradingview-screener indisponible → scan EOD seul ; LLM indisponible → classification par règles (mots-clés) avec confiance réduite ; IMAP indisponible → rappel manuel.
- **Sauvegardes** : `pg_dump -Fc` quotidien 23:00 Réunion vers un dossier synchronisé cloud (OneDrive/Drive), rétention 30 jours + 12 mensuelles ; export CSV des tables métier ; **test de restauration mensuel** (script `scripts/restore_test.ps1`) ; `.env` sauvegardé séparément (chiffré).
- Logs : JSON, rotation (10 Mo × 5), niveau INFO ; erreurs des providers avec extrait de réponse tronqué.
- Robustesse des parseurs : fixtures réelles anonymisées versionnées dans `tests/fixtures/{rss,imap,csv}/` ; assertion sur la structure attendue ; alerte P4 si un parseur ne trouve plus rien 2 jours de suite (changement de format).

## 14.3 Performance

- Scan EOD de l'univers (~900 valeurs) < 10 min ; `open_scan` < 60 s ; page mobile < 2 s ; WebSocket : agrégation 1 min sans perte sur ≤ 150 symboles.
- Base : index sur (isin, date/ts) ; partitions mensuelles pour l'intraday ; archivage Parquet.

## 14.4 Qualité logicielle

- Tests unitaires (indicateurs, détecteurs sur fixtures, scoring, risque avec les exemples de docs/09 §9.1, comparaison des comptes, fiscal/PMP avec l'exemple BOFiP 100×95 + 200×105 + 100×107 → PMP 103, fuseaux), tests d'intégration (jobs sur fixtures, API `TestClient`), tests de non-régression des parseurs, test « aucun endpoint de trading », test « aucun chiffre du texte LLM absent des données ».
- **Tests d'incidents** (obligatoires par phase) : coupure WebSocket en séance, quota API atteint, réponse RSS vide ou malformée, e-mail de newsletter au format inattendu, redémarrage du PC pendant un job, changement d'heure, jour férié sur une place mais pas sur l'autre, exécution déclarée deux fois (idempotence), ordre saisi jamais exécuté, stop rejeté par le courtier.
- **Calendrier par place** : les jobs qui traitent des instruments utilisent `market_calendar` de la place de l'instrument (Xetra fermé les 24 et 31 décembre, Paris en demi-séance) ; test dédié.
- Couverture cible : `domain/` ≥ 90 %.
- CI locale : `scripts/check.ps1` (ruff, mypy, pytest, `docker compose config`) branché en hook Claude Code (Stop) ; pre-commit.
- Versionnement : SemVer ; `CHANGELOG.md` ; tag à chaque fin de phase.

## 14.5 Coûts d'exploitation (suivis dans `/sante` et `subscriptions`)

| Poste | Estimation mensuelle (09/2026) |
|---|---|
| EODHD EOD+Intraday | ≈ 27 € (29,99 $) |
| Saxo données Euronext L1 | 0–7 € (remboursé si ≥ 4 transactions) |
| Domaine personnel | ≈ 1 € |
| Cloudflare Tunnel + Access | 0 € |
| API Anthropic (Haiku classification ≈ 30 items/jour, Sonnet rapport quotidien + digest) | ≈ 4–5 $ (plafond configuré 15 $/mois, coupure au-delà avec repli règles) |
| healthchecks.io | 0 € |
| Newsletters existantes (Zonebourse, Momentum) | inchangé |
| Newsletters en test (Décision Bourse 29 €, ABC Premium 19,90 €) | selon décision, revue mensuelle |
| Électricité du PC | non chiffré |

## 14.6 Documentation vivante

`README.md` (démarrage), `RUNBOOK.md` (exploitation : redémarrage, restauration, rotation des jetons, checklist Windows, procédure Cloudflare), `docs/ADR/`, `docs/15-status.md` (avancement par critère), `CHANGELOG.md`.

---

# 15 — Roadmap par phases et critères d'acceptation (v1.1.1)

Chaque phase = une ou plusieurs sessions Claude Code, un tag Git, des critères vérifiables. L'utilisateur valide chaque phase avant la suivante. Les durées sont indicatives (soirées/week-ends). Principe de l'ordre : **d'abord des données qualifiées et un portefeuille opérationnel avec ses protections, puis quelques décisions exploitables par jour, puis la détection d'ouverture, enfin les tableaux de bord.** L'enregistrement minimal des décisions et de leurs résultats commence dès la phase 1.

## Phase 0 — Socle technique, accès distant, qualification des données, reprise après incident (2 semaines)

Livrables : dépôt initialisé (structure de CLAUDE.md), `docker compose` (app + cloudflared, Postgres existant), migrations initiales, `config/params.yaml` chargé avec **validation bloquante par fonctionnalité** (docs/14 §14.2), calendriers de marché 2026 **par place** (Euronext Paris/Amsterdam/Bruxelles, Xetra) + calendrier SRD 2026, planificateur avec un job de test, `/health`, `/sante`, bot Telegram (boutons, `/mode`), e-mail de test, Cloudflare Tunnel + Access, watchdog externe, sauvegarde, scripts Windows, `RUNBOOK.md`. **Qualification des données** (docs/04 §4.3 bis) : providers EODHD (EOD, intraday, WebSocket), tradingview-screener, yfinance, Saxo (lecture, compte réel) sur un échantillon de 10 valeurs FR/DE/NL, avec fiches `docs/providers/*.md`.

Critères :
- [ ] A0.1 `docker compose up -d` démarre tout ; après redémarrage du PC, coupure de box de 10 min et arrêt de Docker Desktop, la pile revient seule, les jobs manqués sont rejoués ou marqués, le watchdog externe a alerté (test réel).
- [ ] A0.2 Depuis le **PC pro (navigateur)** et le smartphone : `https://bourse.<domaine>/` demande l'OTP Cloudflare puis affiche `/sante`. Si le proxy pro bloque : ADR-001 sur le plan B.
- [ ] A0.3 Un job planifié à 09:05 Paris s'exécute à l'heure Réunion attendue (été et hiver simulés) ; un job sur un instrument Xetra ne tourne pas le 24/12 alors qu'un job Paris tourne en demi-séance.
- [ ] A0.4 Telegram : message avec boutons, clic enregistré, autre `user_id` ignoré, `/mode reunion 2h` change le mode et l'affiche sur `/sante`.
- [ ] A0.5 healthchecks.io alerte si le job de test manque ; `pg_dump` quotidien présent ; restauration testée.
- [ ] A0.6 `tests/test_no_trading_endpoints.py` passe ; démarrage refusé si un groupe requis manque, fonctionnalité désactivée (sans arrêt) si un groupe optionnel manque (tests) ; `pytest`, `ruff`, `mypy` verts.
- [ ] A0.7 **Qualification** : pré-qualification documentaire avant le code, puis, par prototype, pour chaque provider, fiche complétée avec délai mesuré (écart `market_timestamp`/horloge sur 3 séances), périmètre des volumes, comportement à la déconnexion **et existence réelle d'un endpoint de rattrapage des ticks manqués du flux temps réel** ; comparaison à l'interface Saxo sur 10 valeurs (cours, volumes, horodatages) ; décision consignée (ADR-002) sur les sources retenues pour l'EOD, l'intraday et le temps réel, et sur le périmètre RVOL.

## Phase 1 — Portefeuille opérationnel, protections, risque, univers et watchlist européenne (3 semaines)

Livrables : Saxo OpenAPI **live en lecture** (positions, cash, ordres en attente dont stops, exécutions), import CSV BoursoBank, états d'exécution et de protection par quantité (docs/07 §7.4, docs/09 §9.1) avec rapprochement déclaré/confirmé, saisie Telegram/web (« Ordre saisi », « Exécuté », « Stop saisi »), `position_monitor` (seuil franchi vs stop exécuté), équité avec engagements SRD, **moteur de risque** (docs/07 §7.2–7.3 : dimensionnement sur prix max, risque ouvert cumulé, secteur/facteur, scénario de gap, coupe-circuit), calendrier SRD et alertes de liquidation, référentiel instruments + univers PEA/SRD (docs/05) + drapeaux PEA à trois niveaux, backfill EOD 10 ans, `features_daily`, régime de marché, watchlist momentum, ingestion RSS + IMAP (Zonebourse, Momentum) avec `gap_open`/`drift_since_open`, classification LLM des news, enregistrement minimal des décisions et `decision_snapshots`, pages `/` (3 décisions : au début, positions/protections/risques), `/positions`, `/watchlist`, `/sante`.

Critères :
- [ ] A1.1 Positions, cash, ordres en attente Saxo identiques à l'interface Saxo pendant 5 jours, avec synchronisation en séance toutes les 5 min et après chaque déclaration ; import BoursoBank testé sur un export réel anonymisé ; écarts listés.
- [ ] A1.2 Une exécution déclarée puis importée est rapprochée en une seule ligne portant `declared_uid` et `broker_fill_id` (prix ± 0,5 %, ± 10 min) ; un double clic sur « Exécuté » ne crée qu'une déclaration ; un « Ordre saisi » ne crée pas de position ; toute quantité exécutée non protégée déclenche une P1 immédiate **quel que soit le mode**, rappelée toutes les 15 min, et une exécution partielle donne un état `filled_partial` + `partially_protected` cohérent (tests).
- [ ] A1.3 Risque ouvert cumulé, réservé, par secteur, scénario de gap et levier affichés et testés sur un portefeuille synthétique (SRD + comptant + PEA, devise SEK) ; équité conforme à docs/10 §10.3.
- [ ] A1.4 Exemples de dimensionnement de docs/09 §9.1 reproduits par les tests avec les paramètres de `params.example.yaml` (**114 actions, R 2,381 €, risque réel 214 €**) ; risque courant borné à zéro par position ; un dépassement du plafond produit `BLOQUÉ` avec la règle ; une proposition sur état de portefeuille périmé (> 10 min) est « à vérifier ».
- [ ] A1.5 Univers ≥ 600 instruments, `pea_eligible_regulatory` et `srd_status` renseignés ; diff hebdo produit ; EOD officiel complet chaque matin à 06:45 avec trous détectés.
- [ ] A1.6 Newsletters : extraction correcte sur 10 lettres de fixtures avec heure de réception, `p_open`, `p_recv`, `drift_since_open` ; RSS classés en < 2 min après récupération ; coût LLM affiché.
- [ ] A1.7 Seuil de stop franchi (cours simulé) → P1 « vérifier l'exécution » en < 60 s ; sans exécution confirmée après 5 min → proposition SELL au marché dans le même incident (tests).
- [ ] A1.8 Décisions et instantanés enregistrés pour toute proposition (même sans détecteur : lettres seules), base de la comparaison des trois démarches (docs/12).

## Phase 2 — Entrées préparées : pullbacks (D4) et cassures (D2/D3), signaux externes, scoring, trois décisions, brief et rapport (**MVP**, 3 semaines)

Livrables : indicateurs (annexe docs/06) ; détecteurs D4, D2, D3 en **EOD** (ordres J+1 à plage de déclenchement avec prix max, protection liée si possible) et D5, D6 ; S1–S9 ; scoring /100 sur total atteignable avec les trois lectures (docs/07 §7.6) ; portes communes et **par détecteur** ; comparaison des trois options de compte (docs/08 §8.2) ; fiches de proposition et d'ordre ; jobs `news_scan`, `imap_poll`, `brief_premarket`, `eod_pipeline`, `midday_digest` ; **mode de disponibilité** appliqué aux alertes et aux détecteurs ; écran « 3 décisions du jour » ; rapport 10 rubriques ; pages `/brief`, `/opportunites`, `/rapport`, `/ecarts`.

Critères :
- [ ] A2.1 Chaque indicateur a un test sur fixture ; chaque détecteur a ≥ 2 fixtures (déclenche / ne déclenche pas) et ≥ 1 test d'incident (données incomplètes, split, barre incomplète).
- [ ] A2.2 D4 sans aucun catalyseur produit un BUY sur la fixture de référence (ratio d'admission 0,96, docs/06 §6.5) et un WATCH sur la fixture médiocre (0,60) ; D1 n'existe pas encore ; une valeur attractive avec entrée de faible qualité va en watchlist « à l'affût », jamais en BUY (tests).
- [ ] A2.3 Aucune proposition sans stop structurel, prix max, taille, comparaison des comptes (TTF comptée dans les trois options quand elle est due), portes évaluées ; score global et ratio d'admission affichés avec la complétude des fondamentaux ; une date de résultats inconnue est une réserve visible.
- [ ] A2.4 En mode `reunion`, aucune P2 intraday n'est envoyée ; en mode `absent`, aucune proposition ; au plus 3 propositions prioritaires par créneau en mode `disponible` (tests + 5 jours réels).
- [ ] A2.5 Rapport quotidien : 10 rubriques ; test « aucun chiffre non sourcé dans le texte LLM » ; brief reçu à 08:45 Paris 5 jours ouvrés consécutifs.
- [ ] A2.6 Ordres J+1 (règle docs/07 §7.4) : fiche avec ordre **à plage de déclenchement** (seuil + limite = prix max) ; sans plage ni ordre lié → jamais préparée comme ordre (alerte) ; plage sans ordre lié → préparée seulement si mode `disponible` prévu sur la fenêtre ou acceptation explicite tracée ; ordre lié → tous modes (tests des quatre cas) ; à l'expiration, rappel d'annulation de l'ordre courtier ; un changement de mode ne modifie aucun ordre (test).
- [ ] A2.7 Page `/ecarts` : chaque jour, valeurs de l'outil vs valeurs des lettres (heures, prix, drift) ; **deux semaines de fonctionnement réel en parallèle du processus actuel = validation technique** (pas de conclusion de rentabilité).

## Phase 3 — Détection d'ouverture (D1), en mode `disponible`, après validation du flux temps réel (2 semaines)

Livrables : WebSocket EODHD (et/ou Saxo streaming) sur positions + liste d'ouverture + watchlist ; `RVOL_5/15`, `OR5/15` avec périmètre cohérent (ADR-002) ; D1 avec exigence d'horodatage de marché < 3 min ; `open_scan` ; `intraday_watch_start` ; D2 intraday et D4 reprise ; alerte immédiate puis argumentaire LLM ; expiration scan + 30 min.

Critères :
- [ ] A3.1 100 symboles suivis de 08:55 à 17:40 avec barres 1 min complètes/incomplètes marquées ; coupure simulée → propositions rétrogradées « à vérifier », aucune fiche d'ordre sur donnée différée (test).
- [ ] A3.2 RVOL numérateur et dénominateur sur le même périmètre (test) ; changement de source en cours d'indicateur → `source_switched` + P4.
- [ ] A3.3 Rejeu de 5 journées passées sur données intraday historiques : D1 cohérent avec les gaps observés, coûts et délai humain appliqués, règle « stop d'abord » (contrôle manuel).
- [ ] A3.4 Alertes d'ouverture à 09:05/09:15/09:30 pendant 5 jours réels en mode `disponible` ; aucune en mode `reunion`.

## Phase 4 — KPI P&L et fiscal (3 semaines)

Livrables : coûts (courtage, CRD calculée puis rapprochée, prorogations, TTF), valorisation EOD et FX, `kpi_daily`, page `/kpi`, benchmarks, exports ; abonnements et KPI d'utilité ; **fiscal** : PMP, cessions, moins-values reportables, estimation PFU + simulation barème (indicative), simulation de clôture, PEA (ancienneté, 5 ans, versements), page `/fiscal`, rapprochement IFU.

Critères :
- [ ] A4.1 CRD calculée = CRD prélevée par Saxo à ± 5 % sur un mois.
- [ ] A4.2 P&L réalisé/latent, espérance R, profit factor, drawdown (réalisé + latent), courbe d'équité vs CAC 40 GR / STOXX 600 NR ; tests sur jeu de trades synthétique (SRD prorogé, SEK, vente partielle, stop gappé).
- [ ] A4.3 Test PMP conforme à l'exemple BOFiP ; devise ≠ EUR ; SRD au règlement ; changement de taux daté appliqué selon le fait générateur ; rapprochement IFU avec écarts.

## Phase 5 — Journal, post-mortem, comparaison des trois démarches (2 semaines)

Livrables : journal enrichi (tags, MAE/MFE, captures), `signal_outcomes` J+1/5/20 sur instantanés, expectancy théorique vs réalisée par détecteur/source/heure/place/régime/mode, précocité, **comparaison démarche actuelle (référence définie en docs/12 §12.2) / détection autonome / combinaison**, comportements, revue hebdo/mensuelle avec propositions d'ADR, mémoire de trading, règle de reconduction des abonnements, pages `/journal`, `/revue`.

Critères :
- [ ] A5.1 100 % des signaux ont leurs outcomes à J+20 sur instantané ; tableau par détecteur/source avec taille d'échantillon, intervalle de confiance et mention « indicatif » sous 60 signaux.
- [ ] A5.2 Comparaison des trois démarches disponible sur la période écoulée (résultat net, drawdown, R engagés, occasions exécutables, temps).
- [ ] A5.3 Digest du samedi reçu avec 3 questions et ≥ 1 proposition d'ADR chiffrée quand l'échantillon le permet ; règle de reconduction des abonnements appliquée.

## Phase 6 — Extensions (au fil de l'eau)

Webhooks TradingView ; extension US (CTO) ; Suisse/UK en CTO ; fondamentaux et calendrier EODHD All-in-One ; EQS Newswire ; transactions de dirigeants (AMF) ; backtest léger (Backtrader) ; export des positions vers TradingView ; second utilisateur (lecture).

## Suivi

`docs/15-status.md` : tableau des critères (ID, statut, date, commit, note) tenu par Claude Code en fin de session.

---

# 15-status — Avancement des critères d'acceptation (v1.1.1)

Tenu à jour par Claude Code en fin de chaque session (statut ∈ {à faire, en cours, fait, bloqué}).

| Critère | Statut | Date | Commit | Note |
|---|---|---|---|---|
| A0.1 – A0.7 | à faire | | | A0.2 : tester d'abord le proxy du PC pro ; A0.7 : qualification des données (ADR-002) |
| A1.1 – A1.8 | à faire | | | Portefeuille, protections, risque |
| A2.1 – A2.7 | à faire | | | MVP : entrées préparées, 3 décisions, mode de disponibilité |
| A3.1 – A3.4 | à faire | | | Détection d'ouverture, après ADR-002 |
| A4.1 – A4.3 | à faire | | | KPI P&L et fiscal |
| A5.1 – A5.3 | à faire | | | Post-mortem, trois démarches |

## Décisions en attente de l'utilisateur

- Domaine personnel à acheter (nom) — phase 0.
- Demande de clé live Saxo OpenAPI (usage personnel) — à lancer **dès la phase 0** (la simulation n'a pas de données de marché ; délai d'approbation inconnu).
- Liste de 10 valeurs FR/DE/NL et relevés de référence depuis l'interface Saxo pour la qualification — phase 0 (A0.7).
- Planning de disponibilité par défaut (heures Réunion) à confirmer dans `availability.schedule` — phase 0.
- Abonnement EODHD EOD+Intraday — phase 1.
- Adresse Gmail dédiée aux newsletters et redirections — phase 1.
- Export CSV BoursoBank réel (anonymisé) pour les tests — phase 1.
- 10 newsletters de fixtures (Zonebourse, Momentum Capital) déposées dans `tests/fixtures/imap/` — phase 1 (A1.6).
- 5 journées passées à rejouer pour contrôler D1 (dates + gaps observés) — phase 3 (A3.3).
- Paramètres à renseigner : `srd.coverage_rate_broker`, `srd.prorogation_deadline_local` (interface Saxo), `pea.opening_date`, `pea.deposits_total_eur`, `risk.factor_groups`, TMI pour la simulation au barème — phases 1 à 4.
- Confirmer, à la première proposition sur chaque place, `pea_available_boursobank` et `pea_order_types_ok` — phase 2.
- Test d'accès depuis le PC pro (proxy) au hostname Cloudflare — phase 0 (A0.2).

## Journal des sessions

| Date | Phase | Fait | Non fait | À décider |
|---|---|---|---|---|
| | | | | |

---

# 16 — Glossaire, paramètres datés et sources réglementaires

## 16.1 Glossaire

- **ADR** (Average Daily Range) : amplitude quotidienne moyenne en % (docs/06 annexe). **ADR** (dans docs/ADR/) : Architecture Decision Record.
- **ATR** : Average True Range. **RVOL** : volume relatif. **RS** : force relative (rang percentile). **OR5/OR15** : opening range 5/15 min.
- **R** : risque initial par action (entrée − stop) ; gains/pertes exprimés en multiples de R. **Expectancy** : espérance de gain par trade en R.
- **MAE/MFE** : excursion adverse/favorable maximale pendant la vie d'une position.
- **PEA** : plan d'épargne en actions (BoursoBank). **CTO** : compte-titres ordinaire (Saxo). **SRD** : service de règlement différé. **CRD** : commission de règlement différé. **PMP** : prix moyen pondéré d'acquisition. **PFU** : prélèvement forfaitaire unique. **TTF** : taxe sur les transactions financières. **IFU** : imprimé fiscal unique.
- **Régime** : état du marché (vert/orange/rouge). **Portes** : checklist bloquante avant proposition. **Coupe-circuit** : gel des entrées après pertes cumulées.
- **RT / D15 / EOD / STALE / N/A** : statuts de fraîcheur des données.
- **Liste d'ouverture** : ≤ 15 valeurs suivies en temps réel dès 08:55. **Watchlist momentum** : leaders RS ≥ 80e percentile.
- **Précocité** : écart entre le prix obtenu grâce au scan propre et le prix au moment de la newsletter. **`gap_open`** : ouverture / clôture précédente − 1. **`drift_since_open`** : cours à l'heure de réception d'une lettre / ouverture − 1.
- **Trois lectures** : attractivité de la valeur / qualité de l'entrée / admissibilité (docs/07 §7.6). **Mode de disponibilité** : disponible / réunion / absent (docs/02 §2.3). **États d'une position** : exécution (order_entered → filled_partial → filled → closed) et protection (unprotected → partially_protected → protected), indépendants et suivis par quantité. **Ratio d'admission** : score d'un détecteur calculé sur ses seuls blocs applicables (docs/06 §6.5) ; **score global /100** : grille du projet, informatif.

## 16.2 Règles de marché (à encoder, sources 09/09/2026)

- Euronext Paris (continu) : pré-ouverture 07:15 ; fixing d'ouverture 09:00 ; continu 09:00–17:30 ; pré-clôture 17:30–17:35 ; fixing 17:35 ; trading at last 17:35–17:40. Demi-séances 24 et 31 décembre (clôture 14:00/14:05, à confirmer dans l'appendice Euronext).
- Fermetures Euronext 2026 (Info-Flash Euronext) : 1er janv., 3 avril, 6 avril, 1er mai, 25 déc. ; 2027 attendu : 1er janv., 26 mars, 29 mars (1er mai et 25 déc. en week-end) — à confirmer à parution.
- Xetra : continu 09:00–17:30 ; enchère d'ouverture ≈ 08:50–09:00 ; fermé 2026 : 1er janv., 3 et 6 avril, 1er mai, 24, 25, 31 déc. (journées entières) ; 2027 : 1er janv., 26 et 29 mars, 1er mai, 24, 25, 31 déc.
- Seuils de réservation Euronext : valeurs pédagogiques ± 10 % statique / ± 2 % dynamique, réservation ≥ 5 min — paramètres réels par groupe de cotation, à vérifier dans le Trading Manual.
- Pas de cotation : MiFID II RTS 11 (règlement délégué 2017/588), 6 bandes de liquidité × fourchettes de prix (ex. 20–50 € : 0,20 / 0,10 / 0,05 / 0,02 / 0,01 / 0,005) — table `tick_sizes` à charger.
- Règlement-livraison T+2 ; passage UE à T+1 annoncé pour octobre 2027 (à vérifier).
- Calendrier SRD 2026 : docs/08 §8.4.

## 16.3 Paramètres datés (extrait de `config/params.yaml`)

| Clé | Valeur | Effet | Source (consultée le 09/09/2026) |
|---|---|---|---|
| `tax.social_contributions_rate` | 0,186 | 2026-01-01 | service-public.gouv.fr F21618 (màj 15/04/2026) ; LFSS 2026 (loi 2025-1403 du 30/12/2025) |
| `tax.pfu_income_tax_rate` | 0,128 | — | idem |
| `tax.pfu_total_rate` | 0,314 | 2026-01-01 | idem |
| `tax.pea_ps_rate_after_5y` | 0,186 (à confirmer) | 2026-01-01 | Hagnéré Patrimoine, France Épargne (LF 2026) |
| `tax.loss_carryforward_years` | 10 | — | service-public F21618 |
| `tax.ttf_rate` | 0,004 | 2025-04-01 | BoursoBank, Bourse Direct FAQ 2026 |
| `tax.ttf_cap_threshold_eur` | 1 000 000 000 | — | idem |
| `tax.cost_basis_method` | `pmp` | — | CGI art. 150-0 D ; BOFiP BOI-RPPM-PVBMI-20-10-20-40 |
| `pea.eligible_countries` | UE-27 + IS, LI, NO | — | service-public F2385 (màj 22/05/2026) |
| `pea.deposit_cap_eur` | 150 000 | — | idem |
| `pea.srd_allowed` | false | — | BforBank, Café de la Bourse, Le Trader du Dimanche (CMF L221-31) |
| `srd.eligibility_full` | cap ≥ 1 Md€ et volume ≥ 1 M€/jour | — | BoursoBank aide, Euronext factsheet |
| `srd.eligibility_long_only` | volume ≥ 100 k€/jour | — | idem |
| `srd.coverage_rates` | espèces 0,20 / obligations 0,25 / actions 0,40 | — | RG AMF (médiateur AMF 12/02/2020), FranceTransactions 25/08/2026 |
| `srd.coverage_rate_broker` | à renseigner (Saxo) | — | Interface Saxo |
| `srd.crd_daily_rate` | 0,00023 (Saxo) | 2026 | home.saxo (09/09/2026) |
| `srd.prorogation_rate` / `min_eur` | 0,0020 / 10 (Saxo) | 2026 | home.saxo |
| `srd.margin_call_delay_sessions` | 1 jour de bourse | — | RG AMF art. 315-19 |
| `srd.calendar_2026` | docs/08 | 2026 | Boursorama, abcbourse, Bourse Direct, Saxo (concordants) |
| `brokers.saxo.commission_pct` / `commission_min_eur` | Classic 0,08 % min 2 € | 2026 | home.saxo (à confirmer sur relevé) |
| `brokers.boursobank.commission_grid` | grille BoursoBank (PEA plafond 0,5 %) | 2026 | boursobank.com (à confirmer sur relevé) |
| `risk.max_risk_per_trade_pct` | 0,0075 | — | Projet actuel |
| `capital.capital_pilote_eur` | 40 000 (à ajuster) | — | Projet actuel |
| `capital.cash_additionnel_max_eur` | 90 000 | — | Projet actuel |
| `risk.positions_max` | 15 (plafond ; 5 positions et 60 % d'exposition = `risk.diversification_hints`, repères seulement) | — | Projet actuel, précisé v1.1 |
| `risk.max_open_risk_pct` / `max_sector_risk_pct` / `max_factor_risk_pct` | 0,04 / 0,02 / 0,03 | — | Choix spec v1.1 |
| `risk.slippage_pct` / `risk.stop.buffer_atr_mult` / `risk.stop.policy` | 0,002 / 0,25 / `structural_first` | — | Choix spec v1.1 |
| `risk.gap_scenario_pct` | −0,10 | — | Choix spec v1.1 |
| `risk.exposure.green` / `orange` / `red` | 0,80 / 0,50 / 0 (plafonds ; `risk.diversification_hints` = repères 5 positions / 60 %) | — | Projet actuel, précisé v1.1.1 |
| `risk.post_fill_risk_tolerance` / `risk.stop.unconfirmed_exit_minutes` / `risk.sector_third_position_min_ratio` | 1,10 / 5 / 0,80 | — | Choix spec v1.1.1 |
| `scoring.watch_ratio_range` / `scoring.admission_blocks` / `scoring.f_min_available_subcriteria` | [0,60 ; 0,74] / blocs par détecteur (docs/06 §6.5) / 2 | — | Choix spec v1.1.1 |
| `data.stale_after_minutes` | RT 3 / différé 20 | — | Choix spec |
| `notifications.unread_escalation_minutes` / `availability.unprotected_fill_alert_level` | 10 / P1 | — | Choix spec |
| `user.work_slots_local` | créneaux C1/C2/C3 (heure de Paris), informatif | — | docs/02 §2.2 |
| `config_validation.required_groups` / `optional_groups` | docs/14 §14.2 | — | Choix spec v1.1.1 |
| `regime.red_min_components` | 2 | — | Choix spec |
| `risk.srd_max_leverage` | 2,0 (abs. 2,5) | — | Choix spec |
| `risk.circuit_breaker` | −3 R/semaine, −6 R/mois | — | Choix spec (benchmark) |
| `risk.earnings_blackout_hours` | 48 | — | Choix spec |
| `detectors.d1_gap_catalyst.gap_min_large` / `gap_min_other` | 0,03 / 0,05 | — | Choix spec (episodic pivot, ORB) |
| `detectors.d1_gap_catalyst.rvol15_min` | 2,0 | — | Choix spec (ORB : volume relatif) |
| `detectors.d1_gap_catalyst.expire_minutes_after_scan` / `expire_latest_local` | 30 min / 11:00 Europe/Paris | — | Choix spec |
| `detectors.d2_breakout.rs_rank_min` | 80 | — | Minervini |
| `detectors.d3_range_expansion.close_ratio_min` | 1,04 | — | Stockbee |
| `detectors.d4_pullback.pullback_pct` | 0,03–0,10 | — | Choix spec |
| `detectors.d5_external.drift_since_open_max_for_buy` | 0,02 | — | Choix spec |
| `scoring.buy_min_ratio` / `buy_min_ratio_orange` / `d1_min_c` / `c_bonus_cap` | 0,75 / 0,80 / 12 / 5 | — | Projet actuel (75/80), précisé v1.1.1 (ratio d'admission par détecteur ; C au dénominateur pour D1 seulement, bonus plafonné sinon) |
| `accounts.portfolio_state_max_age_minutes` / `cto.sync_intraday_minutes` | 10 / 5 | — | Choix spec v1.1.1 |
| `notifications.p1_grouping` / `p1_repeat_minutes` | `by_incident` / 15 | — | Choix spec v1.1.1 |
| `availability.default_mode` / `d1_allowed_modes` | `reunion` / [`disponible`] | — | Choix spec v1.1 |
| `review.human_delay_minutes` / `same_bar_rule` | 5 / `stop_first` | — | Choix spec v1.1 |
| `llm.monthly_budget_usd` | 15 | — | Choix spec |
| `data.buy_intraday_max_market_age_minutes` | 3 (âge de l'horodatage de marché ; l'âge de téléchargement ne compte pas) | — | Choix spec v1.1 |

## 16.4 Sources réglementaires et de référence (consultées le 09/09/2026)

service-public.gouv.fr/particuliers/vosdroits/F2385 (PEA) ; …/F21618 (plus-values, PFU 31,4 %) ; bofip.impots.gouv.fr BOI-RPPM-PVBMI-20-10-20-40 (PMP) ; dlapiper.com et hagnere-patrimoine.fr (LFSS 2026, CSG 10,6 %) ; placement.meilleurtaux.com (flat tax 31,4 %, 14/04/2026) ; france-epargne.fr (LF 2026, PLF 2027) ; cms.law (PEA et Brexit) ; lafinancepourtous.com (PEA-PME) ; amf-france.org (médiateur SRD 02/2020 ; tick sizes 03/2018) ; euronext.com (srd_factsheet_fr.pdf ; trading-hours-holidays ; Info-Flash calendrier 2026) ; boursobank.com (SRD, frais) ; home.saxo et help.saxo (SRD, calendrier, données de marché) ; boursedirect.fr (tarifs 06/01/2026, guide SRD, calendrier SRD, guide fiscal) ; abcbourse.com (liquidations, séance, SRD) ; boursorama.com (calendrier SRD, filtres PEA/SRD) ; francetransactions.com (25/08/2026) ; letraderdudimanche.com (14/08/2026) ; cashmarket.deutsche-boerse.com (Xetra) ; eur-lex.europa.eu (2017/588) ; eodhd.com (pricing, real-time feed 26/08/2026) ; developer.saxo (OpenAPI, application live « usage personnel ») ; github.com/shner-elmo/TradingView-Screener ; developers.cloudflare.com (Tunnel, Access, OTP) ; core.telegram.org/bots ; platform.claude.com/docs (pricing) ; apscheduler.readthedocs.io ; docs.docker.com (Desktop Windows) ; learn.microsoft.com (powercfg, Windows Update) ; healthchecks.io.

---

# config/params.example.yaml

```yaml
# config/params.example.yaml — BOURSE-PILOT
# Copier en config/params.yaml. Chaque bloc "effective_from" est une date ISO ; le code utilise la
# valeur en vigueur à la date du fait générateur. Aucun de ces nombres ne doit être codé en dur.
version: "2026.09.3"   # v1.1.1

timezones:
  storage: "UTC"
  market: "Europe/Paris"
  display: "Indian/Reunion"

user:
  telegram_user_id: 0            # à renseigner
  email: "..."                   # adresse autorisée par Cloudflare Access et destinataire des rapports
  work_slots_local:              # créneaux utilisateur, heure de Paris
    brief: "08:45"
    open: ["09:05", "09:45"]
    evening: ["17:45", "18:30"]

capital:
  effective_from: "2026-09-09"
  capital_pilote_eur: 40000
  cash_additionnel_max_eur: 90000     # mobilisable uniquement pour score >= 85, confiance >= 4, risque faible

risk:
  capital_base: "pilote"              # les plafonds se calculent sur le capital pilote, pas sur l'équité du jour
  max_risk_per_trade_pct: 0.0075      # 300 € pour 40 000 €
  slippage_pct: 0.002                 # appliqué à la sortie sur stop à seuil (stop_exec = stop × (1 − x)) et à l'entrée seulement si l'ordre ne borne pas le prix
  risk_current_floor_zero: true       # risque courant borné à zéro par position (jamais de risque négatif compensateur)
  post_fill_risk_tolerance: 1.10      # au-delà de 110 % du risque prévu après exécution → proposition d'ajustement
  max_position_pct_of_capital: 0.20
  max_position_pct_of_adv: 0.05
  max_open_risk_pct: 0.04             # risque cumulé jusqu'aux stops (positions + ordres d'entrée en attente)
  max_sector_risk_pct: 0.02
  max_factor_risk_pct: 0.03
  factor_groups: {}                   # ex. defense: ["FR0000073272", "..."]
  gap_scenario_pct: -0.10
  positions_max: 15
  diversification_hints: {positions: 5, exposure: 0.60}   # REPÈRES affichés, jamais des objectifs ni des blocages
  exposure:                           # plafonds (bornes hautes) par régime
    green: 0.80
    orange: 0.50
    red: 0.0                          # aucune nouvelle entrée
  size_multipliers:
    regime_orange: 0.5
    risk_high: 0.5
  fx_haircut: 0.10
  max_fx_exposure_pct: 0.30
  max_positions_per_sector: 2
  max_positions_per_sector_green_high_score: 3
  sector_third_position_min_ratio: 0.80   # ratio d'admission requis pour une 3e position sectorielle en régime vert
  max_exposure_per_non_paris_market_pct: 0.40
  srd_max_leverage: 2.0
  srd_abs_max_leverage: 2.5
  srd_cash_reserve_pct: 0.20
  srd_coverage_buffer_alert: 1.5
  srd_coverage_buffer_block: 1.3
  srd_max_cost_pct_of_target1: 0.25
  earnings_blackout_hours: 48
  earnings_policy_on_position: "reduce_50"   # reduce_50 | sell | hold_if_gain_ge_1r
  circuit_breaker:
    weekly_loss_r: 3
    monthly_loss_r: 6
    resume_size_multiplier: 0.5
  revenge_trade_cooldown_sessions: 5
  no_new_buy_after_local: "16:45"
  no_new_buy_before_local: "09:03"
  stop:
    policy: "structural_first"        # structurel − tampon ; volatilité seulement en repli ; rejet si trop loin
    buffer_atr_mult: 0.25
    max_distance_adr: 2.5             # borne générale ; chaque détecteur a la sienne (stop_max_adr_mult)
    protection_order_type_default: "a_seuil_de_declenchement"
    unconfirmed_exit_minutes: 5       # seuil franchi sans exécution confirmée → proposition SELL au marché (même incident P1)
    trailing_1r: {ref: "low_5d", atr_mult: 0.5}
    trailing_2r: {ref: "mm10", atr_mult: 0.5}
    move_to_breakeven_at_r: 2.0
    reduce_at_target1_pct: 0.5
  pyramiding:
    allowed: true
    min_gain_r: 1.0

universe:
  markets_p0: ["PA", "AS", "BR", "XETRA"]
  markets_p1: ["MI", "MC", "ST", "CO", "HE", "OL"]
  markets_p2: ["LS", "IR", "VI", "WAR"]
  min_adv_eur_cto: 1000000
  min_adv_eur_pea: 500000
  min_market_cap_eur: 300000000
  small_cap_threshold_eur: 1000000000
  min_price: 1.0
  min_history_sessions: 250
  momentum_watchlist_rs_rank_min: 80
  momentum_watchlist_max: 100
  opening_list_max: 15
  realtime_symbols_max: 150
  blacklist: []

regime:
  red_min_components: 2               # rouge si >= 2 composantes rouges ; orange si >= 1 orange ou 1 rouge
  breadth_green: 0.50
  breadth_orange: 0.35
  distribution_days_orange: 4
  distribution_days_red: 6
  vol_pct_orange: 60
  vol_pct_red: 85

detectors:
  d1_gap_catalyst:
    enabled: true
    scan_times_local: ["09:05", "09:15", "09:30", "10:00", "10:30"]
    gap_min_large_adv_eur: 5000000
    gap_min_large: 0.03
    gap_min_other: 0.05
    rvol15_min: 2.0
    gap_high_risk: 0.15
    entry_max_pct_above_or_high: 0.01
    stop_fallback_atr_mult: 1.0       # utilisé seulement si day_low indisponible
    stop_max_adr_mult: 1.5
    expire_minutes_after_scan: 30
    expire_latest_local: "11:00"
    phase: 3                          # modes autorisés : availability.d1_allowed_modes (source unique)
  d2_breakout:
    enabled: true
    pivot_lookback: 60
    rvol_min: 1.5
    rs_rank_min: 80
    max_extension_atr_above_mm20: 1.5
    consolidation_20_max: 0.15
    entry_buffer_pct: 0.003
    entry_max_pct_above_pivot: 0.01
    stop_fallback_atr_mult: 1.5
    stop_lookback_low: 10
    stop_max_adr_mult: 2.5
  d3_range_expansion:
    enabled: true
    close_ratio_min: 1.04
    rvol_min: 1.3
    close_in_top_third: true
    consolidation_days: [3, 20]
    ti65_min: 1.05
    rs_rank_min: 70
    entry_max_pct_above_high: 0.005
    stop_max_adr_mult: 2.5
    time_stop_sessions: 5
  d4_pullback:
    enabled: true
    rs_rank_min: 80
    pullback_pct: [0.03, 0.10]
    ma_refs: ["mm10", "mm20"]
    volume_dryup_ratio: 0.8
    rsi_range: [40, 60]
    entry_max_pct_above_prev_high: 0.008
    stop_max_adr_mult: 1.5
    min_reward_risk_t1: 2.0
    catalyst_required: false
  d5_external:
    enabled: true
    confirm_bonus_c: 5
    drift_since_open_max_for_buy: 0.02
    sources: ["zonebourse", "momentum_capital", "decision_bourse", "abc_premium", "abc_rss", "tradingsat_rss", "bourse_direct"]
  d6_catalysts:
    enabled: true
    rss_poll_seconds_premarket: 300
    rss_poll_seconds_day: 900
    max_items_per_cycle: 50
    llm_model: "claude-haiku-4-5"
  exits:
    d1_time_stop_sessions: 10
    thesis_mm20_close_below_days: 2
    rs_rank_exit: 50
    time_stop_sessions_default: 40
    srd_liquidation_alert_days_before: 3

scoring:
  buy_min_ratio: 0.75                 # total / total_atteignable (F non attribué si fondamentaux indisponibles)
  buy_min_ratio_orange: 0.80
  d1_min_c: 12                        # catalyseur requis pour D1 seulement
  c_bonus_cap: 5                      # D2/D3/D4 : min(C, 5) ajouté au numérateur du ratio d'admission, pas au dénominateur
  admission_blocks:                   # blocs au dénominateur du ratio d'admission (F ajouté si >= 2 sous-critères disponibles)
    d1_gap_catalyst: ["C", "T", "R", "M"]
    d2_breakout: ["T", "R", "M"]
    d3_range_expansion: ["T", "R", "M"]
    d4_pullback: ["T", "R", "M"]
  f_min_available_subcriteria: 2
  buy_min_confidence: 3
  watch_ratio_range: [0.60, 0.74]
  exceptional_min_ratio: 0.85
  surprise_points_with_consensus: 5
  announced_magnitude_points: 3

accounts:
  portfolio_state_max_age_minutes: 10  # âge max de l'état (cash, quantités, ordres) pour émettre une proposition
  pea:
    broker: "boursobank"
    order_types_stop: ["a_seuil_de_declenchement", "a_plage_de_declenchement"]   # à confirmer
    entry_order_types: ["limite", "a_seuil_de_declenchement", "a_plage_de_declenchement"]   # à confirmer par place
    linked_orders_if_done: false      # à confirmer (protection liée à l'entrée)
    import: "csv"
    import_reminder_days: 7
    state_max_age_hours: 168          # état déclaratif entre deux imports ; affiché sur chaque proposition PEA
  cto:
    broker: "saxo"
    order_types_stop: ["stop", "stop_limit", "trailing_stop", "oco"]              # à confirmer
    entry_order_types: ["limit", "stop", "stop_limit"]                            # à confirmer ; stop_limit = équivalent Saxo de l'ordre à plage de déclenchement
    linked_orders_if_done: true       # à confirmer (ordres liés Saxo)
    import: "openapi_readonly"
    sync_intraday_minutes: 5          # en séance, + immédiat après déclaration et avant toute proposition
    sync_time_local: "19:00"          # rapprochement complet
    match_tolerance: {price_pct: 0.005, minutes: 10}

srd:
  effective_from: "2026-01-01"
  eligibility_full: {min_market_cap_eur: 1000000000, min_daily_volume_eur: 1000000}
  eligibility_long_only: {min_daily_volume_eur: 100000}
  coverage_rates: {cash: 0.20, bonds: 0.25, equities: 0.40}
  coverage_rate_broker: null            # à renseigner depuis l'interface Saxo
  crd_daily_rate: 0.00023               # Saxo, 09/2026
  prorogation_rate: 0.0020              # Saxo
  prorogation_min_eur: 10
  prorogation_deadline_local: null      # à renseigner (heure limite Saxo le jour de liquidation)
  margin_call_delay_sessions: 1
  calendar_2026:                        # liquidation -> règlement
    - ["2026-01-27", "2026-01-30"]
    - ["2026-02-24", "2026-02-27"]
    - ["2026-03-26", "2026-03-31"]
    - ["2026-04-27", "2026-04-30"]
    - ["2026-05-26", "2026-05-29"]
    - ["2026-06-25", "2026-06-30"]
    - ["2026-07-28", "2026-07-31"]
    - ["2026-08-26", "2026-08-31"]
    - ["2026-09-25", "2026-09-30"]
    - ["2026-10-27", "2026-10-30"]
    - ["2026-11-25", "2026-11-30"]
    - ["2026-12-28", "2026-12-31"]
  calendar_2027_provisional: ["2027-01-26", "2027-02-23", "2027-03-24", "2027-04-27", "2027-05-26", "2027-06-25", "2027-07-27", "2027-08-26", "2027-09-27", "2027-10-26", "2027-11-25", "2027-12-28"]

tax:
  - effective_from: "2026-01-01"
    social_contributions_rate: 0.186
    pfu_income_tax_rate: 0.128
    pfu_total_rate: 0.314
    pea_ps_rate_after_5y: 0.186         # à confirmer
    loss_carryforward_years: 10
    cost_basis_method: "pmp"
    ttf_rate: 0.004
    ttf_cap_threshold_eur: 1000000000
    srd_taxable_event: "settlement"     # à confirmer (BOFiP)
    srd_costs_deductible: null          # à confirmer
    ttf_on_srd: true                    # hypothèse prudente, à confirmer sur relevé Saxo
    pea_historic_rates: null            # taux historiques si PEA ouvert avant 2018 (à renseigner)
  - effective_from: "2018-01-01"
    social_contributions_rate: 0.172
    pfu_income_tax_rate: 0.128
    pfu_total_rate: 0.30

pea:
  eligible_countries: ["AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR","HU","IE","IT","LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE","IS","LI","NO"]
  deposit_cap_eur: 150000
  pea_pme_cap_eur: 225000
  srd_allowed: false
  leverage_allowed: false
  opening_date: null                    # à renseigner
  deposits_total_eur: null              # à renseigner (ou import)

brokers:
  saxo:
    commission_pct: 0.0008
    commission_min_eur: 2
    market_data: {euronext_l1_eur: 7, refunded_if_trades_per_month: 4, xetra_l1_eur: 7}
  boursobank:
    commission_grid: "à renseigner"     # cf. relevé ; plafond PEA 0,5 %

data:
  primary_eod: "eodhd"
  primary_realtime: "eodhd_ws"
  secondary_realtime: "saxo"
  screener: "tradingview_screener"
  fallback_eod: "yfinance"
  buy_intraday_max_market_age_minutes: 3   # âge de l'horodatage de MARCHÉ (pas du téléchargement) ; BUY EOD calculé sur EOD
  perimeter_consistency_required: true     # un indicateur ne mélange jamais deux périmètres (primaire vs Cboe)
  silent_source_switch_allowed: false
  stale_after_minutes: {realtime: 3, delayed: 20}
  eodhd: {plan: "eod_intraday", daily_quota: 100000, ws_symbols_per_connection: 50}
  tradingview_screener: {min_interval_seconds: 60, max_requests_per_scan: 6, limit_per_request: 1500, fallback: "eodhd_rest_delayed"}
  rss_feeds:
    - {name: "actusnews", url: "https://www.actusnews.com/fr/rss", user_agent: "browser"}
    - {name: "abc_bourse", url: "https://www.abcbourse.com/rss/displaynewsrss"}
    - {name: "tradingsat", url: "https://www.tradingsat.com/rssbourse.xml"}
    - {name: "globenewswire_fr", url: "à renseigner"}
  imap:
    host: "imap.gmail.com"
    poll_minutes: 10
    window_local: ["07:30", "19:00"]
    senders:
      zonebourse: ["@zonebourse.com", "@marketscreener.com"]
      momentum_capital: ["@capital.fr", "@prismamedia"]
      decision_bourse: ["@decisionbourse.fr"]
      abc_premium: ["@abcbourse.com"]

notifications:
  telegram: {enabled: true, rate_limit_per_sec: 1, quiet_hours_local_display: ["22:00", "06:30"]}
  email: {enabled: true, smtp: "smtp.gmail.com", daily_limit: 100}
  dedup_key: ["instrument", "account", "kind", "day"]   # P2/P3
  p1_grouping: "by_incident"          # P1 : une alerte par incident puis rappel, jamais fusionnées entre incidents distincts
  p1_repeat_minutes: 15
  unread_escalation_minutes: 10

availability:
  default_mode: "reunion"
  schedule:                           # heure Réunion sauf ref: market_open (relatif à l'ouverture de Paris : 11:00 été / 12:00 hiver) ; en dehors : default_mode
    - {days: "1-5", from: "06:30", to: "08:30", mode: "disponible"}     # avant le travail
    - {days: "1-5", ref: "market_open", offset_min: [0, 45], mode: "disponible", confirmed: false}   # À CONFIRMER : sinon les scans D1 sont hors créneau en été (choix affiché sur /sante)
    - {days: "1-5", from: "12:00", to: "13:00", mode: "disponible"}     # pause déjeuner
    - {days: "1-5", from: "19:30", to: "22:00", mode: "disponible"}     # revue du soir
    - {days: "6-7", from: "08:00", to: "20:00", mode: "disponible"}
  unprotected_fill_alert_level: "P1"  # dans tous les modes
  quotas:
    disponible: {proposals_per_slot: 3, p2_per_day: 6, p3_grouped_per_day: 3}
    reunion: {proposals_per_slot: 0, p2_per_day: 0, p3_grouped_per_day: 1}
    absent: {proposals_per_slot: 0, p2_per_day: 0, p3_grouped_per_day: 0}
  d1_allowed_modes: ["disponible"]

config_validation:
  required_groups: ["timezones", "capital", "risk", "accounts", "notifications", "data", "jobs"]
  optional_groups: ["tax", "llm", "availability.schedule", "detectors.d1_gap_catalyst", "subscriptions", "brokers"]   # manquant → fonctionnalité désactivée, mention sur /sante

review:
  human_delay_minutes: 5              # délai humain appliqué en rejeu entre alerte et ordre
  same_bar_rule: "stop_first"         # barre touchant stop et objectif sans chronologie connue
  technical_validation_days: 10
  profitability_min_days: 90
  profitability_min_signals_per_detector: 60

llm:
  provider: "anthropic"
  classify_model: "claude-haiku-4-5"
  write_model: "claude-sonnet-4-6"
  temperature: 0
  monthly_budget_usd: 15
  cache: true
  prompt_versions_dir: "app/llm/prompts"

jobs:                                    # heure de Paris sauf mention tz ; market_days_only => calendrier de la place de chaque instrument traité (docs/02 §2.4)
  universe_refresh: {cron: "30 6 * * 1", misfire_grace_min: 240, market_days_only: false}
  eod_backfill_check: {cron: "45 6 * * 1-5", misfire_grace_min: 240, market_days_only: true}
  news_scan:
    premarket: {cron: "*/5 7-8 * * 1-5", market_days_only: true}
    day: {cron: "*/15 9-18 * * 1-5", market_days_only: true}
    misfire_grace_min: 5
  daily_regime: {cron: "30 7 * * 1-5", misfire_grace_min: 60, market_days_only: true}
  imap_poll: {every_minutes: 10, window_local: ["07:30", "19:00"], days: "1-5", market_days_only: true}
  zonebourse_lists_daily: {cron: "30 8 * * 1-5", enabled: false, market_days_only: true}   # activer si Zonebourse Premium
  brief_premarket: {cron: "45 8 * * 1-5", misfire_grace_min: 20, market_days_only: true}
  intraday_watch_start: {cron: "55 8 * * 1-5", misfire_grace_min: 10, market_days_only: true, enabled: false}   # phase 3
  open_scan: {times_from: "detectors.d1_gap_catalyst.scan_times_local", misfire_grace_min: 10, market_days_only: true, enabled: false}   # phase 3 ; modes : availability.d1_allowed_modes
  position_monitor: {every_seconds: 60, window_local: ["09:00", "17:40"], half_day_end: "14:10", market_days_only: true}
  portfolio_sync_intraday: {every_minutes_from: "accounts.cto.sync_intraday_minutes", window_local: ["09:00", "17:40"], also_after: ["declaration", "before_proposal"], market_days_only: true}
  midday_digest: {cron: "0 12 * * 1-5", market_days_only: true}
  eod_pipeline: {cron: "50 17 * * 1-5", half_day: "14:20", misfire_grace_min: 240, market_days_only: true}
  portfolio_sync: {cron: "0 19 * * 1-5", misfire_grace_min: 240, market_days_only: true}
  backup: {cron: "0 23 * * *", tz: "Indian/Reunion", market_days_only: false}
  weekly_review: {cron: "0 8 * * 6", market_days_only: false}
  us_open_scan: {cron: "35 15 * * 1-5", enabled: false, market_days_only: true}          # phase 6

subscriptions:
  - {name: "EODHD EOD+Intraday", monthly_cost: 29.99, currency: "USD", started: null, review: null, status: "planned"}
  - {name: "Saxo Euronext L1", monthly_cost: 7, currency: "EUR", note: "remboursé si >= 4 transactions/mois"}
  - {name: "Zonebourse", monthly_cost: null, status: "active"}
  - {name: "Momentum Capital", monthly_cost: null, status: "active"}
  - {name: "Décision Bourse", monthly_cost: 29, status: "test_planned"}
  - {name: "ABC Bourse Premium", monthly_cost: 19.90, status: "test_planned"}
```

---

# Séquence de prompts Claude Code — BOURSE-PILOT (v1.1.1)

Mode d'emploi : une session par phase (ou par sous-lot), `/clear` entre deux, plan validé avant tout code, commit à chaque critère validé, **petit lot par session terminé par un comportement observable** (page, message Telegram, ligne de rapport). Copier-coller les prompts tels quels (adapter les éléments entre `<…>`). Les prompts sont en français ; Claude Code écrit le code en anglais (convention CLAUDE.md).

## Prompt 0 — Prise en main de la spécification

```
Tu démarres le projet BOURSE-PILOT. Lis dans l'ordre : CLAUDE.md, docs/00, docs/01, docs/02, docs/03, docs/07, docs/15, docs/15-status.md, config/params.example.yaml. Ne code rien.
Rends-moi : (1) un résumé en 15 lignes de ce que tu as compris des objectifs et des dix règles absolues ; (2) la liste des points de la spec que tu juges ambigus ou contradictoires, avec ta proposition par défaut pour chacun (rappel : params.yaml > docs/07 > docs/06 > autres, et toute divergence est signalée, jamais résolue en silence) ; (3) les décisions qui m'appartiennent (comptes, abonnements, domaine, clés API) et ce dont tu as besoin de ma part pour la phase 0 ; (4) un plan de la phase 0 (fichiers, tests, ordre) en 10 étapes maximum, en séparant le socle technique et la qualification des données.
Enregistre tes propositions par défaut dans docs/ADR/000-hypotheses-initiales.md, statut "proposé". Attends ma validation.
```

## Prompt 1 — Phase 0 (a) : socle et reprise après incident

```
Phase 0 (docs/15 §Phase 0), plan validé, sous-lot « socle ». Implémente : structure du dépôt (CLAUDE.md), pyproject avec uv, docker-compose (service app + cloudflared ; Postgres existant : <nom du conteneur / URL>), chargement de config/params.yaml avec validation Pydantic par fonctionnalité (groupes requis → démarrage refusé ; groupes optionnels manquants → fonctionnalité désactivée avec mention sur /sante ; tests des deux cas), migrations Alembic initiales (tables système, market_calendar PAR PLACE pour XPAR/XAMS/XBRU/XETR 2026, srd_calendar 2026, availability), APScheduler avec jobstore Postgres et un job de test respectant market_days_only et le calendrier de la place, routes /health et /sante, bot Telegram (long polling, filtrage sur mon user_id, message de test avec boutons dont le clic s'enregistre en base, commande /mode disponible|reunion|absent [durée]), envoi d'e-mail de test, scripts Windows (scripts/check.ps1, scripts/backup.ps1, tâche au démarrage), RUNBOOK.md (checklist Windows de docs/14 §14.2, procédure Cloudflare Tunnel + Access, procédure de test de reprise après incident), tests/test_no_trading_endpoints.py.
Contraintes : tests unitaires pour le chargement des paramètres, la conversion Europe/Paris ↔ Indian/Reunion (été et hiver), le calendrier par place (24/12 : Xetra fermé, Paris demi-séance) ; rien en dur.
Avance par étapes ; après chaque étape, lance uv run pytest -q et commit. Termine en remplissant docs/15-status.md (A0.1 → A0.6) avec ce qui reste à faire de mon côté (domaine, Cloudflare, test depuis le PC pro, test de reprise réel).
```

## Prompt 2 — Phase 0 (b) : qualification des données

```
Phase 0, sous-lot « qualification des données » (docs/04 §4.3 bis, docs/15 A0.7). Lis docs/04 et docs/13 §13.2. Implémente l'interface MarketDataProvider (EOD, intraday, realtime) avec des DTO Pydantic portant source, market_timestamp, received_at, processed_at, price_type, market_perimeter, data_status, puis les providers EODHD (EOD bulk, intraday 5 min, WebSocket ws/eu avec barres 1 min et indicateur complete_bar), tradingview_screener (limit >= 1000, <= 6 requêtes par scan, désactivable), yfinance (secours EOD), Saxo OpenAPI en lecture sur mon compte RÉEL (la simulation n'a pas de données de marché) : infoprices + streaming sur 10 valeurs.
Écris un script scripts/qualify_providers.py qui, sur 10 valeurs FR/DE/NL que je te donne, pendant 3 séances, mesure : délai (market_timestamp vs horloge), écarts de cours et de volumes entre providers et vs l'interface Saxo (je saisirai les valeurs de référence dans tests/fixtures/qualification/), trous, comportement à la déconnexion/reprise. Produis une fiche docs/providers/<nom>.md par provider (modèle de docs/04 §4.3 bis) et un ADR-002 proposant : source EOD, source intraday, source temps réel, périmètre RVOL (numérateur et dénominateur sur le même périmètre), et ce qu'on ne fait PAS (pas de rebouchage par l'API historique intraday, pas de changement de source silencieux ; le rattrapage des ticks par l'endpoint dédié du flux temps réel n'est retenu que si la qualification prouve qu'il existe et fonctionne). Tests avec réponses enregistrées (respx/vcr). Critère A0.7.
```

## Prompt 3 — Phase 1 : portefeuille opérationnel, protections, risque, univers

```
Phase 1 (docs/15 §Phase 1). Lis docs/07 (référence risque), docs/08, docs/09 §9.1, docs/10 §10.1, docs/05, docs/13. Plan d'abord, découpé en 4 sous-lots ; je valide chaque sous-lot.
Sous-lot A — Comptes et états : Saxo OpenAPI live lecture (positions, balances, orders dont stops, closedpositions, exécutions) avec synchronisation en séance toutes les 5 min + immédiate après déclaration + avant toute proposition, import CSV BoursoBank (export réel anonymisé dans tests/fixtures/csv/), tables orders/trades/positions/stops avec états d'exécution et de protection PAR QUANTITÉ (CLAUDE.md règle 9), exécutions déclarées (declared_uid) rapprochées des exécutions confirmées (broker_fill_id) selon docs/08 §8.6 (tests : double clic = une déclaration ; déclaration + import = une ligne avec les deux références ; import sans déclaration = trade tagué discretionary?), déclarations Telegram/web « Ordre saisi » / « Exécuté » / « Stop saisi » (type à seuil vs à plage, quantité couverte), P1 immédiate dans tous les modes pour toute quantité exécutée non protégée (regroupée par incident, rappel toutes les 15 min), position_monitor distinguant seuil franchi et stop exécuté, portfolio_state avec âge (proposition « à vérifier » au-delà de 10 min), équité avec engagements SRD (docs/10 §10.3).
Sous-lot B — Moteur de risque (docs/07 §7.2–7.3) : dimensionnement sur prix max (limite non dépassable) et stop glissé (stop_exec = stop × (1 − slippage)), marge de change qui réduit la quantité, frais A/R + TTF avec une itération, recalcul après exécution ; risque initial / courant (borné à zéro par position) / réservé, plafond cumulé, secteur/facteur, scénario de gap, exposition (repères, pas objectifs), levier et couverture SRD, coupe-circuit sur pertes de période en R (réalisées + variation du latent), drawdown d'équité TWR ; tests de référence = exemple de docs/09 §9.1 (114 actions, R 2,381 €, 214 € de risque réel) et un portefeuille synthétique multi-comptes.
Sous-lot C — Univers et données : référentiel instruments + alias, universe_refresh (docs/05), drapeaux PEA à trois niveaux (docs/08 §8.4 bis), srd_eligibility, backfill EOD 10 ans, features_daily (annexe docs/06, tests sur fixtures), market_regime, watchlist momentum, calendrier SRD et alertes J−3.
Sous-lot D — Ingestion et enregistrement : RSS (feeds de params.yaml, UA navigateur, dédup, fixtures), IMAP Gmail (adresse dédiée, heure de réception) avec parseurs Zonebourse et Momentum Capital sur fixtures, gap_open et drift_since_open, classification LLM D6 (schéma JSON, cache, budget, repli mots-clés), decision_snapshots et decisions pour toute proposition, pages /, /positions, /watchlist, /sante.
Critères A1.1 → A1.8. Un ADR par choix non couvert. docs/15-status.md à jour.
```

## Prompt 4 — Phase 2 : entrées préparées, scoring, trois décisions, brief et rapport (MVP)

```
Phase 2 (docs/15 §Phase 2). Lis docs/06, docs/07 §7.5–7.6, docs/08 §8.2, docs/09, docs/02 §2.3. Plan puis 4 sous-lots validés un par un.
Sous-lot A — Détecteurs EOD : D4 pullback (aucun catalyseur requis), D2 cassure et D3 expansion (ordres J+1 à plage de déclenchement avec prix max ; protection liée « if-done » si le courtier le permet, sinon règle de docs/02 §2.3 sur la disponibilité), D5 externe, D6 catalyseurs, S1–S9 (S1 = seuil franchi → P1 « vérifier l'exécution », SELL au marché seulement après 5 min sans exécution confirmée) ; stops STRUCTURELS avec tampon et rejet si distance > max (docs/07 §7.4) ; ≥ 2 fixtures par détecteur + 1 test d'incident chacun ; historisation.
Sous-lot B — Score global /100 (information) ET ratio d'admission par détecteur (dénominateur = blocs applicables : C seulement pour D1 ; bonus C plafonné à 5 pour D2/D3/D4 ; F partiel sur sous-critères disponibles), explanations[], confiance, risque, trois lectures (attractivité / qualité de l'entrée / admissibilité), portes communes + portes par détecteur, comparaison des trois options de compte avec TTF dans les trois quand elle est due ; tests table-driven dont les fixtures de référence de docs/06 §6.5 (D4 sans catalyseur : 0,96 → BUY ; 0,60 → WATCH).
Sous-lot C — Mode de disponibilité (planning + /mode), quotas d'alertes par mode, sélection des 3 décisions du jour (classement par qualité d'entrée et urgence), page / réorganisée, /brief, /opportunites, /rapport/<date> (10 rubriques ; test « aucun chiffre non sourcé »), /ecarts.
Sous-lot D — Jobs : news_scan (pré-ouverture, séance, soir), imap_poll, brief_premarket, eod_pipeline (clôtures provisoires, réconciliation 06:45), midday_digest ; fiches d'ordre avec prix max et protection ; rappel d'annulation des ordres courtier à l'expiration.
Critères A2.1 → A2.7. Puis deux semaines de fonctionnement réel en parallèle de mon processus actuel : validation technique uniquement, consignée dans docs/15-status.md.
```

## Prompt 5 — Phase 3 : détection d'ouverture (D1), après validation du flux

```
Phase 3 (docs/15 §Phase 3). Lis docs/06 D1 et annexe, docs/04 §4.5, ADR-002. Plan puis implémentation : intraday_watch_start (positions + liste d'ouverture + watchlist, ≤ 150 symboles, connexions de 50), barres 1 min avec complete_bar, RVOL_5/RVOL_15 et OR5/OR15 sur le périmètre retenu par l'ADR-002 (numérateur et dénominateur identiques ; source_switched + P4 sinon), backfill de référence 20 j à 08:45 pour la liste d'ouverture, D1 avec exigence d'horodatage de marché < data.buy_intraday_max_market_age_minutes (sur donnée différée : candidat « à vérifier », sans fiche d'ordre), D2 intraday et D4 reprise, open_scan aux heures de params.yaml, actif seulement en mode disponible, alerte immédiate puis argumentaire LLM, expiration scan + 30 min, commande cli run-job open_scan --date <date> sur données intraday historiques avec coûts, délai humain (review.human_delay_minutes) et règle « stop d'abord ». Critères A3.1 → A3.4 ; je fournis 5 journées à rejouer.
```

## Prompt 6 — Phase 4 : KPI P&L et fiscal

```
Phase 4 (docs/15 §Phase 4). Lis docs/10, docs/11, docs/16 §16.3. Plan puis implémentation : coûts (courtage, CRD quotidienne calculée puis rapprochée, prorogations, TTF), valorisation EOD et FX, kpi_daily, page /kpi (cartes, courbe d'équité vs CAC 40 GR et STOXX 600 NR, tableaux par détecteur/source/compte/mode, heatmap mensuelle, distribution des R), exports, subscriptions avec KPI d'utilité ; domain/tax : lots, PMP (test = exemple BOFiP → 103), cessions avec conversion de devise, SRD au règlement (paramètre), moins-values reportables (10 ans), estimation PFU + simulation barème étiquetée « indicative », simulation de clôture, PEA (ancienneté, 5 ans, versements), rapprochement IFU, page /fiscal avec l'avertissement. Tests sur jeu de trades synthétique (SRD prorogé, SEK, vente partielle, stop gappé) ; test d'un changement de taux daté. Critères A4.1 → A4.3.
```

## Prompt 7 — Phase 5 : journal, post-mortem, comparaison des trois démarches

```
Phase 5 (docs/15 §Phase 5). Lis docs/12. Implémente : journal (tags, notes, captures, MAE/MFE depuis prices_intraday), signal_outcomes J+1/5/20 calculés sur decision_snapshots pour TOUS les signaux (pris ou non, lettres seules comprises, survenus en mode reunion/absent, expirés), expectancy théorique vs réalisée par détecteur/source/heure/place/régime/mode avec taille d'échantillon et intervalle de confiance (« indicatif » sous 60), précocité (drift_since_open, gain_de_précocité), comparaison des trois démarches (démarche actuelle reconstituée selon docs/12 §12.2 / autonome / combinaison : résultat net des coûts, drawdown, R engagés, occasions exécutables, temps), comportements (docs/12 §12.3), weekly_review (agrégats + digest LLM Sonnet avec 3 questions et ADR chiffrés), trader_memory, règle de reconduction des abonnements, pages /journal et /revue. Critères A5.1 → A5.3.
```

## Prompt de revue adversariale (à lancer en fin de chaque phase, dans une session séparée)

```
Tu es relecteur adversarial du projet BOURSE-PILOT (lis CLAUDE.md et docs/15). Sans modifier le code, cherche : (1) toute voie par laquelle un ordre pourrait être passé ou une permission de trading demandée ; (2) tout cours/nombre affiché sans source, horodatage de marché ou statut, et tout endroit où l'âge de téléchargement masque le retard de marché ; (3) tout calcul de prix/stop/taille/score délégué au LLM ; (4) toute erreur de fuseau ou de calendrier (jours fériés par place, changement d'heure, demi-séances) ; (5) tout paramètre métier codé en dur ou dupliqué hors params.yaml ; (6) toute fuite de secret ; (7) tout chemin où un ordre saisi devient une position sans exécution confirmée, ou où une exécution peut être comptée deux fois ; (8) tout indicateur mélangeant deux périmètres de données ou changeant de source en silence ; (9) les tests manquants pour les critères de la phase, en particulier les tests d'incidents. Rends un rapport classé par gravité avec fichier:ligne, et propose les tests à ajouter. Ne corrige rien.
```

## Prompt de session courante (après la phase 2, usage quotidien)

```
Lis docs/15-status.md et les 3 derniers ADR. Voici ce que j'ai observé aujourd'hui : <observations>. Propose (sans coder) les corrections et leur priorité ; pour toute modification de paramètre, prépare un override tracé (ancienne valeur, nouvelle, motif) plutôt qu'un changement de code. Code uniquement ce que je valide.
```
