"""docs/26 §26.4 — robust attention z, relative volume and the radar labels."""

import pytest

from app.data.providers.wikimedia import parse_pageviews, parse_sparql, sparql_query
from app.domain.weak_signals import (
    ATTENTION_NEWS,
    ATTENTION_NO_NEWS,
    PUMP,
    VOLUME_QUIET,
    RadarInput,
    RadarParams,
    label,
    relative_volume,
    robust_z,
)

P = RadarParams()


def test_robust_z_flags_a_spike_and_excludes_it_from_the_baseline():
    calm = [1000.0 + (i % 7) * 30 for i in range(60)]
    assert abs(robust_z(calm + [1000, 1060, 1030], P)) < 1.5
    assert robust_z(calm + [6000, 7000, 8000], P) > 3


def test_robust_z_is_unavailable_with_a_short_history():
    assert robust_z([100.0] * 20, P) is None


def test_a_rarely_read_page_is_not_a_spike_on_a_few_views():
    assert robust_z([2.0] * 60 + [6, 5, 7], P) < 6  # the MAD floor caps the noise of tiny counts
    assert robust_z([2.0] * 60 + [2, 2, 2], P) == pytest.approx(0.0)


def test_relative_volume():
    assert relative_volume([100.0] * 60 + [300.0]) == pytest.approx(3.0)
    assert relative_volume([100.0] * 10) is None


def _inp(**kw):
    base = {"attention_z": None, "rvol": None, "move_atr": None, "news_recent": 0, "small_cap": False, "rs_rank": None}
    return RadarInput(**{**base, **kw})


def test_labels_order_and_pump_guard():
    assert label(_inp(attention_z=3.5, rvol=4, small_cap=True, move_atr=2), P).label == PUMP
    # the same frenzy with a published news item is corroborated attention, not a pump
    lab = label(_inp(attention_z=3.5, rvol=4, small_cap=True, news_recent=1, rs_rank=80), P)
    assert lab.label == ATTENTION_NEWS and lab.idea
    assert label(_inp(attention_z=2.4), P).label == ATTENTION_NO_NEWS
    assert label(_inp(rvol=3, move_atr=0.2), P).label == VOLUME_QUIET
    assert label(_inp(rvol=3, move_atr=1.5), P) is None
    assert not label(_inp(attention_z=2.4, news_recent=2, rs_rank=50), P).idea
    assert label(_inp(), P) is None


def test_wikidata_and_pageviews_parsing():
    q = sparql_query(["FR0000120073"], ["fr", "en"])
    assert "wdt:P946" in q and "fr.wikipedia.org" in q and '"FR0000120073"' in q
    payload = {"results": {"bindings": [
        {"isin": {"value": "FR0000120073"}, "t_fr": {"value": "Air liquide"}, "t_en": {"value": "Air Liquide"}},
        {"isin": {"value": "FR0000120073"}, "t_fr": {"value": "Autre"}},
    ]}}  # fmt: skip
    assert parse_sparql(payload, ["fr", "en"]) == {"FR0000120073": {"fr": "Air liquide", "en": "Air Liquide"}}
    views = parse_pageviews({"items": [{"timestamp": "2026102000", "views": 812}, {"timestamp": "bad"}]})
    assert list(views.values()) == [812] and next(iter(views)).isoformat() == "2026-10-20"
