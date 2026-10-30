# ruff: noqa: E501
"""docs/36 — causes of the rises (pure): the falls of the same zigzag, time codes and timing classes, each cause
flag on constructed bars, the primary-cause priority, the share still capturable once known, the attribution and
event-study aggregates with their masks."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from app.domain import move_causes as mc
from app.domain.indicators import Bar, rvol_day
from app.domain.moves import MoveParams, find_episodes
from app.domain.research.entry_policies import letter_known_day

P = mc.CauseParams()
PARIS = ZoneInfo("Europe/Paris")


def _bars(closes: list[float], spread: float = 0.005, volume: int = 1000) -> list[Bar]:
    return [Bar(c, c * (1 + spread), c * (1 - spread), c, volume) for c in closes]


def _sessions(n: int, first: date = date(2024, 1, 1)) -> list[date]:
    out, d = [], first
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


# --- moves ----------------------------------------------------------------------------------------------------------


def test_falls_are_the_symmetric_swings_of_the_same_zigzag():
    base = [101.2 - 0.03 * i + (0.3 if i % 2 else -0.3) for i in range(40)]
    rise = [100 * 1.012**k for k in range(1, 11)]  # +12.7 % in 10 sessions
    fall = [rise[-1] * 0.988**k for k in range(1, 11)]  # −11.4 % in 10 sessions
    after = [fall[-1] * 1.01**k for k in range(1, 8)]
    bars = _bars(base + rise + fall + after)
    mp = MoveParams()
    rises, falls = mc.find_rises(bars, mp), mc.find_falls(bars, mp)
    big_r = [m for m in rises if m.magnitude >= 0.1]
    big_f = [m for m in falls if m.magnitude >= 0.1]
    assert len(big_r) == 1 and len(big_f) == 1
    r, f = big_r[0], big_f[0]
    assert f.start == r.end == 49 and f.end == 59 and f.direction == mc.FALLS and f.sign == -1  # same pivots
    assert 0.11 < f.magnitude < 0.12 and f.shape == "progressive" and f.t0 == 50 and f.sessions == 10
    assert [(m.start, m.end) for m in rises] == [(e.start, e.peak) for e in find_episodes(bars, mp)]
    # a volatile value (σ ≈ 3 %): a 7.5 % drop in two sessions stays inside the noise (2 σ √2 ≈ 8.5 %)
    noisy = (
        [100 * (1.03 if i % 2 else 1.0) + 0.02 * i for i in range(40)] + [97.5, 96.0] + [96.0 * 1.06**k for k in (1, 2)]
    )
    assert [m for m in mc.find_falls(_bars(noisy), MoveParams(sigma_k=0)) if m.end == 41]
    assert not [m for m in mc.find_falls(_bars(noisy), mp) if m.end == 41]


# --- time codes and timing --------------------------------------------------------------------------------------------


def test_publication_times_are_placed_on_the_value_sessions():
    days = _sessions(10, date(2024, 1, 1))  # Mon 1 … Fri 5, Mon 8 …
    o, c = time(9, 0), time(17, 30)

    def at(d: date, hh: int, mm: int = 0) -> int | None:
        return mc.place_time(datetime.combine(d, time(hh, mm), PARIS), o, c, days)

    assert at(days[2], 7) == mc.night(2)  # before the open: the night before the session
    assert at(days[2], 11) == mc.during(2)  # during the session
    assert at(days[2], 18) == mc.night(3)  # after the close: the night before the next session
    assert at(date(2024, 1, 6), 12) == mc.night(5)  # Saturday → Monday's open
    assert at(date(2023, 11, 1), 12) is None  # more than 7 days before the first session: not placed
    assert mc.entry_session(mc.night(3)) == 3 and mc.entry_session(mc.during(3)) == 4
    # « known at the close of t0 − 1 » is the letters' known-day rule (after 17:30 Paris: the next day)
    t0 = 4
    for hh in (7, 11, 17, 18, 20):
        for d in days[1:6]:
            code = at(d, hh)
            local = datetime.combine(d, time(hh), PARIS)
            known_by_close = letter_known_day(local.astimezone(UTC)) <= days[t0 - 1]
            assert (mc.timing(code, t0, P) == mc.KNOWN_BEFORE) == known_by_close, (d, hh)


def test_earnings_unknown_time_is_read_the_prudent_way():
    days = _sessions(10, date(2024, 1, 1))
    assert mc.earnings_code(days[3], "bmo", days) == mc.night(3)
    assert mc.earnings_code(days[3], "amc", days) == mc.night(4)
    assert mc.earnings_code(days[3], "unknown", days) == mc.night(4)  # never assumed known before its close
    assert mc.earnings_code(days[3], "intraday", days) == mc.during(3)
    assert mc.earnings_code(date(2024, 1, 6), "bmo", days) == mc.night(5)  # a Saturday: Monday
    assert mc.earnings_code(days[9], "amc", days) is None  # history ends


def test_timing_classes_and_the_window():
    t0 = 10
    assert mc.timing(mc.during(t0 - 1), t0, P) == mc.KNOWN_BEFORE
    assert mc.timing(mc.night(t0 - 5), t0, P) == mc.KNOWN_BEFORE  # first time inside the window
    assert mc.timing(mc.during(t0 - 6), t0, P) is None
    assert mc.timing(mc.night(t0), t0, P) == mc.BEFORE_OPEN
    assert mc.timing(mc.during(t0), t0, P) == mc.DURING
    assert mc.timing(mc.during(t0 + 1), t0, P) == mc.DURING
    assert mc.timing(mc.night(t0 + 2), t0, P) is None
    codes = [mc.during(t0 - 8), mc.night(t0), mc.during(t0 + 1)]
    f = mc.flag_from_codes(codes, t0, P)
    assert f.present and f.timing == mc.BEFORE_OPEN and f.entry == t0  # the earliest one inside the window
    assert mc.flag_from_codes([mc.during(t0 - 8)], t0, P) == mc.ABSENT
    assert mc.flag_from_codes(codes, t0, P, covered=False).present is None  # not covered: unknown, never absent


# --- cause flags on constructed bars --------------------------------------------------------------------------------


def test_opening_gap_needs_max_of_3_pct_and_1_5_atr_in_either_direction():
    assert mc.gap_threshold(100.0, 1.0, P) == 0.03  # 1.5 ATR = 1.5 % < 3 %
    assert abs(mc.gap_threshold(100.0, 4.0, P) - 0.06) < 1e-12  # a volatile value: 1.5 ATR = 6 %
    thr = mc.gap_threshold(100.0, 4.0, P)
    assert mc.gap_flag(mc.opening_gap(105.0, 100.0), thr, 7)[0] == mc.ABSENT
    f, d = mc.gap_flag(mc.opening_gap(107.0, 100.0), thr, 7)
    assert f.present and f.timing == mc.DURING and f.entry == 8 and d == 1  # bought at best the next open
    f, d = mc.gap_flag(mc.opening_gap(93.0, 100.0), thr, 7)
    assert f.present and d == -1
    assert mc.gap_flag(None, thr, 7)[0].present is None


def test_abnormal_volume_is_read_before_the_move_only():
    vols = [1000.0] * 30
    rv = mc.rvol_series(vols)
    bars = _bars([10.0] * 30)
    assert rv[25] == rvol_day(bars[:26]) == 1.0 and rv[19] is None  # production definition
    spike = vols[:]
    spike[22] = 3000.0
    rv = mc.rvol_series(spike)
    assert mc.rvol_flag(rv, 25, P) == mc.Flag(True, mc.KNOWN_BEFORE, 25)  # t0 − 3
    assert mc.rvol_flag(rv, 26, P) == mc.ABSENT  # t0 − 4: outside
    assert mc.rvol_flag(rv, 22, P) == mc.ABSENT  # the spike on t0 itself is not « before »
    assert mc.rvol_flag([None] * 30, 25, P).present is None


def test_market_and_sector_explain_half_of_the_move_in_its_direction():
    assert mc.explained_flag(0.08, 0.05, 1, 12, P) == mc.Flag(True, mc.DURING, 13)
    assert mc.explained_flag(0.08, 0.03, 1, 12, P) == mc.ABSENT
    assert mc.explained_flag(-0.08, -0.05, -1, 12, P).present  # a fall with the market
    assert mc.explained_flag(-0.01, -0.05, 1, 12, P) == mc.ABSENT  # wrong direction for a rise
    assert mc.explained_flag(0.08, None, 1, 12, P).present is None


def test_equal_weighted_benchmark_liquid_half_and_minimum():
    p0 = np.array([10.0, 10.0, 10.0, 10.0, 10.0, 10.0])
    p1 = np.array([11.0, 11.0, 11.0, 9.0, 9.0, 9.0])
    ok = np.ones(6, dtype=bool)
    assert abs(mc.ew_return(p0, p1, ok) - 0.0) < 1e-12
    liq = np.array([5.0, 5.0, 5.0, 1.0, 1.0, 1.0])
    assert abs(mc.ew_return(p0, p1, ok, liq) - 0.1) < 1e-12  # the liquid half rose
    ok[0] = False  # the value itself, excluded
    assert mc.ew_return(p0, p1, ok, liq) is not None
    ok[1] = False
    assert mc.ew_return(p0, p1, ok, liq) is None  # fewer than 5 values


def test_dividend_detachment_dated_by_its_ex_session():
    t0 = 20
    assert mc.flag_from_sessions([18], t0, P) == mc.Flag(True, mc.KNOWN_BEFORE, 18)
    assert mc.flag_from_sessions([20], t0, P).timing == mc.BEFORE_OPEN
    assert mc.flag_from_sessions([21], t0, P).timing == mc.DURING
    assert mc.flag_from_sessions([10, 30], t0, P) == mc.ABSENT
    assert mc.flag_from_sessions([], t0, P, known=False).present is None


def test_primary_cause_follows_the_fixed_priority():
    flags = {c: mc.ABSENT for c in mc.CAUSES}
    assert mc.primary_cause(flags) == mc.NO_CAUSE and mc.without_information(flags)
    flags[mc.SETUP] = mc.Flag(True, mc.KNOWN_BEFORE, 5)
    flags[mc.GAP] = mc.Flag(True, mc.DURING, 6)
    assert mc.primary_cause(flags) == mc.GAP  # a gap comes before a setup
    flags[mc.MARKET] = mc.Flag(True, mc.DURING, 7)
    assert mc.primary_cause(flags) == mc.MARKET and not mc.without_information(flags)
    flags[mc.LETTER] = mc.Flag(True, mc.KNOWN_BEFORE, 4)
    flags[mc.EARNINGS] = mc.Flag(True, mc.BEFORE_OPEN, 5)
    assert mc.primary_cause(flags) == mc.EARNINGS  # information first
    flags[mc.EARNINGS] = mc.UNKNOWN  # an uncovered source never counts
    assert mc.primary_cause(flags) == mc.LETTER


def test_capturable_share_starts_at_the_open_after_the_cause_is_known():
    closes = [100.0, 100.0, 110.0, 115.0, 120.0, 118.0]
    opens = [100.0, 100.0, 108.0, 111.0, 116.0, 119.0]
    rise = mc.Move(mc.RISES, 1, 4, 0.2, 0.01, 0.5, True)
    assert (
        mc.capturable_share(closes, opens, rise, 2) == (120 - 108) / 20
    )  # known before the open of t0: the gap is lost
    assert mc.capturable_share(closes, opens, rise, 3) == (120 - 111) / 20  # an opening gap: bought at t0 + 1
    assert mc.capturable_share(closes, opens, rise, 5) == 0.0  # known after the top
    assert mc.capturable_share(closes, opens, rise, None) is None
    fall = mc.Move(mc.FALLS, 0, 2, 0.1, 0.01, 0.5, True)
    assert mc.capturable_share([100.0, 95.0, 90.0], [100.0, 98.0, 94.0], fall, 1) == (90 - 98) / (90 - 100)


def test_controls_are_split_like_the_rises():
    alloc = mc.allocate({("P0", "a", 2020): 3, ("P0", "a", 2021): 1, ("P1", "b", 2021): 1}, 10)
    assert sum(alloc.values()) == 10 and alloc[("P0", "a", 2020)] == 6 and alloc[("P0", "a", 2021)] == 2
    assert mc.allocate({"x": 0}, 5) == {"x": 0}
    assert mc.cap_class(5e8, P).startswith("petite") and mc.cap_class(None, P) == "inconnue"
    assert mc.cap_class(2e10, P).startswith("grande")


# --- aggregates -----------------------------------------------------------------------------------------------------


def _unit(group: str, **present: bool | None) -> mc.Unit:
    flags = {c: mc.ABSENT for c in mc.CAUSES}
    if group == mc.CONTROLS:
        flags[mc.MARKET] = flags[mc.SECTOR] = mc.UNKNOWN
    for c, v in present.items():
        flags[c] = mc.Flag(v, mc.KNOWN_BEFORE if v else None, 3 if v else None)
    return mc.Unit(group, "P0", "petite (< 1 Md€)", 2024, flags, amplitude="5–10 %" if group != mc.CONTROLS else None)


def test_attribution_compares_rises_falls_and_controls():
    units = (
        [_unit(mc.RISES, publication_resultats=True) for _ in range(30)]
        + [_unit(mc.RISES) for _ in range(70)]
        + [_unit(mc.FALLS, publication_resultats=True) for _ in range(15)]
        + [_unit(mc.FALLS) for _ in range(85)]
        + [_unit(mc.CONTROLS, publication_resultats=True) for _ in range(10)]
        + [_unit(mc.CONTROLS, publication_resultats=None) for _ in range(10)]  # not covered
        + [_unit(mc.CONTROLS) for _ in range(90)]
    )
    att = mc.attribution(units, ["d2_breakout"], P)
    e = att["causes"][mc.EARNINGS]
    assert e["hausses"]["part"] == 0.3 and e["baisses"]["part"] == 0.15 and e["controles"]["n"] == 100
    assert e["controles"]["part"] == 0.1 and e["controles"]["unites"] == 110
    assert e["lift_hausses"] == 3.0 and e["ratio_hausses_baisses"] == 2.0
    assert e["moment"][mc.KNOWN_BEFORE]["hausses"]["k"] == 30
    assert "sans_objet" in att["causes"][mc.MARKET]["controles"] and att["causes"][mc.MARKET]["lift_hausses"] is None
    assert att["cause_principale"][mc.RISES][mc.EARNINGS]["k"] == 30
    assert att["sans_cause"][mc.RISES]["k"] == 70 and att["sans_cause"][mc.CONTROLS]["k"] == 100
    small = mc.attribution(units[:10] + units[100:105] + units[200:205], [], P)
    assert "masque" in small["causes"][mc.EARNINGS]["hausses"]  # fewer than 20: masked
    bd = mc.breakdowns(units, P)
    assert bd["annee"]["2024"]["unites"] == {"hausses": 100, "baisses": 100, "controles": 110}
    assert set(bd["amplitude"]["5–10 %"]["unites"].values()) == {100, 0}  # controls have no amplitude


def _outcome(x: float, label: int, week: str) -> mc.Outcome:
    hs = (5, 10, 20)
    return mc.Outcome(
        {h: x for h in hs}, {h: x - 0.005 for h in hs}, {h: None for h in hs}, {h: x / 2 for h in hs}, label, week
    )


def test_event_summary_masks_small_samples_and_compares_with_the_base_rate():
    events = [_outcome(0.02 + 0.001 * (k % 7), 1 if k % 2 else 0, f"2024-{k % 30:02d}") for k in range(60)]
    base = [_outcome(0.0 + 0.001 * (k % 5) - 0.002, 1 if k % 4 == 0 else 0, f"2024-{k % 30:02d}") for k in range(80)]
    s = mc.event_summary(events, base, (5, 10, 20), P)
    cell = s["5"]
    assert cell["net_cto"]["n"] == 60 and abs(cell["net_cto"]["moyenne"] - (cell["brut"]["moyenne"] - 0.005)) < 1e-4
    assert cell["net_cto"]["semaines"] == 30 and cell["net_cto"]["t_semaines"] > 3
    assert cell["net_pea"] == {"n": 0}  # not PEA-eligible
    assert s["barriere"]["part"] == 0.5 and s["taux_de_base"]["part"] == 0.25 and s["lift_barriere"] == 2.0
    tiny = mc.event_summary(events[:12], base, (5,), P)
    assert "masque" in tiny["5"]["net_cto"] and "masque" in tiny["barriere"]
    leads = mc.discoveries({"x": s, "y": tiny}, (5, 10, 20), P)
    assert leads["cellules_lues"] == 3 * 2 + 1  # net CTO and excess at three horizons, the barrier rate
    assert {x["mesure"] for x in leads["pistes"]} == {"net_cto", "exces_place"}


def test_forward_outcome_is_bought_at_the_open_after_the_cause():
    bars = [Bar(100, 101, 99, 100, 1000)] * 3 + [Bar(104, 106, 103, 105, 1000)] + [Bar(105, 107, 104, 106, 1000)] * 25
    o = mc.forward_outcome(bars, 3, (5, 10), 1.0, 0.01, MoveParams(), 0.004, None, date(2024, 1, 8), lambda h: 0.01)
    assert abs(o.gross[5] - (106 / 104 - 1)) < 1e-12  # from the open of the entry session, not the previous close
    assert abs(o.net_cto[5] - (o.gross[5] - 0.004)) < 1e-12 and o.net_pea[5] is None
    assert abs(o.excess[10] - (o.gross[10] - 0.01)) < 1e-12 and o.week == "2024-02"
    assert o.label == 0  # +5 % never reached in 10 sessions
