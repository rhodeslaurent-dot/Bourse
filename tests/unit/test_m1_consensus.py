"""ADR-026 (18/11/2026): the medium-term idea — analysts' consensus + long trend (or results welcomed), never chased,
a stop under the structure of three months, the consensus average as target; never a guessed rating scale."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.domain.detectors import ConsensusFacts, DetectorContext, M1ConsensusTrend, run_detectors
from app.domain.indicators import Bar
from tests.unit.test_d1_gap import bars as sliding_bars

PARAMS = {"enabled": True, "min_analysts": 10, "min_upside": 0.15, "max_above_mm50": 0.08, "entry_max_pct": 0.01,
          "stop_lookback": 60, "max_stop_pct": 0.15, "recommendations_ok": ["strong_buy", "buy"]}  # fmt: skip


def rising() -> list[Bar]:
    out, price = [], 60.0
    for i in range(230):
        price *= 1.002 if i % 7 else 0.995
        out.append(Bar(price * 0.998, price * 1.008, price * 0.992, price, 100_000))
    return out


def cons(**kw: object) -> ConsensusFacts:
    base = ConsensusFacts(target=rising()[-1].close * 1.25, n_analysts=24, low=70.0, high=140.0, rating=1.4,
                          recommendation="buy", day="2026-11-17", source="tradingview")  # fmt: skip
    return replace(base, **kw)  # type: ignore[arg-type]


def ctx(c: ConsensusFacts | None, welcomed: str | None = None) -> DetectorContext:
    return DetectorContext(rs_rank=70, zones=[], wick_buffer_pct=0.005, stop_buffer_atr_mult=0.25, consensus=c,
                           welcomed=welcomed)  # fmt: skip


def test_a_long_uptrend_with_a_far_consensus_target_gives_a_medium_term_idea():
    (st,) = M1ConsensusTrend(PARAMS).detect(rising(), ctx(cons()))
    last = rising()[-1]
    assert st.detector == "m1_consensus_trend" and st.horizon == "moyen_terme"
    assert st.entry_level == pytest.approx(last.close) and st.p_max_technical == pytest.approx(last.close * 1.01)
    assert st.stop < min(b.low for b in rising()[-60:]) and st.targets == [pytest.approx(last.close * 1.25)]
    ev = st.evidence
    assert (
        ev["potentiel"] == pytest.approx(0.25) and ev["nb_analystes"] == 24 and ev["recommandation_analystes"] == "buy"
    )
    assert ev["tendance"].startswith("longue tendance haussière") and ev["mesure"].startswith("non mesuré")


@pytest.mark.parametrize(
    "change",
    [
        {"n_analysts": 6},  # too few analysts
        {"target": 1.0},  # target under the price: no upside (filled below)
        {"recommendation": "hold"},  # the analysts do not lean to buying
    ],
)
def test_no_idea_without_the_analysts(change):
    if change.get("target") == 1.0:
        change = {"target": rising()[-1].close * 1.08}  # 8 % < 15 %
    assert M1ConsensusTrend(PARAMS).detect(rising(), ctx(cons(**change))) == []


def test_a_broken_trend_needs_results_welcomed_and_the_scale_of_the_rating_is_never_guessed():
    slide = sliding_bars()
    c = cons(target=slide[-1].close * 1.3)
    assert M1ConsensusTrend(PARAMS).detect(slide, ctx(c)) == []  # under its 200-day average: not on its own
    (st,) = M1ConsensusTrend(PARAMS).detect(slide, ctx(c, "résultats publiés le 17/11, salués : +6 %"))
    assert "résultats salués" in st.evidence["tendance"]
    assert M1ConsensusTrend(PARAMS).detect(rising(), ctx(cons(rating=4.6))) != []  # no scale declared: not used
    scaled = {**PARAMS, "rating_best": 1, "rating_max": 2.0}
    assert M1ConsensusTrend(scaled).detect(rising(), ctx(cons(rating=4.6))) == []
    assert M1ConsensusTrend(scaled).detect(rising(), ctx(cons(rating=1.6))) != []


def test_not_chased_and_only_run_with_a_consensus():
    chased = rising()
    last = chased[-1]
    chased[-1] = Bar(last.open, last.close * 1.2, last.low, last.close * 1.19, last.volume)  # 19 % up in a day
    assert M1ConsensusTrend(PARAMS).detect(chased, ctx(cons(target=last.close * 2))) == []
    assert run_detectors({"m1_consensus_trend": PARAMS}, rising(), ctx(None)) == []  # backtests have no consensus
    assert len(run_detectors({"m1_consensus_trend": PARAMS}, rising(), ctx(cons()))) == 1
