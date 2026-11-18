"""docs/06 D1 (18/11/2026): the opening gap with a catalyst — fires only from the opening scans, on a held gap, a
volume pace far above the usual one and a catalyst since the previous close."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain.detectors import D1GapCatalyst, DetectorContext, GapSnapshot
from app.domain.indicators import Bar

PARAMS = {"enabled": True, "gap_min_large_adv_eur": 5_000_000, "gap_min_large": 0.03, "gap_min_other": 0.05,
          "rvol15_min": 2.0, "gap_high_risk": 0.15, "entry_max_pct_above_or_high": 0.01, "stop_max_adr_mult": 1.5,
          "hold_max_below_high_pct": 0.02}  # fmt: skip


def bars() -> list[Bar]:
    """A rise to 120, then a slide back to 100: levels above the price for the targets."""
    out, price = [], 80.0
    for _ in range(150):
        price *= 1.0027
        out.append(Bar(price * 0.997, price * 1.01, price * 0.99, price, 100_000))
    for _ in range(70):
        price *= 0.9975
        out.append(Bar(price * 1.002, price * 1.01, price * 0.99, price, 100_000))
    return out


def gap(**kw: object) -> GapSnapshot:
    prev = bars()[-1].close
    base = GapSnapshot(prev_close=prev, open=prev * 1.07, high=prev * 1.078, low=prev * 1.066, last=prev * 1.075,
                       volume=400_000, adv_shares=100_000, elapsed=0.05, adv_eur=5e8,
                       catalyst="résultats publiés le 17/11 à 22:05")  # fmt: skip
    return replace(base, **kw)  # type: ignore[arg-type]


def ctx(g: GapSnapshot | None) -> DetectorContext:
    return DetectorContext(rs_rank=40, zones=[], wick_buffer_pct=0.005, stop_buffer_atr_mult=0.25, gap=g)


def test_a_held_gap_with_volume_and_a_catalyst_gives_a_setup():
    g = gap()
    (st,) = D1GapCatalyst(PARAMS).detect(bars(), ctx(g))
    assert st.detector == "d1_gap_catalyst" and st.entry_level == pytest.approx(g.high)
    assert st.p_max_technical == pytest.approx(g.high * 1.01)
    assert st.stop < g.low and st.level_low == g.low  # under the day's low, with the ATR buffer
    assert st.targets and all(t > st.p_max_technical for t in st.targets)  # levels, never an R multiple
    ev = st.evidence
    assert ev["ecart_ouverture"] == pytest.approx(0.07) and ev["rythme_volume"] == pytest.approx(
        50.0
    )  # 400 000 / (100 000 × 8 %)
    assert ev["catalyseur"].startswith("résultats") and ev["cloture_veille"] == g.prev_close


@pytest.mark.parametrize(
    "change",
    [
        {"catalyst": None},  # a move without a fact since the close
        {"open": bars()[-1].close * 1.02},  # gap under 3 % for a large value
        {"volume": 2_000.0},  # no unusual volume
        {"volume": 15_000.0},  # the usual volume of the first minutes (8 % of a day) is not a surge
        {"last": bars()[-1].close * 1.065},  # the gap is being given back (under the open)
        {"adv_shares": None},  # usual volume unknown: never a guess
    ],
)
def test_no_setup_without_every_condition(change):
    assert D1GapCatalyst(PARAMS).detect(bars(), ctx(gap(**change))) == []


def test_a_small_value_needs_a_wider_gap_and_normal_scans_never_fire():
    small = gap(adv_eur=1e6, open=bars()[-1].close * 1.04)
    assert D1GapCatalyst(PARAMS).detect(bars(), ctx(small)) == []  # 4 % < 5 % for a value under 5 M€ a day
    assert D1GapCatalyst(PARAMS).detect(bars(), ctx(None)) == []  # the evening and morning scans have no gap


def test_a_stop_wider_than_the_limit_is_flagged():
    wide = gap(low=bars()[-1].close * 0.99, open=bars()[-1].close * 1.07)
    (st,) = D1GapCatalyst(PARAMS).detect(bars(), ctx(wide))
    assert st.warnings and "stop trop large" in st.warnings[0] and not st.testable
