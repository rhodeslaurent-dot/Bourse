"""Management rules of 22/10/2026 (docs/07 §7.9, docs/22 R1–R21), replayed on the cases of the
user's framework. Thresholds come from config/params.example.yaml; the fee fractions 1.03 % /
1.43 % are the framework's own round-trip estimates (inputs, not parameters)."""

import pytest

from app.domain.admission import AdmissionInput, Setup, Status, admit, gross_reward_risk, p_max_rr
from app.domain.conjuncture import (
    BrentParams,
    ConjunctureParams,
    Threshold,
    conjuncture,
    market_gate,
    srd_mode,
)
from app.domain.costs import CostParams, envelope_tax_rate, net_outcome, round_trip_fee_fraction
from app.domain.events import EventParams, event_exposure
from app.domain.heat import (
    HeatLine,
    HeatParams,
    OverrideParams,
    check_new_line,
    check_notional,
    heat_b,
    heat_state,
    override_eligible,
    override_fits,
)
from app.domain.indicators import Bar
from app.domain.ranking import RankItem, rank
from app.domain.risk import FeeSchedule
from app.domain.stops import Zone, next_stop, place_stop, placement_violation, refuse_lowering
from app.domain.zones import detect_zones, exploitable_targets, unfilled_gaps

FR_FEES = 0.0143  # TTF 0,40 % + courtage aller-retour (référentiel §5.2)
NON_TTF_FEES = 0.0103


@pytest.fixture
def k(config):
    return config.params.admission["min_reward_risk"]


# --- R4 / R5 : prix maximal et structure ---------------------------------------------------------


def test_p_max_arcelormittal_ttf_correction(k):
    """16/10 : siège au Luxembourg → hors TTF, P_max 52,39 → 52,60."""
    assert p_max_rr(61.12, 49.16, FR_FEES, k) == pytest.approx(52.39, abs=0.01)
    assert p_max_rr(61.12, 49.16, NON_TTF_FEES, k) == pytest.approx(52.60, abs=0.01)


def test_id_logistics_refused_by_26_cents_per_100(k):
    a = admit(AdmissionInput(331.00, 368.0, 318.40, Setup.PULLBACK, 318.40, FR_FEES, k))
    assert a.p_max == pytest.approx(330.14, abs=0.01)
    assert a.status == Status.REFUSED and "dépassé de 0.26%" in a.reasons[0]


def test_loreal_refused_bad_ratio(k):
    rr = gross_reward_risk(383.50, 394.0, 362.91)
    assert rr == pytest.approx(0.51, abs=0.01)
    a = admit(AdmissionInput(383.50, 394.0, 362.91, Setup.BREAKOUT, None, FR_FEES, k))
    assert a.status == Status.REFUSED and a.p_max == pytest.approx(367.95, abs=0.05)


def test_jcdecaux_and_orange_pass_for_the_wrong_reason(k):
    """19/10 : prix sous P_max parce que le support a cassé / le franchissement est rendu."""
    jcd = admit(AdmissionInput(25.04, 29.76, 23.66, Setup.PULLBACK, 25.30, FR_FEES, k))
    assert jcd.p_max is not None and 25.04 < jcd.p_max
    assert jcd.status == Status.DEGRADED and "support perdu" in jcd.reasons[0]
    ora = admit(AdmissionInput(14.255, 16.74, 13.42, Setup.BREAKOUT, 14.35, FR_FEES, k))
    assert ora.status == Status.DEGRADED and "franchissement rendu" in ora.reasons[0]
    held = admit(AdmissionInput(25.31, 29.76, 23.66, Setup.PULLBACK, 25.30, FR_FEES, k))
    assert held.status == Status.ADMITTED


def test_technical_cap_and_testability(k):
    a = admit(AdmissionInput(50.0, 61.12, 49.16, Setup.BREAKOUT, None, NON_TTF_FEES, k, p_max_technical=51.0))
    assert a.p_max == 51.0 and a.ok
    assert admit(AdmissionInput(50.0, None, 49.0, Setup.BREAKOUT, None, 0.01, k)).status == Status.NOT_TESTABLE
    assert admit(AdmissionInput(50.0, 60.0, None, Setup.BREAKOUT, None, 0.01, k)).status == Status.NOT_TESTABLE


# --- R1–R3 : stops ---------------------------------------------------------------------------------


def test_stop_placement_under_the_zone(config):
    buf = config.params.risk.stop.wick_buffer_pct
    support = Zone(49.00, 49.60)  # Eurazeo : vrai support structurel à 49,00
    assert place_stop(support, buf) == pytest.approx(48.755)
    assert placement_violation(49.60, [support]) == support  # à la borne haute : erreur « Airbus »
    assert placement_violation(48.755, [support]) is None


