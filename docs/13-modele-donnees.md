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
