# Changelog

Format SemVer ; un tag par fin de phase (`v0.1` = fin de phase 0).

## [Unreleased] — Boîte mail partagée — 2026-10-21

- `imap_poll` peut lire une boîte Gmail existante : recherche serveur limitée aux non-lus récents des expéditeurs de `data.imap.senders`, lecture en `BODY.PEEK` (état inchangé), seules les lettres reconnues sont marquées lues ; dossier ou libellé Gmail réglable (`data.imap.mailbox`, défaut `INBOX`).

## [Unreleased] — Surveillance externe — 2026-10-21

- healthchecks.io : seuls les jobs de `notifications.watchdog_jobs` envoient un signal (défaut : `saxo_token_keepalive`, `backup`, `eod_backfill_check`, `portfolio_sync`, `position_monitor`), soit 5 checks à régler au lieu d'une quinzaine et pas de fausse alerte le week-end ; état sur `/sante` ; RUNBOOK §4 avec la planification de chaque check.

## [Unreleased] — Apprentissage : doublons — 2026-10-21

- Les analyses répétées (ou servies par le cache) réenregistraient les mêmes propositions : questions et propositions sont maintenant dédoublonnées (texte normalisé, ou clé + valeur pour un paramètre), y compris contre les propositions déjà acceptées ou refusées ; message « rien de nouveau » et nombre de doublons écartés. Migration 0008 : doublons existants marqués `duplicate` / `dismissed` (rien n'est supprimé).
- Le dossier de la revue décrit ce que l'outil fait déjà (`contexte_outil` : journal des décisions, calcul quotidien des résultats, revue hebdomadaire, notes) et liste les propositions et questions en attente ; prompt `learning_review_v2` : ne rien proposer d'existant, listes vides acceptées, priorité aux questions tant qu'aucun résultat n'est définitif.
- Fenêtre d'attente pendant les traitements longs et dans le panneau assistant.

## [Unreleased] — Correctif LLM — 2026-10-21

- **Tous les appels réels à Claude échouaient** depuis le passage au SDK `anthropic` 1.x : `messages.parse()` n'accepte plus `temperature` (TypeError), donc classification des news, extraction des lettres, assistant et revue retombaient sur le repli par règles. La température 0 (CLAUDE.md règle 3) est désormais transmise par `extra_body`, acceptée par les modèles épinglés (Sonnet 4.6, Haiku 4.5). Nouveau test sur le vrai chemin du SDK (transport HTTP simulé).
- Les erreurs d'API sont décrites (code et message de l'API, sans secret) et journalisées ; réponse tronquée détectée ; `llm.review_max_tokens` 4000.
- Écriture refusée sans Cloudflare Access : page explicative, message dans le panneau assistant, état sur `/sante`.

## [Unreleased] — Apprentissage et assistant global — 2026-10-21

