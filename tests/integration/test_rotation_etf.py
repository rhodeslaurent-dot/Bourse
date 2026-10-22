"""Phase 3 — ETFs in the universe (liquidity only, PEA by list or name) and rotation."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from app.db.models import FeatureDaily, Instrument
from app.db.session import db_session
from app.domain.universe import InstrumentFacts, UniverseFilters, etf_pea_status, universe_decision
from app.services.rotation import etf_ranking, sector_strength

DAY = date(2026, 10, 21)


def test_etf_decision_and_pea():
    f = UniverseFilters(1e6, 5e5, 3e8, 1e9, 1.0, 250, [], 1e6)
    etf = InstrumentFacts("IE0002XZSHO1", "XPAR", 5.2, None, 3e6, 300)
    assert universe_decision(etf, f, "etf").included  # no market cap needed
    assert not universe_decision(etf, f).included  # as a stock it would fail on the market cap
    assert not universe_decision(InstrumentFacts("X", "XPAR", 5.2, None, 2e5, 300), f, "etf").included
    assert etf_pea_status("IE0002XZSHO1", "iShares MSCI World Swap PEA UCITS ETF", []) == (True, 0.6, "nom_etf")
    assert etf_pea_status("IE00B4L5Y983", "iShares Core MSCI World", []) == (False, 0.7, "etf_hors_pea")
    assert etf_pea_status("IE00B4L5Y983", "x", ["IE00B4L5Y983"])[2] == "liste_utilisateur"


def test_sector_strength_and_etf_ranking(db_engine, config, calendars):
    with db_session() as s:
        for k in range(8):
            isin = f"FR00000000{k:02d}"
            sector = "Energy" if k < 4 else "Utilities"
            s.add(Instrument(isin=isin, name=isin, mic="XPAR", currency="EUR", in_universe=True, sector=sector))
            s.add(FeatureDaily(isin=isin, day=DAY, rs_rank=90 - k if k < 4 else 30 + k, sessions=250))
        for k, rank in enumerate((95, 40, 70)):
            isin = f"IE00000000{k:02d}"
            s.add(Instrument(isin=isin, name=f"ETF {k} PEA", mic="XPAR", currency="EUR", in_universe=True, kind="etf",
                             pea_eligible=True, pea_source="nom_etf"))  # fmt: skip
            s.add(FeatureDaily(isin=isin, day=DAY, rs_rank=rank, rs_3m=0.05, sessions=250))
        s.flush()
        sec = sector_strength(s, DAY)
        assert list(sec) == ["Energy", "Utilities"] and sec["Energy"]["centile"] == 100.0
        etfs = etf_ranking(s, DAY)
        assert [e["rang"] for e in etfs] == [95, 70, 40] and etfs[0]["porteur"]
    from app.main import create_app

    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        html = c.get("/rotation").text
    assert "Energy" in html and "ETF 0 PEA" in html and "premier quintile" in html
