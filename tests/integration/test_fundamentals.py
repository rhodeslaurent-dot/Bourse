"""Phase 3 — free fundamentals (Yahoo) and D7 « tendance + qualité » with the analyst target."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.data.providers.yahoo_fundamentals import clean, yahoo_symbol
from app.db.models import Fundamentals, Instrument, Proposal
from app.db.session import db_session
from app.services import engine
from app.services.fundamentals import analyst_target, facts, refresh
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars


def test_symbols_and_cleaning():
    assert yahoo_symbol("TTE.PA") == "TTE.PA" and yahoo_symbol("SAP.XETRA") == "SAP.DE"
    assert (
        yahoo_symbol("VOLV_B.ST") == "VOLV-B.ST" and yahoo_symbol("AAPL.US") == "AAPL" and yahoo_symbol("X.ZZ") is None
    )
    assert clean({"revenueGrowth": 0.12, "trailingPE": float("nan"), "targetMeanPrice": "bad", "sector": "Energy"}) == {
        "revenueGrowth": 0.12,
        "sector": "Energy",
    }


def test_refresh_facts_and_analyst_target(db_engine):
    info = {
        "revenueGrowth": 0.14, "operatingMargins": 0.18, "totalDebt": 4e10, "ebitda": 3e10, "trailingPE": 9.5,
        "targetMeanPrice": 118.0, "targetLowPrice": 95.0, "targetHighPrice": 135.0, "numberOfAnalystOpinions": 21,
        "recommendationKey": "buy", "currency": "EUR", "financialCurrency": "USD",
    }  # fmt: skip
    with db_session() as s:
        s.add(Instrument(isin=ISIN, name="TotalEnergies", mic="XPAR", currency="EUR", in_universe=True,
                         ticker_eodhd="TTE.PA", sector="Energy"))  # fmt: skip
        s.flush()
        calls = []
        rep = refresh(s, date(2026, 10, 25), info_fn=lambda sym: calls.append(sym) or info, sleep=lambda _: None)
        assert rep["ok"] == 1 and calls == ["TTE.PA"]
        assert refresh(s, date(2026, 10, 25), info_fn=lambda sym: info, sleep=lambda _: None)["deja"] == 1
        ff, shown = facts(s, s.get(Instrument, ISIN))
        assert ff.revenue_growth == 0.14 and round(ff.debt_to_ebitda, 2) == 1.33 and ff.sector_median_pe is None
        assert shown["nb_analystes"] == 21 and shown["devise"] == "USD"
        assert analyst_target(shown, 3, "EUR") is None  # target in another currency: shown, never converted
        shown["devise"] = "EUR"
        assert (
            analyst_target(shown, 3, "EUR") == 118.0 and analyst_target({**shown, "nb_analystes": 2}, 3, "EUR") is None
        )


def test_engine_scores_f_and_shows_consensus(db_engine, config):
    with db_session() as s:
        _seed(s, breakout_bars())
        s.add(Fundamentals(isin=ISIN, day=DAY, source="yahoo", symbol="TTE.PA", received_at=datetime.now(UTC),
                           data={"revenueGrowth": 0.14, "operatingMargins": 0.18, "targetMeanPrice": 118.0,
                                 "numberOfAnalystOpinions": 21, "currency": "EUR"}))  # fmt: skip
        s.flush()
        engine.scan(s, config, DAY, now=NOW)
        ps = [p for p in s.query(Proposal).filter(Proposal.isin == ISIN) if (p.details or {}).get("score")]
        assert ps, "at least one scored proposal"
        d = ps[0].details
        assert d["score"]["blocs"]["F"] is not None and d["fondamentaux"]["objectif_analystes"] == 118.0
        assert any(118.0 in (p.targets or []) for p in s.query(Proposal).filter(Proposal.isin == ISIN))
