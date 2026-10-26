"""ADR-013 (user decision of 26/10/2026, option B): d2, d3, d4 and d7 lose at full cost over 8 years (backtest
n° 2): their proposals stay visible as WATCH with the reason, never as BUY, and never in the order book."""

from __future__ import annotations

from app.db.models import Proposal
from app.db.session import db_session
from app.services import engine
from app.web.opportunities_routes import order_book
from tests.integration.test_engine import DAY, NOW, _seed, breakout_bars


def test_watch_only_detectors_never_propose_a_buy(db_engine, config, example_raw):
    config.raw["scoring"]["watch_only_detectors"] = list(example_raw["scoring"]["watch_only_detectors"])
    config.params.scoring["watch_only_detectors"] = list(example_raw["scoring"]["watch_only_detectors"])
    assert set(example_raw["scoring"]["watch_only_detectors"]) == {
        "d2_breakout", "d3_range_expansion", "d4_pullback", "d7_trend_quality"}  # fmt: skip
    with db_session() as s:
        _seed(s, breakout_bars())
        engine.scan(s, config, DAY, now=NOW)
        props = s.query(Proposal).all()
        assert props and all(p.action != "BUY" for p in props)
        assert all(any("espérance historique négative" in b for b in (p.gates or {}).get("blocked", [])) for p in props)
        items = [{"p": p, "name": p.isin, "snapshot_price": None} for p in props]
        assert order_book(items) == []  # nothing to post at the broker
