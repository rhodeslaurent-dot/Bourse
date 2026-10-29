"""Review of 29/10/2026: a limit waiting for a pullback (WATCH) can become an entry intraday — the score, the
market gate (R10) and the events a stop does not cover (R14) are tested at that limit at creation, as for a BUY."""

from __future__ import annotations

from datetime import timedelta

from app.db.models import Instrument, Proposal
from app.db.session import db_session
from app.services import engine
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars


def test_a_waiting_limit_carries_the_earnings_block_and_its_score(db_engine, config):
    with db_session() as s:
        _seed(s, breakout_bars())  # closes 2.5 % above its limit
        s.get(Instrument, ISIN).earnings_next_date = NOW.date() + timedelta(days=1)  # tomorrow
        s.flush()
        engine.scan(s, config, DAY, now=NOW)
        waiting = [
            p
            for p in s.query(Proposal).filter(Proposal.isin == ISIN)
            if any("en attente d'un repli" in b for b in (p.gates or {}).get("blocked", []))
        ]
        assert waiting, "the synthetic run above the limit must give a waiting limit"
        for p in waiting:
            blocked = p.gates["blocked"]
            assert any(b.startswith("BLOQUÉ : publication de résultats") for b in blocked)
            assert "score" in p.details and "portail" in p.details
            # the score reasons come before the waiting one: the intraday follow-up ignores such a limit
            assert any(b.startswith("WATCH : ratio d'admission") for b in blocked) or p.details["score"]
