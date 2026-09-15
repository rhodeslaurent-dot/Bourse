# 15-status — Avancement des critères d'acceptation (v1.1.1)

Tenu à jour par Claude Code en fin de chaque session (statut ∈ {à faire, en cours, fait, bloqué}).

| Critère | Statut | Date | Commit | Note |
|---|---|---|---|---|
| A0.1 | en cours | 2026-09-13 | | Socle livré (compose, restart unless-stopped, misfire_grace, startup.ps1) ; **test réel** (redémarrage PC, coupure box 10 min, arrêt Docker) à faire par l'utilisateur — RUNBOOK §5 |
| A0.2 | à faire | | | Domaine + Cloudflare Tunnel/Access à créer (RUNBOOK §2) ; middleware JWT prêt ; tester d'abord le proxy du PC pro |
| A0.3 | fait (tests) | 2026-09-13 | | `test_job` 09:05 Paris → 11:05 été / 12:05 hiver Réunion (test cron) ; job Xetra sauté le 24/12, job Paris tourne en demi-séance (tests + `run-job`) ; à observer en réel |
| A0.4 | en cours | 2026-09-13 | | Bot codé (boutons, clic en base, autre user_id ignoré et journalisé, `/mode reunion 2h` → `/sante`) et testé hors réseau ; à vérifier avec le vrai bot (token + user_id dans `.env`) |
| A0.5 | en cours | 2026-09-13 | | Ping healthchecks.io par job, job `backup` (pg_dump -Fc + rotation), `restore_test.ps1` ; compte healthchecks et test de restauration à faire |
| A0.6 | fait | 2026-09-13 | | `tests/test_no_trading_endpoints.py` ; refus de démarrage si groupe requis manquant ; désactivation par fonctionnalité si groupe optionnel manquant ; pytest (60), ruff, mypy verts |
| A0.7 | en cours | 2026-09-13 | | **Pré-qualification documentaire faite** (`docs/providers/*.md`), interface `MarketDataProvider` + DTO (source, horodatage de marché, périmètre, statut), providers EODHD (EOD, bulk, intraday, REST différé, WebSocket → barres 1 min avec `complete_bar`), tradingview-screener (≤ 6 req., ≥ 60 s), yfinance (secours EOD), Saxo lecture seule (garde `ReadOnlyViolation`), `scripts/qualify_providers.py` + analyse testée, ADR-002 **proposé**. **Reste** : clés EODHD/Saxo, 10 valeurs (`tests/fixtures/qualification/symbols.yaml`), 3 séances de mesures + relevés Saxo, fiches complétées, ADR-002 accepté |
| A1.1 | en cours | 2026-09-15 | | Sync Saxo lecture (positions, ordres dont stops, cash) → rapprochement, `portfolio_state` daté ; jobs `portfolio_sync` 19:00 et `portfolio_sync_intraday` 5 min ; import CSV BoursoBank (fixture anonymisée) ; **5 jours de contrôle réel** à faire avec la clé live |
| A1.2 | fait (tests) | 2026-09-15 | | Double clic = 1 déclaration ; déclaré + import = 1 ligne avec `declared_uid` + `broker_fill_id` (± 0,5 %, ± 10 min) ; « Ordre saisi » ≠ position ; P1 non protégée dans tous les modes, rappel 15 min ; partiel → `filled_partial` + `partially_protected` |
| A1.3 | fait (tests) | 2026-09-15 | | Risque courant/réservé, secteur, facteur, scénario de gap, exposition, levier et couverture SRD sur portefeuille synthétique (SRD + comptant + PEA, SEK) ; équité docs/10 §10.3 ; affichés sur `/` (page « Trois décisions ») |
| A1.4 | fait (tests) | 2026-09-15 | | `test_sizing_examples` : 114 actions, R 2,381 €, frais ≈ 27 €, 214 € si exécuté à 42,40 ; risque courant borné à zéro ; `BLOQUÉ : <règle>` avec la clé params ; état périmé (> 10 min) signalé sur `/` et `/positions` (le marquage « à vérifier » de la proposition arrive avec les propositions, phase 2) |
| A1.5 | à faire | | | Sous-lot C |
| A1.6 | à faire | | | Sous-lot D |
| A1.7 | fait (tests) | 2026-09-15 | | Seuil franchi → P1 immédiate ; 5 min sans exécution confirmée → SELL au marché dans le même incident ; « stop exécuté » distinct ; hors séance pas de SELL |
| A1.8 | à faire | | | Sous-lot D |
| A2.1 – A2.7 | à faire | | | MVP : entrées préparées, 3 décisions, mode de disponibilité |
| A3.1 – A3.4 | à faire | | | Détection d'ouverture, après ADR-002 |
| A4.1 – A4.3 | à faire | | | KPI P&L et fiscal |
| A5.1 – A5.3 | à faire | | | Post-mortem, trois démarches |

