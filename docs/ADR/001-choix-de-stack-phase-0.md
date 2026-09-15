# ADR-001 — Choix de stack et d'organisation retenus en phase 0

Statut : **accepté** (validation utilisateur du 2026-09-15) · Date : 2026-09-13

## Contexte

`docs/03` impose des contraintes (A1–A10) et recommande une stack ; certains choix sont laissés ouverts (n8n vs `imaplib`, scission worker/web, jobstore).

## Décisions

1. **Stack** : Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, APScheduler 3.11 (jobstore SQLAlchemy sur Postgres en exploitation), httpx, Pydantic v2, Jinja2 + HTMX + Tailwind (CDN), python-telegram-bot 22 (long polling), `smtplib` (Gmail STARTTLS), PyJWT pour le jeton Cloudflare Access. Gestion des dépendances : `uv` (`uv.lock` versionné).
2. **Un seul processus** `app` (FastAPI + APScheduler dans le `lifespan`) ; scission worker/web seulement si l'UI ralentit (même image, commande différente).
3. **Ingestion IMAP dans `app`** (`imaplib`, parseurs testables sur fixtures) plutôt que n8n ; n8n conservé en option via `POST /api/ingest/*` protégé par `X-App-Token`.
4. **Base** : Postgres 16 existant en exploitation ; SQLite en tests/dev. Les modèles restent portables ; les objets Postgres-only (partitions) arriveront dans des migrations dédiées.
5. **Sécurité** : middleware vérifiant `Cf-Access-Jwt-Assertion` (JWKS de l'équipe, audience, émetteur, expiration, e-mail autorisé) dès que les variables Cloudflare sont définies ; `/health` et `/api/webhooks/*` exemptés ; jeton applicatif pour n8n ; Telegram filtré sur `TELEGRAM_USER_ID` avec journalisation des tentatives.
6. **Calendriers** : source de vérité en fichier versionné `config/market_calendars.yaml` (par MIC, avec source), chargé en base (`market_calendar`) par `seed-calendar` ; le domaine refuse une année non encodée.
7. **Logs** JSON sur stdout (Docker les fait tourner) avec masquage des messages contenant une clé de type secret.
8. **Convention `from_store`** : une date lue en base sans fuseau (SQLite) est réputée UTC (règle 4) ; toute autre date naïve est refusée.

## Conséquences

- Aucune dépendance à un GPU, à Redis ou à un broker de messages (A9, A10).
- Le passage au Postgres existant se fait par `DATABASE_URL` + réseau Docker externe (`POSTGRES_NETWORK`).
- Le test de reprise réel (A0.1) et l'accès depuis le PC pro (A0.2) restent à faire par l'utilisateur (RUNBOOK).
