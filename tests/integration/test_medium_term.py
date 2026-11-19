"""ADR-026 (18/11/2026): medium-term ideas through the usual engine — the consensus as the engine cross-checks it,
the Python sizing and gates of any card, their own section on the Opportunités page."""

from __future__ import annotations

from datetime import timedelta

from app.db.models import AnalystConsensus, Proposal, Signal
from app.db.session import db_session
from app.services import engine
from tests.integration.test_engine import DAY, ISIN, NOW, _seed
from tests.unit.test_m1_consensus import rising

LAST = rising()[-1].close


def _consensus(s, *, target: float, n: int = 24, day=DAY) -> None:  # type: ignore[no-untyped-def]
    s.add(AnalystConsensus(isin=ISIN, day=day, source="tradingview", received_at=NOW,
                           data={"objectif_moyen": target, "objectif_bas": LAST * 0.9, "objectif_haut": LAST * 1.6,
                                 "nb_analystes": n, "note": 1.5, "devise": "EUR"}))  # fmt: skip
    s.flush()


def _m1(s):  # type: ignore[no-untyped-def]
    return [(p, sg) for p in s.query(Proposal) if (sg := s.get(Signal, p.signal_id)).detector == "m1_consensus_trend"]


def test_a_consensus_far_above_a_rising_value_gives_a_sized_medium_term_card(db_engine, config):
    with db_session() as s:
        _seed(s, rising())
        _consensus(s, target=LAST * 1.3)
        rep = engine.scan(s, config, DAY, isins=[ISIN], now=NOW)
        ((p, sig),) = _m1(s)
        assert "par détecteur : " in rep.note() and f"m1_consensus_trend {p.action} 1" in rep.note()
        again = engine.scan(s, config, DAY, isins=[ISIN], now=NOW + timedelta(minutes=5))
        assert "m1_consensus_trend déjà 1" in again.note()  # a rescan still says how many ideas the day holds
        assert p.horizon == "moyen_terme" and p.targets == [LAST * 1.3]
        assert sig.evidence["nb_analystes"] == 24 and sig.evidence["source_consensus"] == "tradingview"
        assert sig.evidence["note_analystes"] == 1.5  # shown as published, never used without a declared scale
        assert p.stop is not None and p.stop < LAST * 0.95  # a stop under three months of structure
        assert p.action in ("BUY", "WATCH") and (p.details or {}).get("comptes")  # costs per account, as any card
        if p.action == "BUY":
            assert p.shares and p.risk_eur  # the quantity is Python's (rule 7)


def test_no_card_without_enough_analysts_or_with_an_old_consensus(db_engine, config):
    with db_session() as s:
        _seed(s, rising())
        _consensus(s, target=LAST * 1.3, n=5)
        engine.scan(s, config, DAY, isins=[ISIN], now=NOW)
        assert _m1(s) == []
    with db_session() as s:
        s.query(AnalystConsensus).delete()
        _consensus(s, target=LAST * 1.3, day=DAY - timedelta(days=12))
        engine.scan(s, config, DAY, isins=[ISIN], now=NOW + timedelta(minutes=5))
        assert _m1(s) == []  # a consensus not read for 12 days is not today's target


def test_the_page_shows_them_in_their_own_section(raw, calendars, db_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import validate_params
    from app.main import create_app

    config = validate_params(raw)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with db_session() as s:
        _seed(s, rising())
        _consensus(s, target=LAST * 1.3)
        engine.scan(s, config, DAY, isins=[ISIN], now=NOW)
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        page = c.get("/opportunites").text
    assert "Idées moyen terme — analystes et tendance" in page and "Non mesuré par l&#39;outil" in page
