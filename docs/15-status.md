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
| A0.7 | à faire | | | Session suivante (Prompt 2) : pré-qualification documentaire puis prototype sur 10 valeurs FR/DE/NL, fiches `docs/providers/*.md`, ADR-002 |
| A1.1 – A1.8 | à faire | | | Portefeuille, protections, risque |
| A2.1 – A2.7 | à faire | | | MVP : entrées préparées, 3 décisions, mode de disponibilité |
| A3.1 – A3.4 | à faire | | | Détection d'ouverture, après ADR-002 |
| A4.1 – A4.3 | à faire | | | KPI P&L et fiscal |
| A5.1 – A5.3 | à faire | | | Post-mortem, trois démarches |

## Divergences signalées (règle 8 — jamais résolues en silence)

- **`brokers` requis ou optionnel ?** CLAUDE.md règle 8 et docs/14 §14.2 le listent parmi les groupes requis (« nécessaire aux frais estimés ») ; `config/params.example.yaml › config_validation` le met dans `optional_groups`. Appliqué : `params.yaml` prime → optionnel en phase 0 ; à basculer en requis en phase 1 (ADR-000 H1).
- **`srd`** n'apparaît dans aucune des deux listes : traité comme optionnel (ADR-000 H12).
- **Demi-séances Euronext** : clôture encodée 14:05 (docs/16 dit 14:00/14:05 « à confirmer ») — ADR-000 H2.
- **CDC v2.3 (juillet 2026)** vs **CDC v1.1.1 (septembre 2026)** : v1.1.1 fait foi ; écarts listés dans ADR-000 H11.

## Décisions en attente de l'utilisateur

- **Valider ADR-000** (hypothèses H1–H12) et ADR-001 (stack) avant la session « qualification des données ».
- Renseigner `.env` (Telegram token + user_id, SMTP, ping key healthchecks) et `config/params.yaml` ; confirmer le créneau `ref: market_open`.

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
| 2026-09-13 | 0 (a) socle | Dépôt + docs éclatés ; config validée par fonctionnalité ; fuseaux ; calendriers par place + SRD ; mode de disponibilité ; tables système + migration ; planificateur (gating par place, demi-séances, jobs_runs, watchdog) ; `/health`, `/sante`, `/api/mode` ; Cloudflare Access ; Telegram + e-mail ; compose + scripts + RUNBOOK ; 60 tests verts | Tests réels A0.1/A0.2/A0.4/A0.5 (nécessitent domaine, Cloudflare, bot, healthchecks) ; qualification A0.7 | ADR-000 H1–H12 ; ADR-001 |
