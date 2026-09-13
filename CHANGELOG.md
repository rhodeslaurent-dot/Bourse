# Changelog

Format SemVer ; un tag par fin de phase (`v0.1` = fin de phase 0).

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
