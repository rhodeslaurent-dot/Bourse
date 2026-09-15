"""docs/06 annexe: every indicator has a test on a frozen OHLC fixture."""

import csv
from pathlib import Path

import pytest

from app.domain.indicators import (
    Bar,
    adr,
    atr,
    breadth_above_ma,
    compute_daily_features,
    consolidation,
    distribution_days,
    gap_open,
    percentile_rank,
    pivot,
    returns,
    rs_score,
    rsi,
    rvol_day,
    sma,
    sma_series,
    ti65,
    true_range,
)

FX = Path(__file__).resolve().parents[1] / "fixtures" / "ohlc"


def load(name: str) -> list[Bar]:
    with open(FX / name, encoding="utf-8") as fh:
        return [
            Bar(float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), int(r["volume"]))
            for r in csv.DictReader(fh)
        ]


@pytest.fixture(scope="module")
def up() -> list[Bar]:
    return load("uptrend_pullback.csv")


@pytest.fixture(scope="module")
def flat() -> list[Bar]:
    return load("flat.csv")


def test_sma_and_series():
    assert sma([1, 2, 3, 4], 2) == 3.5 and sma([1], 2) is None
    assert sma_series([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]


def test_atr_wilder_hand_computed():
    bars = [Bar(10, 11, 9, 10, 1)] + [Bar(10, 10.5 + i * 0.1, 9.5, 10 + i * 0.1, 1) for i in range(15)]
    tr = true_range(bars)
    assert tr[0] == 2.0
    a = atr(bars, 14)
    first = sum(tr[1:15]) / 14
    expected = (first * 13 + tr[15]) / 14
    assert a == pytest.approx(expected)
    assert atr(bars[:10], 14) is None


def test_adr_and_rvol(up):
    a = adr(up, 20)
    assert a is not None and 0.005 < a < 0.05
    r = rvol_day(up, 20)
    assert r is not None and r > 3  # last bar carries an injected volume spike
    assert rvol_day(up[:10], 20) is None


def test_returns_and_rs(up, flat):
    closes = [b.close for b in up]
    assert returns(closes, 21) == pytest.approx(closes[-1] / closes[-22] - 1)
    assert rs_score(closes) is not None and rs_score(closes) > rs_score([b.close for b in flat])
    assert rs_score(closes[:100]) is None


def test_percentile_rank():
    pop = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile_rank(5.0, pop) == 100 and percentile_rank(1.0, pop) == 0 and percentile_rank(3.0, pop) == 50
    assert percentile_rank(3.0, [3.0, 3.0, 3.0]) == 50.0


def test_ti65_pivot_high52_consolidation(up):
    closes = [b.close for b in up]
    assert ti65(closes) is not None and ti65(closes[:50]) is None
    p = pivot(up, 60)
    assert p == max(b.high for b in up[-61:-1])
    assert pivot(up, 60) <= max(b.high for b in up[-250:])
    c = consolidation(up, 20)
    assert c == (max(b.high for b in up[-21:-1]) - min(b.low for b in up[-21:-1])) / min(b.low for b in up[-21:-1])


def test_rsi_bounds_and_pullback(up):
    closes = [b.close for b in up]
    r = rsi(closes, 14)
    assert 0 <= r <= 100
    assert rsi([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15], 14) == 100.0
    during_pullback = rsi(closes[:288], 14)
    assert during_pullback < 50


def test_gap_and_distribution_days():
    assert gap_open(103, 100) == pytest.approx(0.03)
    idx = [Bar(1, 1, 1, 100, 100)]
    for i in range(1, 30):
        down = i % 3 == 0
        idx.append(Bar(1, 1, 1, idx[-1].close * (0.995 if down else 1.002), 150 if down else 100))
    assert distribution_days(idx, 25) == len([i for i in range(5, 30) if i % 3 == 0])


def test_breadth():
    up_s = [100 + i for i in range(60)]
    down_s = [200 - i for i in range(60)]
    assert breadth_above_ma({"a": up_s, "b": down_s, "c": up_s}, 50) == pytest.approx(2 / 3)
    assert breadth_above_ma({"a": [1, 2]}, 50) is None


def test_daily_features_bundle(up, flat):
    f = compute_daily_features(up)
    assert f.complete and f.mm200 is not None and f.rs_score is not None and f.atr14 > 0
    assert f.mm50 > f.mm200  # uptrend
    assert not compute_daily_features(up[:100]).complete