def test_vallourec_ratchet_and_invariant(config):
    buf = config.params.risk.stop.wick_buffer_pct
    supports = [Zone(17.33, 17.71)]
    no = next_stop(17.60, 19.09, [19.32], supports, buf)
    assert no.stop == 17.60 and not no.changed
    assert any("conservé (l'invariant prime)" in n for n in no.notes)
    yes = next_stop(17.60, 19.40, [19.32], supports, buf)
    assert round(yes.stop, 2) == 19.22 and yes.changed


def test_placement_prevails_over_ratchet(config):
    buf = config.params.risk.stop.wick_buffer_pct
    d = next_stop(91.50, 94.00, [93.00], [Zone(92.12, 92.70)], buf)
    assert d.stop == 91.50 and "placement prime" in d.reason


def test_stop_never_lowered_except_dividend(config):
    buf = config.params.risk.stop.wick_buffer_pct
    assert refuse_lowering(247.20, 240.0) is not None and refuse_lowering(247.20, 250.0) is None
    d = next_stop(20.0, 20.5, [], [], buf, dividend=0.5)
    assert d.stop == 19.5 and d.changed and "seule exception" in d.notes[0]


# --- R8 / R9 / R21 : plafonds ---------------------------------------------------------------------


@pytest.fixture
def heat_params(config):
    r = config.params.risk
    return HeatParams(
        40000,
        r.max_open_risk_pct_by_gate,
        r.max_line_risk_pct_of_cap,
        r.reserved_signal_budget_pct,
        r.reserved_budget_age_sessions,
        r.srd_risk_weight,
        r.heat_a_soft_cap_pct,
    )


def test_caps_follow_the_gate(heat_params):
    lines = [HeatLine("A", 84, 37.37, 38.0, 35.0, age_sessions=12), HeatLine("B", 100, 98.0, 99.0, 91.50, srd=True)]
    st = heat_state(lines, 0.0, "restreint", heat_params)
    assert st.cap_eur == pytest.approx(1400) and st.heat_b_eur == pytest.approx(84 * 2.37 + 100 * 6.5 * 1.25)
    blocked = check_new_line(st, 450, heat_params)
    assert blocked and "dépassement" in blocked[0]
    assert heat_state(lines, 0.0, "ouvert", heat_params).cap_eur == pytest.approx(2000)


def test_line_cap_srd_weight_and_floor(heat_params):
    st = heat_state([], 0.0, "ouvert", heat_params)
    assert check_new_line(st, 550, heat_params) == []
    assert "risk.max_line_risk_pct_of_cap" in check_new_line(st, 550, heat_params, srd=True)[0]  # 687,5 > 600
    assert heat_b(HeatLine("V", 10, 15.62, 18.815, 17.60), 1.25) == 0.0  # stop au-dessus du PRU
    assert heat_b(HeatLine("T", 335, 12.0, 6.0, None, excluded="tax_harvest"), 1.25) == 0.0


def test_reserved_budget_protects_fresh_signals(heat_params):
    old = [HeatLine("R", 200, 40.0, 41.0, 31.0, age_sessions=15)]  # 1 800 € > 2 000 − 300
    st = heat_state(old, 0.0, "ouvert", heat_params)
    assert any("budget réservé" in a for a in st.alerts)
    assert check_new_line(st, 150, heat_params) == []
    assert "budget réservé" in check_new_line(st, 150, heat_params, adds_to_old_line=True)[0]


def test_notional_cap_loreal_case(config):
    """16/10 : 33 titres pour 12 270 € = 23,9 % du capital, heat conforme."""
    max_pct = config.params.risk.max_position_pct_of_capital
    assert max_pct == 0.20
    assert check_notional(12270, 51400, max_pct) and not check_notional(9000, 51400, max_pct)


def test_override_only_for_exceptional_and_bounded(config):
    o = config.params.risk.override
    p = OverrideParams(o["min_score"], o["min_confidence"], o["min_net_rr"], o["max_multiplier"])
    assert override_eligible(1, 88, 4, 3.2, p) == (True, [])
    ok, missing = override_eligible(2, 80, 3, 2.5, p)
    assert not ok and len(missing) == 4
    assert override_fits(900, 2000, p) and not override_fits(1100, 2000, p)


# --- R10 / R12 : conjoncture et portail ------------------------------------------------------------


@pytest.fixture
def conj_params(config):
    c = config.params.conjuncture
    th = {
        "vix_pct_1y": Threshold(**c["vix_pct_1y"]),
        "oat_bund_spread_bp": Threshold(**c["oat_bund_spread_bp"]),
        "us10y_pct": Threshold(**c["us10y_pct"]),
        "index_5d_change_pct": Threshold(**c["index_5d_change_pct"], higher_is_worse=False),
    }
    return ConjunctureParams(th, c["high_min_components"])


