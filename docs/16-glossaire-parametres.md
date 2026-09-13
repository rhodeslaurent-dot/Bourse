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
