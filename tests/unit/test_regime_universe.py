from app.domain.regime import Light, RegimeInputs, RegimeParams, compute_regime
from app.domain.universe import (
    InstrumentFacts,
    MomentumCandidate,
    SrdStatus,
    UniverseFilters,
    momentum_watchlist,
    pea_regulatory_eligibility,
    srd_status_from_rules,
    universe_decision,
)


def _rp(config) -> RegimeParams:
    r = config.params.regime
    return RegimeParams(
        r["red_min_components"],
        r["breadth_green"],
        r["breadth_orange"],
        r["distribution_days_orange"],
        r["distribution_days_red"],
        r["vol_pct_orange"],
        r["vol_pct_red"],
    )


def test_regime_lights(config):
    p = _rp(config)
    assert compute_regime(RegimeInputs(True, True, 0.6, 2, 30), p).light == Light.GREEN
    r = compute_regime(RegimeInputs(True, False, 0.6, 2, 30), p)
    assert r.light == Light.ORANGE and r.components["indices_vs_mm50"] == Light.ORANGE
    assert compute_regime(RegimeInputs(True, True, 0.6, 7, 30), p).light == Light.ORANGE  # 1 red → orange
    assert compute_regime(RegimeInputs(False, False, 0.3, 2, 30), p).light == Light.RED  # 2 reds
    m = compute_regime(RegimeInputs(None, None, None, None, None), p)
    assert m.light == Light.GREEN and len(m.missing) == 4  # missing components are reported, never guessed


def test_pea_eligibility_rules(config):
    countries = config.params.pea["eligible_countries"]
    assert pea_regulatory_eligibility("FR0000120271", "FR", countries).eligible is True
    trap = pea_regulatory_eligibility("JE00B4T3BW64", None, countries)
    assert trap.eligible is False and "piège" in trap.reason
    assert pea_regulatory_eligibility("GB0002634946", "GB", countries).eligible is False
    unknown = pea_regulatory_eligibility("XS1234567890", None, countries)
    assert unknown.eligible is None and unknown.confidence == 0.0
    assert pea_regulatory_eligibility("NL0010273215", None, countries).confidence == 0.6
    assert pea_regulatory_eligibility("NL0010273215", "NL", countries, broker_flag=True).confidence == 0.95
    assert pea_regulatory_eligibility("NL0010273215", "NL", countries, broker_flag=False).eligible is False


def test_srd_status_rules(config):
    s = config.params.raw_srd if hasattr(config.params, "raw_srd") else config.raw["srd"]
    full, lo = s["eligibility_full"], s["eligibility_long_only"]
    assert srd_status_from_rules("XPAR", 2e9, 5e6, full, lo) == SrdStatus.COMPLET
    assert srd_status_from_rules("XPAR", 5e8, 5e5, full, lo) == SrdStatus.LONG_ONLY
    assert srd_status_from_rules("XPAR", 5e8, 5e4, full, lo) == SrdStatus.NONE
    assert srd_status_from_rules("XETR", 5e10, 5e7, full, lo) == SrdStatus.NONE
    assert srd_status_from_rules("XPAR", None, 5e6, full, lo) == SrdStatus.UNKNOWN


def test_universe_filters(config):
    u = config.params.universe
    f = UniverseFilters(
        u["min_adv_eur_cto"],
        u["min_adv_eur_pea"],
        u["min_market_cap_eur"],
        u["small_cap_threshold_eur"],
        u["min_price"],
        u["min_history_sessions"],
        ["FR0000000000"],
    )
    ok = universe_decision(InstrumentFacts("FR0000120271", "XPAR", 55.0, 1.3e11, 2e8, 2600), f)
    assert ok.included and ok.cto_ok and not ok.small_cap
    small = universe_decision(InstrumentFacts("FR0000000001", "XPAR", 12.0, 4e8, 7e5, 300), f)
    assert small.included and not small.cto_ok and small.pea_liquidity_ok and small.small_cap
    bad = universe_decision(InstrumentFacts("FR0000000000", "XPAR", 0.5, 1e8, 1e5, 100, suspended=True), f)
    assert not bad.included and len(bad.reasons) == 6


def test_momentum_watchlist():
    cands = [
        MomentumCandidate("A", 95, 100, 90, 80),
        MomentumCandidate("B", 85, 100, 95, 98),  # MM50 < MM200
        MomentumCandidate("C", 70, 100, 90, 80),  # rank too low
        MomentumCandidate("D", 88, 100, 90, 80),
        MomentumCandidate("E", 99, 100, None, 80),
    ]
    wl = momentum_watchlist(cands, 80, 1)
    assert [c.isin for c in wl] == ["A"]
    assert [c.isin for c in momentum_watchlist(cands, 80, 100)] == ["A", "D"]
