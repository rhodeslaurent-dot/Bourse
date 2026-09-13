from datetime import UTC, datetime, timedelta

from app.data.qualification import (
    Observation,
    Reference,
    compare_to_reference,
    delay_stats,
    gap_summary,
    render_report,
    volume_perimeter_hint,
)

T = datetime(2026, 9, 15, 7, 15, tzinfo=UTC)


def obs(provider, isin, delay_s, last, vol, status="realtime"):
    return Observation(provider, "X", isin, T + timedelta(seconds=delay_s), T, last, vol, status, "primary")


def test_delay_stats_and_unavailable():
    rows = [
        obs("saxo", "A", 1, 10, 100),
        obs("saxo", "A", 3, 10, 100),
        obs("eodhd_rest", "A", 900, 10, 50, "delayed"),
        Observation("eodhd_rest", "X", "A", T, None, None, None, "unavailable", "primary"),
    ]
    st = {s.provider: s for s in delay_stats(rows)}
    assert st["saxo"].median_s == 2 and st["saxo"].n == 2
    assert st["eodhd_rest"].unavailable == 1 and st["eodhd_rest"].statuses == {"delayed": 1, "unavailable": 1}


def test_compare_to_reference_and_perimeter_hint():
    rows = [obs("eodhd_ws", "A", 1, 10.05, 60), obs("saxo", "A", 1, 10.0, 100)]
    refs = [Reference("A", T, 10.0, 100), Reference("B", T, 5.0, 10)]
    d = compare_to_reference(rows, refs, ["eodhd_ws", "saxo"])
    ws_a = next(x for x in d if x.provider == "eodhd_ws" and x.isin == "A")
    assert round(ws_a.price_diff_pct, 2) == 0.5 and ws_a.volume_ratio == 0.6
    assert all(x.provider_last is None for x in d if x.isin == "B")
    assert volume_perimeter_hint(d) == {"eodhd_ws": 0.6, "saxo": 1.0}


def test_gap_summary_and_report():
    bars = [
        ("eodhd_ws", "MC.PA", T, True),
        ("eodhd_ws", "MC.PA", T + timedelta(minutes=1), False),
        ("eodhd_ws", "MC.PA", T + timedelta(minutes=4), True),
    ]
    g = gap_summary(bars)[0]
    assert g.bars == 3 and g.incomplete_bars == 1 and g.missing_minutes == 2
    text = render_report(
        "2026-09-15",
        delay_stats([obs("saxo", "A", 1, 10, 100)]),
        compare_to_reference([obs("saxo", "A", 1, 10, 100)], [Reference("A", T, 10, 100)], ["saxo"]),
        [g],
        [{"at": "x", "event": "connected"}],
    )
    assert "| saxo | 1 |" in text and "minutes manquantes" in text and "connected" in text
