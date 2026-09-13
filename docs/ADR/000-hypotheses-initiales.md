# ADR-000 — Hypothèses initiales et points ambigus de la spécification

Statut : **proposé** (à valider par l'utilisateur) · Date : 2026-09-13 · Session 1 (Prompt 0 + Prompt 1 socle)

Référence : `docs/00` → `docs/16`, `config/params.example.yaml` (v1.1.1). Ordre de priorité en cas de divergence :
`config/params.yaml` > `docs/07` > `docs/06` > autres (CLAUDE.md règle 8). Rien n'est résolu en silence : chaque point ci-dessous
porte la proposition par défaut appliquée dans le code et attend confirmation.

## 1. Résumé de ce qui est compris (15 lignes)

1. Outil personnel d'aide à la décision (PEA BoursoBank + CTO/SRD Saxo), auto-hébergé sur un PC Windows (Docker), un seul utilisateur à La Réunion.
2. **Jamais de passage d'ordre** : connecteurs courtiers en lecture seule, test automatisé (`tests/test_no_trading_endpoints.py`).
3. **Jamais de donnée inventée** : source, horodatage de marché, statut (RT/D15/EOD/STALE/N/A) ; l'âge de téléchargement ne masque jamais le retard de marché.
4. **Le LLM ne calcule pas** : il classe, résume, rédige ; calculs déterministes en Python testés.
5. Fuseaux : stockage UTC, planification Europe/Paris, affichage Indian/Reunion ; calendrier **par place** (Xetra fermé 24/12, Paris demi-séance).
6. Paramètres métier datés dans `params.yaml`, jamais en dur ; validation bloquante par fonctionnalité (groupes requis / optionnels).
7. Secrets uniquement dans `.env`.
8. Risque : taille sur prix max et stop glissé, 0,75 % du capital pilote, plafonds cumulés (4 %), secteur, facteur, scénario de gap, coupe-circuit ; `BLOQUÉ : <règle>` affiché.
9. Une source unique par règle ; divergences signalées dans `docs/15-status.md`.
10. Un ordre saisi n'est pas une position : états d'exécution et de protection par quantité, rapprochement déclaré/confirmé.
11. Instantané de décision figé pour tout post-mortem.
12. Deux problèmes à résoudre : rareté des opportunités (univers PEA élargi) et signaux tardifs (détection propre 7h–9h30, entrées préparées J+1).
13. Mode de disponibilité (disponible / réunion / absent) = donnée d'entrée du moteur ; écran « 3 décisions du jour ».
14. Phases : 0 socle + qualification → 1 portefeuille/protections/risque → 2 MVP entrées préparées → 3 gaps d'ouverture → 4 KPI/fiscal → 5 post-mortem.
15. Une session Claude Code = un sous-lot, tests verts avant commit, `docs/15-status.md` tenu à jour.

## 2. Points ambigus ou contradictoires, et proposition par défaut

| # | Point | Proposition par défaut appliquée | À valider |
|---|---|---|---|
| H1 | `config_validation.optional_groups` liste `brokers`, mais docs/14 §14.2 et CLAUDE.md règle 8 mettent `brokers` dans les groupes requis (« nécessaire aux frais estimés »). | `params.yaml` prime (règle 8) : `brokers` est **optionnel** en phase 0 (aucun calcul de frais encore) ; il redeviendra requis en phase 1 (dimensionnement) par modification de `required_groups`. Divergence signalée dans `docs/15-status.md`. | Oui |
| H2 | Demi-séances Euronext : docs/16 §16.2 dit « clôture 14:00/14:05, à confirmer ». | `market_calendars.yaml` encode **14:05** (fin du trading at last) pour XPAR/XAMS/XBRU les 24 et 31 décembre 2026-2027 ; `eod_pipeline` bascule à 14:20 et `position_monitor` s'arrête à 14:10 (params). | Oui (appendice Euronext) |
| H3 | Jours fériés 2027 : « attendu, à confirmer à parution ». | Encodés (1er janv., 26 et 29 mars) avec source « attendu » ; l'outil refuse toute date d'une année non encodée (pas de devinette). | À revalider début 2027 |
| H4 | Le créneau relatif à l'ouverture (`ref: market_open`, `confirmed: false`) : docs/02 §2.3 dit qu'il est ignoré tant qu'il n'est pas confirmé, choix affiché sur `/sante`. | Implémenté tel quel : créneau ignoré, avertissement visible sur `/sante`. | Confirmer le créneau (11:00 été / 12:00 hiver) |
| H5 | Ingestion IMAP : docs/03 laisse le choix entre n8n et `imaplib` dans `app`. | **`imaplib` dans `app`** (testable sur fixtures, un composant de moins) ; n8n reste possible via `POST /api/ingest/*` avec `X-App-Token`. Voir ADR-001. | Oui |
| H6 | Base de développement : Postgres existant sur le PC, absent dans l'environnement de développement Claude. | SQLite pour les tests et le développement local (`DATABASE_URL` sqlite), Postgres 16 en exploitation ; modèles et migration écrits pour rester portables (bigint/timestamptz en Postgres). Le partitionnement mensuel de `prices_intraday` sera une migration Postgres-only (phase 3). | Non bloquant |
| H7 | APScheduler : la spec dit « jobstore Postgres ». APScheduler 4 est encore en pré-version. | **APScheduler 3.11** avec `SQLAlchemyJobStore` (table `apscheduler_jobs`) quand `DATABASE_URL` est Postgres, `MemoryJobStore` sinon ; `coalesce=True`, `max_instances=1`, `misfire_grace_time` par job. | Non bloquant |
| H8 | Le job de test de la phase 0 (A0.3, « planifié à 09:05 Paris ») n'existe pas dans `params.example.yaml`. | Ajout de `jobs.test_job` (`5 9 * * 1-5`, `market_days_only: true`) dans `params.example.yaml`. | Oui |
| H9 | Watchdog : healthchecks.io « checks attendus `brief_0845`, `eod_1750`… ». | Ping par **slug = nom du job** (`https://hc-ping.com/<ping-key>/<job>?create=1`), création automatique du check à la première exécution ; les noms de docs/14 sont des libellés à définir dans healthchecks.io. | Non bloquant |
| H10 | Cloudflare Access : docs/14 §14.1 demande la vérification du JWT sur chaque requête ; en développement local il n'y a pas de jeton. | Vérification **activée dès que** `CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD` sont définis ; sinon désactivée et affichée en rouge sur `/sante`. `X-App-Token` accepté pour n8n/webhooks. | Oui |
| H11 | Coexistence avec le cahier des charges v2.3 (juillet 2026, « Agent Investissement France ») : « heat » plafonné à 1 400 €, grades A/B (1 %), marchés US/UK/CH, DCA PEA, pyramiding chiffré. | **Le CDC v1.1.1 (septembre 2026) fait foi** ; il conserve la mécanique du projet actuel et reformule le risque (4 % de risque ouvert = 1 600 € pour 40 000 €, pas de grade A). Les éléments de la v2.3 non repris (DCA, réserve immobilière, marchés US) sont hors périmètre v1 (docs/00 §0.4, phase 6). Archivé dans `docs/archive/`. | Oui |
| H12 | `srd` n'est ni dans `required_groups` ni dans `optional_groups`. | Traité comme **optionnel** (calendrier SRD et coûts SRD désactivés s'il manque) ; ajouté à la liste des fonctionnalités surveillées. | Oui |

