"""docs/18 §18.5 (A18.4, A18.5) — statistics, comparable history, BUY → WATCH rule."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.learning import (
    Sample,
    bucket_label,
    group_stats,
    mean_ci,
    should_demote,
    threshold_table,
    track_record,
)

T0 = datetime(2026, 6, 1, 8, tzinfo=UTC)


def mk(i, r, detector="external", source="zonebourse", group="ignoree", drift=None, days_final=5):
    created = T0 + timedelta(days=i)
    return Sample(
        i,
        created,
        created + timedelta(days=days_final),
        r,
        None if r is None else r / 20,
        {"detector": detector, "source": source, "decision_group": group},
        {"drift": drift},
    )


def test_group_stats_ci_and_reliability():
    ss = [mk(i, r) for i, r in enumerate([1.0, -1.0, 2.0, -1.0, 0.5] * 5)]
    ss += [mk(100 + i, -1.0, group="prise") for i in range(3)]
    g = {x.key: x for x in group_stats(ss, "decision_group", min_display=20, min_judge=60)}
    ign = g["ignoree"]
    assert ign.n == 25 and ign.wins == 15 and ign.win_rate == pytest.approx(0.6)
    assert ign.mean_r == pytest.approx(0.3) and ign.ci_low < 0.3 < ign.ci_high
    assert ign.reliability == "indicatif" and g["prise"].reliability == "insuffisant"
    assert mean_ci([1.0]) == (1.0, None, None)


def test_threshold_table_buckets():
    ss = [mk(i, 1.0 if i % 2 else -1.0, drift=d) for i, d in enumerate([0.005, 0.015, 0.025, 0.035, 0.01, 0.03])]
    t = threshold_table(ss, "drift", [0.01, 0.02, 0.03], 1, 60, fmt=lambda v: f"{v:.0%}")
    assert [x.key for x in t] == ["< 1%", "1% – 2%", "2% – 3%", "≥ 3%"]
    assert bucket_label(None, [1.0]) is None


def test_track_record_no_look_ahead_and_shrinkage():
    hist = [mk(i, -1.0) for i in range(30)]
    at = T0 + timedelta(days=40)
    tr = track_record(hist, at, "external", "zonebourse", k=20, min_n=20, favorable=0.3, unfavorable=-0.2)
    assert tr.scope == "détecteur + source" and tr.n == 30 and tr.shrunk_r == pytest.approx(-0.6)
    assert tr.verdict == "defavorable"
    # at an earlier date, only outcomes final by then count
    early = track_record(
        hist, T0 + timedelta(days=10), "external", "zonebourse", k=20, min_n=20, favorable=0.3, unfavorable=-0.2
    )
    assert early.n == 6 and early.verdict == "insuffisant"


def test_track_record_falls_back_to_detector():
    hist = [mk(i, 1.0, source="momentum") for i in range(25)] + [mk(50 + i, 1.0) for i in range(3)]
    tr = track_record(
        hist, T0 + timedelta(days=90), "external", "zonebourse", k=20, min_n=20, favorable=0.3, unfavorable=-0.2
    )
    assert tr.scope == "détecteur" and tr.n == 28 and tr.verdict == "favorable"


def test_demotion_rule():
    hist = [mk(i, -0.5) for i in range(60)]
    tr = track_record(
        hist, T0 + timedelta(days=200), "external", "zonebourse", k=20, min_n=20, favorable=0.3, unfavorable=-0.2
    )
    assert should_demote("BUY", tr, 60, True)
    assert not should_demote("BUY", tr, 61, True) and not should_demote("WATCH", tr, 60, True)
    assert not should_demote("BUY", tr, 60, False)