## Divergences signalées (règle 8 — jamais résolues en silence)

- **`brokers` requis ou optionnel ?** CLAUDE.md règle 8 et docs/14 §14.2 le listent parmi les groupes requis (« nécessaire aux frais estimés ») ; `config/params.example.yaml › config_validation` le met dans `optional_groups`. Appliqué : `params.yaml` prime → optionnel en phase 0 ; à basculer en requis en phase 1 (ADR-000 H1).
- **`srd`** n'apparaît dans aucune des deux listes : traité comme optionnel (ADR-000 H12).
- **Demi-séances Euronext** : clôture encodée 14:05 (docs/16 dit 14:00/14:05 « à confirmer ») — ADR-000 H2.
- **Risque courant d'une position gagnante** : la prose de docs/07 §7.3 (« contribue au plus ses frais de sortie ») et la formule `max(0, qty × (cours − stop glissé)) + frais` divergent quand le stop est remonté au-dessus du prix d'entrée mais sous le cours ; **la formule est appliquée** (elle donne un risque positif jusqu'au stop), la borne à zéro ne joue que si le stop glissé ≥ cours. Test `test_position_risk_definitions`.
- **CDC v2.3 (juillet 2026)** vs **CDC v1.1.1 (septembre 2026)** : v1.1.1 fait foi ; écarts listés dans ADR-000 H11.

## Décisions en attente de l'utilisateur

- ADR-000 et ADR-001 : **acceptés** le 15/09/2026 (« ok allons y ») ; ADR-003 (sous-lot 1A) à relire.
- Renseigner `.env` (Telegram token + user_id, SMTP, ping key healthchecks, `EODHD_API_TOKEN`, `SAXO_*`) et `config/params.yaml` ; confirmer le créneau `ref: market_open`.
- Remplacer les 10 valeurs d'exemple de `tests/fixtures/qualification/symbols.yaml` par votre liste (avec les Uic Saxo), puis lancer `scripts/qualify_providers.py` sur 3 séances et saisir les relevés Saxo (`reference_saxo_<date>.csv`).
- Valider ADR-002 après les mesures (source temps réel, périmètre RVOL, rattrapage des ticks).

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
| 2026-09-15 | 1 (B) moteur de risque | `domain/risk` (dimensionnement prix max + stop glissé, frais itérés, marge de change, plafonds, risque initial/courant/réservé, gap, coupe-circuit, drawdown TWR), `domain/accounts` (coût SRD, TTF), `services/risk_view`, page `/` ; 136 tests | — | Divergence prose/formule §7.3 signalée |
| 2026-09-15 | 1 (A) comptes et états | Domaine ordres/exécutions/protections par quantité, rapprochement déclaré/confirmé, S1, équité SRD ; tables + migration 0002 ; service portefeuille ; sync Saxo lecture ; import CSV BoursoBank ; `position_monitor` avec P1 par incident ; API `/api/orders|executions|protections|positions` ; pages `/positions`, `/import` ; commandes Telegram ; 117 tests | Contrôle réel 5 jours (A1.1) | ADR-003 |
| 2026-09-13 | 0 (b) qualification | DTO + interface provider ; EODHD REST/WS + agrégateur de barres ; tradingview-screener ; yfinance ; Saxo lecture seule + OAuth (`saxo-auth`, jeton chiffré) ; script et analyse de qualification (rapport Markdown) ; fiches de pré-qualification ; ADR-002 proposé ; 83 tests | Mesures réelles sur 3 séances (clés API et compte Saxo live requis) | ADR-002 (après mesures) |
| 2026-09-13 | 0 (a) socle | Dépôt + docs éclatés ; config validée par fonctionnalité ; fuseaux ; calendriers par place + SRD ; mode de disponibilité ; tables système + migration ; planificateur (gating par place, demi-séances, jobs_runs, watchdog) ; `/health`, `/sante`, `/api/mode` ; Cloudflare Access ; Telegram + e-mail ; compose + scripts + RUNBOOK ; 60 tests verts | Tests réels A0.1/A0.2/A0.4/A0.5 (nécessitent domaine, Cloudflare, bot, healthchecks) ; qualification A0.7 | ADR-000 H1–H12 ; ADR-001 |
