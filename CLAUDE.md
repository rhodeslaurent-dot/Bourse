# BOURSE-PILOT — instructions permanentes pour Claude Code

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