- Spécification `docs/18`, ADR-009 (boucle d'apprentissage sans ré-entraînement ni optimisation automatique) ; migration 0007 : `signal_outcomes`, `learning_reviews`, `learning_questions`, `learning_suggestions`, `trader_memory`, `assistant_messages.page`.
- Watchlist momentum recalculée chaque matin de marché (`watchlist_refresh`, 07:15) ; les jobs peuvent porter une note sur leur ligne `jobs_runs`.
- Résultat de chaque proposition passée, prise ou non (`domain/outcomes`, job `outcomes_update` 07:20) ; statistiques par décision, détecteur, source, régime, heure et tranches (`domain/learning`) ; historique comparable figé dans l'instantané, tri des opportunités et rétrogradation BUY → WATCH (docs/12 §12.5.3).
- Revue IA (`weekly_review` le samedi et bouton « Lancer une analyse ») : constats, questions à l'utilisateur, propositions d'amélioration, toutes contrôlées par Python ; leçons retenues ; page `/apprentissage`.
- Assistant disponible sur chaque page (panneau flottant, faits de la page, continuité, leçons ; boutons « Demander ») ; navigation : « Apprentissage » remplace « Assistant », menu « Plus » sur mobile.
- `params-init --update` ajoute aussi le bloc `learning` et les nouvelles étapes de la journée. Total : 272 tests.

## [Unreleased] — Interface v2 — 2026-10-21

- Cahier des charges `docs/17-interface-v2.md` (critères A17.1–A17.6) ; migration 0006 : `notes`, `workflow_steps`, `assistant_messages`, `kpi_daily`.
- Libellés français partout (`app/web/labels.py`) ; noms de valeurs à la place des ISIN ; notes libres persistantes sur positions, watchlist, propositions, valeurs et journée.
- `/opportunites` : explication en une ligne calculée en Python, portes bloquantes, détail dépliable, décisions depuis le web.
- `/performance` : réalisé FIFO (`domain/performance`, pur), latent, qualité, répartition, courbe d'équité et barres mensuelles en SVG ; job `kpi_snapshot` (18:10 Paris).
- `/journee` : déroulé quotidien cliquable (lancer un job, ouvrir une page, cocher), paramétré par `workflow` dans `params.yaml`.
- `/assistant` : questions sur une valeur ou le portefeuille, réponses du LLM limitées au dossier de faits avec rejet des nombres non sourcés ; fiche `/valeur/<isin>` (graphique, indicateurs expliqués, position, propositions, news, note) ; recherche globale.
- Correctif : la passerelle Claude est instanciée au démarrage et transmise aux jobs (auparavant jamais créée). `params-init --update` ajoute aussi les blocs manquants (`workflow`). Total : 250 tests.

## [Unreleased] — Interface refondue — 2026-10-21

- ADR-008 : Tailwind compilé en local (`/static/app.css`, plus de CDN, HTMX retiré), système d'interface partagé (`_ui.html`), navigation haute et barre mobile, mode sombre, cartes et badges d'état, tuiles de risque avec jauge, tableau des jobs et watchlist lisibles, filtres `eur`/`pct`. Captures vérifiées sur ordinateur, mobile et mode sombre.

## [Unreleased] — Saxo live : instruments sans ISIN — 2026-10-20

- ADR-006 : Saxo OpenAPI ne renvoie pas d'ISIN ; correspondance `Uic` + `Symbol` (`VK:xpar`) → ISIN par alias persistant, `ticker_saxo`, ticker + MIC du référentiel, puis `config/instruments_map.yaml` (`saxo:` / `saxo_uic:`). Symbole `…:xxxx` = radié/bloqué, non suivi. Positions non résolues visibles (`saxo-check`, rapport `unmapped`, P3 `broker_unmapped`). Total : 197 tests.
- Secrets : le mot de passe d'une URL (`scheme://user:mot_de_passe@hôte`) est masqué dans les logs et les erreurs de jobs (y compris celles déjà stockées, au démarrage) ; `backup` passe le mot de passe par `PGPASSWORD` et n'inclut jamais la commande dans son erreur ; URL libpq sans suffixe `+psycopg`.
- `params-init --update` ajoute les jobs manquants depuis l'exemple ; `/sante` liste les jobs implémentés absents de `params.yaml`.
- `universe_refresh` : seules les places ayant un calendrier sont chargées (M7) ; une place refusée par EODHD est ignorée avec sa raison (P4) et les codes disponibles sont journalisés ; `run-job` affiche les logs en console.
- `news_scan` : `news_items.guid` en `Text` (migration 0005, les guid RSS dépassent 255 caractères sous Postgres) ; un flux refusé au flush est isolé par savepoint et ne casse plus les suivants.
- ADR-007 : jeton Saxo stocké avec son expiration, job `saxo_token_keepalive` (20 min, tous les jours), volume `./data` partagé CLI/conteneur, P3 `saxo_auth_expired` une fois par jour, état sur `/sante`. Valeurs factices de `.env` ignorées ; `.dockerignore`.
- CLI : `env-setup` (dont `database`), `env-set`, `params-init`, `saxo-auth [--sim]` avec capture automatique du code, `saxo-check [--sim]`, `.env` chargé automatiquement ; jetons live/sim séparés.

## [Unreleased] — Revue adversariale phase 0–1 — 2026-09-15

- 24 constats traités (ADR-005) : double comptage déclaration/import, horodatage BoursoBank en heure de Paris, ventes Saxo importées, devises et FX réels, calendrier par place dans le moniteur, Pentecôte Xetra, statuts Saxo tolérants, idempotence sur 2 min, ventes orphelines conservées, risque incomplet affiché (jamais estimé), risque réservé au stop de la proposition, IMAP en UTC, séance en date de Paris, niveaux LLM vérifiés dans le texte, prix de modèle inconnu → repli, écritures refusées sans authentification, régime paramétré, screener par place, jobs interrompus, secrets rédigés. Total : 184 tests.

## [Unreleased] — Phase 1 (D) ingestion et enregistrement — 2026-09-15

- `app/data/providers/rss` (feedparser, UA navigateur, dédup GUID/URL, flux illisible → erreur), `imap_gmail` (parseur `.eml` pur, extraction ISIN/alias/niveaux/sens, client IMAP).
- `app/llm` : passerelle Claude (`messages.parse` + Pydantic, température 0, cache par hash, plafonds journalier/mensuel, coût paramétré), prompts versionnés, repli par règles.
- Tables `news_items`, `newsletter_items`, `newsletter_values`, `premarket_watch`, `signals`, `proposals`, `decisions`, `decision_snapshots`, `llm_cache` (migration 0004).
- Services `news` (classification, `premarket_watch`), `newsletters` (`p_open`, `p_recv`, `gap_open`, `drift_since_open`, signal externe + proposition WATCH + instantané, porte drift), `decisions`.
- Jobs `news_scan`, `imap_poll` ; API `/api/ingest/rss`, `/api/ingest/newsletter`, `/api/decisions`, `/api/opportunities`, `/api/proposals/{id}` ; coût LLM sur `/sante`. Total : 169 tests.

## [Unreleased] — Phase 1 (C) univers et données — 2026-09-15

- `app/domain/indicators` (ATR14 Wilder, ADR20, RVOL, RS Minervini + rang percentile, MM, TI65, pivot 60, plus haut 52 s, consolidation, RSI14, gap, distribution days, breadth, percentile de volatilité), `domain/regime` (feu tricolore, composantes manquantes signalées), `domain/universe` (éligibilité PEA à trois sources avec préfixes pièges, statut SRD par règles, filtres de liquidité, watchlist momentum).
- Tables `instruments`, `instrument_aliases`, `universe_snapshots`, `universe_members`, `pea_eligibility`, `srd_eligibility`, `prices_eod`, `features_daily`, `market_regime`, `watchlist`, `fx_rates`, `earnings_calendar` (migration 0003).
- Services `market_data` (upsert EOD officiel/provisoire, trous par calendrier de place, features + rang RS sur l'univers, FX) et `universe` (rafraîchissement, instantané + diff, watchlist, export TradingView).
- Jobs `universe_refresh` (listes EODHD + screener), `eod_backfill_check` (bulk 06:45, trous, features), `daily_regime` ; `backfill` CLI ; alerte de liquidation SRD à J−3 dans `portfolio_sync`.
- Pages `/watchlist` (ajout manuel, export), API `/api/instruments/<isin>` ; calendriers 2025 ajoutés ; fixtures OHLC figées. Total : 157 tests.

## [Unreleased] — Phase 1 (B) moteur de risque — 2026-09-15

- `app/domain/risk` : `size_position` (prix max selon le type d'ordre, stop glissé, frais courtage A/R + TTF itérés jusqu'au point fixe, marge de change réduisant la quantité, plafonds capital/ADV, multiplicateurs) ; `risk_if_filled` et tolérance post-exécution ; risque initial / courant (borné à zéro, quantité non couverte = perte totale) / réservé ; `portfolio_risk` (cumulé, secteur, facteur, gap, exposition, devises, places, levier, couverture) ; `check_candidate` → `BLOQUÉ : <règle> (clé params)` ; coupe-circuit en R ; drawdown TWR.
- `app/domain/accounts` : coût d'un trade SRD (exemple docs/08 §8.3 ≈ 44 €), applicabilité TTF.
- `services/risk_view` + page `/` « Trois décisions du jour » (phase 1 : protections, risques, comptes, fraîcheur de l'état).
- Tests de référence : 114 actions, R 2,381 €, 214 € ; portefeuille synthétique multi-comptes SEK. Total : 136 tests.

## [Unreleased] — Phase 1 (A) comptes et états — 2026-09-15

- `app/domain/orders` : machine à états exécution (`order_entered` → `filled_partial` → `filled` → `closed`) et protection (`unprotected` → `partially_protected` → `protected`) par quantité ; `declared_uid` idempotent ; rapprochement déclaré/confirmé (± 0,5 %, ± 10 min) ; stop jamais abaissé.
- `app/domain/monitor`, `alerts`, `portfolio` : S1 (seuil franchi → SELL au marché après 5 min, même incident), quantité non protégée → P1 tous modes, dédup P2/P3, équité avec engagements SRD, levier et couverture.
- Tables `accounts`, `orders`, `trades`, `positions`, `stops`, `portfolio_state`, `broker_sync_runs`, `cash_snapshots` (migration 0002) ; `services/portfolio` (déclarations, import de fills, état daté) ; `services/alerts`.
- Saxo lecture : positions/ordres/cash → DTO, jobs `portfolio_sync` et `portfolio_sync_intraday` ; import CSV BoursoBank (parseur tolérant + assertions, fixtures anonymisées).
- Job `position_monitor` ; API `/api/orders`, `/api/executions`, `/api/protections`, `/api/positions` ; pages `/positions`, `/import` ; Telegram `/exec`, `/stop`, `/ordre`.
- Tests : 117.

## [Unreleased] — Phase 0 (b) qualification des données — 2026-09-13

- `app/data` : DTO (`Quote`, `EodBar`, `IntradayBar`, `ScreenerRow`) avec source, `market_timestamp`, `received_at`, `processed_at`, `price_type`, `market_perimeter`, `data_status` ; `classify_status` fondé sur le délai déclaré et l'âge de l'horodatage de marché ; interface `MarketDataProvider`.
- Providers : EODHD (EOD, bulk, intraday 5 min, REST différé, WebSocket `ws/eu` → barres 1 min, `complete_bar`, reconnexion), tradingview-screener (≤ 6 requêtes, ≥ 60 s, désactivable), yfinance (secours EOD), Saxo OpenAPI lecture seule (`ReadOnlyViolation` avant tout appel, refresh token chiffré Fernet, `saxo-auth`).
- `scripts/qualify_providers.py` + `app/data/qualification.py` : enregistrement des observations, délai mesuré, écarts vs interface Saxo, ratio de volume (périmètre), trous du flux, rapport Markdown.
- `docs/providers/*.md` (pré-qualification documentaire), ADR-002 proposé, `data.declared_delay_minutes` dans `params.example.yaml`.
- Tests : 83.

## [Unreleased] — Phase 0 (a) socle — 2026-09-13

- Dépôt initialisé à partir du CDC v1.1.1 : `CLAUDE.md`, `docs/00`→`16`, `config/params.example.yaml`, `prompts/`.
- `app/config` : chargement de `params.yaml`, validation bloquante des groupes requis, désactivation par fonctionnalité des groupes optionnels (visible sur `/sante`).
- `app/domain` : fuseaux (UTC / Europe/Paris / Indian/Reunion), calendriers par place (XPAR, XAMS, XBRU, XETR 2026–2027, demi-séances), calendrier SRD, mode de disponibilité (planning + `/mode`).
- `app/db` : tables système (`params_versions`, `param_overrides`, `market_calendar`, `srd_calendar`, `availability`, `jobs_runs`, `data_freshness`, `alerts`, `telegram_events`, `llm_calls`) + migration Alembic `0001`.
- `app/scheduler` : jobs depuis `params.yaml`, `market_days_only` par place, variantes demi-séance, `jobs_runs`, ping healthchecks.io, job de test, job `backup` (pg_dump + rotation).
- `app/api`, `app/web` : `/health`, `/api/health`, `/api/jobs`, `/api/mode`, `/sante` (mobile-first), middleware Cloudflare Access + jeton applicatif.
- `app/notify` : Telegram (long polling, `user_id` unique, boutons, `/mode`, `/test`), e-mail SMTP, abstraction `notify`.
- Docker Compose (app + cloudflared, Postgres existant), scripts Windows (`check.ps1`, `backup.ps1`, `restore_test.ps1`, `startup.ps1`), `RUNBOOK.md`.
- Tests : 60 (config, fuseaux, calendriers, disponibilité, gating, Telegram, API, migrations, aucun endpoint de trading).