## 3. Décisions qui appartiennent à l'utilisateur (rappel du plan d'action, semaine 1)

- Nom de domaine + compte Cloudflare (Tunnel + Access) ; **test depuis le PC pro** (A0.2), plan B Tailscale (ADR-001 si nécessaire).
- Demande de clé **live** Saxo OpenAPI (usage personnel) — chemin critique.
- Abonnement EODHD EOD+Intraday (clé API) ; adresse Gmail dédiée ; export CSV BoursoBank anonymisé.
- 10 valeurs FR/DE/NL et relevés Saxo sur 3 séances (A0.7).
- Planning de disponibilité réel et confirmation du créneau relatif à l'ouverture.
- Bot Telegram (token + `user_id`), compte healthchecks.io (ping key).
- Paramètres « à confirmer » : types d'ordres BoursoBank/Saxo, `srd.coverage_rate_broker`, `srd.prorogation_deadline_local`, plafond PEA.

## 4. Plan de la phase 0 (10 étapes)

**Socle (Prompt 1)** — fait en session 1 : (1) dépôt + CLAUDE.md + docs éclatés ; (2) pyproject/uv, ruff, mypy, pytest ; (3) chargement de `params.yaml` avec validation par fonctionnalité + tests ; (4) fuseaux + calendriers par place + calendrier SRD + tests ; (5) mode de disponibilité (planning, overrides, `/mode`) + tests ; (6) tables système + migration Alembic ; (7) planificateur (gating par place, `jobs_runs`, watchdog, demi-séances) + job de test ; (8) FastAPI `/health`, `/sante`, `/api/mode`, middleware Cloudflare Access, bot Telegram, e-mail ; (9) Docker Compose, scripts Windows, RUNBOOK.
**Qualification (Prompt 2)** — session suivante : (10) interface `MarketDataProvider` + DTO avec statut de donnée, providers EODHD / tradingview-screener / yfinance / Saxo lecture, `scripts/qualify_providers.py`, fiches `docs/providers/*.md`, ADR-002.