def test_conjuncture_october_19(conj_params):
    values = {"vix_pct_1y": 0.90, "oat_bund_spread_bp": 140, "us10y_pct": 5.07, "index_5d_change_pct": -0.012}
    c = conjuncture("orange", values, conj_params)
    assert c.level == "eleve" and c.components["index_5d_change_pct"] == "faible"
    assert market_gate(False, c.level, "orange").state == "restreint_plus"
    calm = conjuncture("green", {"vix_pct_1y": 0.70}, conj_params)
    assert calm.level == "modere" and calm.components["us10y_pct"] == "inconnu"
    assert market_gate(True, calm.level, "green").state == "ouvert"
    assert market_gate(None, calm.level, "green").state == "restreint"
    assert market_gate(True, "faible", "red").state == "restreint_plus"  # jamais de gel absolu


def test_brent_srd_hysteresis(config):
    b = config.params.conjuncture["brent"]
    p = BrentParams(b["srd_restrict_above"], b["srd_normal_below"], b["stable_sessions"])
    assert srd_mode([107.82], "normal", p) == "restreint"
    assert srd_mode([96.0, 94.0, 94.0], "restreint", p) == "restreint"
    assert srd_mode([94.0, 93.0, 94.5], "restreint", p) == "normal"
    assert srd_mode([], "restreint", p) == "restreint"


# --- R14 : annonces --------------------------------------------------------------------------------


def test_event_exposure(config):
    e = config.params.events
    p = EventParams(e["history_count"], e["min_history"], e["gap_quantile"], e["max_gap_loss_r"])
    calm = event_exposure([0.01, 0.02, 0.015, 0.03, 0.012, 0.018, 0.025, 0.02], 100, 95, p)
    assert calm.known and calm.low and calm.loss_r < 1.5
    wild = event_exposure([0.08, 0.06, 0.09, 0.07], 100, 95, p)
    assert wild.known and not wild.low and "forte" in wild.text
    assert not event_exposure([0.01, 0.02], 100, 95, p).known


# --- R11 : coût complet ----------------------------------------------------------------------------


def test_net_outcome_cto_vs_pea(config):
    t = config.params.tax[0]
    saxo = config.params.brokers["saxo"]
    fees = FeeSchedule("saxo", saxo["commission_pct"], saxo["commission_min_eur"])
    cto = CostParams(fees, ttf_rate=t.ttf_rate, tax_rate=envelope_tax_rate("cto", t.pfu_total_rate, 0.186, None))
    r = net_outcome(100, 50.0, 56.0, 47.0, cto)
    pre_tax = 600 - (4.0 + 20.0) - 4.48
    assert r.gain_net_eur == pytest.approx(pre_tax * (1 - 0.314))
    assert r.loss_net_eur == pytest.approx(300 + 24.0 + 3.76)  # aucun crédit d'impôt supposé
    pea = CostParams(fees, ttf_rate=t.ttf_rate, tax_rate=envelope_tax_rate("pea", 0.314, 0.186, 6))
    assert net_outcome(100, 50.0, 56.0, 47.0, pea).net_reward_risk > r.net_reward_risk
    srd = CostParams(fees, srd_daily_rate=config.params.srd.crd_daily_rate, holding_days=20, tax_rate=0.314)
    assert net_outcome(100, 50.0, 56.0, 47.0, srd).carry_eur > 0
    assert round_trip_fee_fraction(5000, cto) == pytest.approx((4.0 + 20.0 + 4.0) / 5000)


# --- zones et classement --------------------------------------------------------------------------


def _bars(lows_highs):
    return [Bar(lo, hi, lo, (lo + hi) / 2, 1000) for lo, hi in lows_highs]


def test_zones_and_exploitable_targets(config):
    z = config.params.zones
    seq = [(11, 12), (10.6, 11.6), (10.0, 11.0), (10.8, 11.8), (11.5, 12.5), (12.2, 13.2), (13.0, 14.0)]
    seq += [(12.4, 13.4), (11.6, 12.6), (10.05, 11.05), (10.9, 11.9), (11.7, 12.7), (12.5, 13.5), (13.02, 14.02)]
    seq += [(12.3, 13.3), (11.5, 12.5), (10.8, 11.8)]
    zones = detect_zones(_bars(seq), atr=1.0, pivot_window=2, merge_atr_mult=z["merge_atr_mult"], min_touches=2)
    lows = [round(x.low, 2) for x in zones]
    assert lows == [10.0, 14.0]  # double creux (10,00–10,05) et double sommet (14,00–14,02)
    assert exploitable_targets(11.5, zones, [15.0]) == [14.0, 15.0]
    gaps = unfilled_gaps(_bars([(10, 11), (11.5, 12), (11.6, 12.4)]))
    assert gaps and gaps[0].low == 11 and gaps[0].high == 11.5


def test_ranking_orders_by_expectation_then_ratio():
    items = [
        RankItem("a", 2.2, 0.06),
        RankItem("b", 3.0, 0.05),
        RankItem("c", 2.5, 0.08, hit_rate=0.5),  # E = 0,75 R
        RankItem("d", None, None),
    ]
    order = [it.key for _, it, _ in rank(items)]
    assert order == ["c", "b", "a", "d"]
