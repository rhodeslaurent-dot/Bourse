"""Lot 2.2 — free data (docs/22 §22.7): ECB FX, economic calendar, macro dashboard with fallback
and plausible ranges, conjunctural context and gate, quote at each analysis with plausibility,
official TTF list, non-euro market caps, home card."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from app.data.dto import DataStatus, MarketPerimeter, PriceType, Quote
from app.data.providers import ecb_fx, forexfactory, ttf_list
from app.db.models import Instrument, MacroPoint, NewsItem, PriceEod
from app.db.session import db_session
from app.services import macro
from app.services.market_data import fx_to_eur
from app.services.quotes import plausible_quotes, quote_at_analysis
from app.services.ttf import ttf_status

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
ECB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<Cube><Cube time="2026-10-21"><Cube currency="USD" rate="1.0812"/><Cube currency="SEK" rate="11.42"/></Cube>
<Cube time="2026-10-20"><Cube currency="USD" rate="1.0790"/><Cube currency="SEK" rate="11.40"/></Cube></Cube>
</gesmes:Envelope>"""


def test_ecb_fx_parsed_and_stored(db_engine):
    pts = ecb_fx.parse(ECB_XML)
    assert len(pts) == 4 and pts[0].day == date(2026, 10, 21) and pts[0].currency == "USD"
    with db_session() as s:
        assert macro.store_fx(s, pts) == 4
        assert round(fx_to_eur(s, "SEK"), 5) == round(1 / 11.42, 5)
        assert fx_to_eur(s, "SEK", date(2026, 10, 20)) == 1 / 11.40
        assert fx_to_eur(s, "JPY") is None  # never guessed


def test_economic_calendar_filtered(db_engine):
    items = [
        {"title": "Main Refinancing Rate", "country": "EUR", "date": "2026-10-29T09:15:00-04:00", "impact": "High",
         "forecast": "2.00%", "previous": "2.00%"},
        {"title": "Federal Funds Rate", "country": "USD", "date": "2026-10-28T14:00:00-04:00", "impact": "High"},
        {"title": "BRC Shop Price Index", "country": "GBP", "date": "2026-10-27T20:01:00-04:00", "impact": "Low"},
        {"title": "", "country": "EUR", "date": "2026-10-27T20:01:00-04:00", "impact": "High"},
    ]  # fmt: skip
    ev = forexfactory.parse(items)
    assert len(ev) == 3 and ev[0].ts == datetime(2026, 10, 29, 13, 15, tzinfo=UTC)
    with db_session() as s:
        assert macro.store_events(s, ev, ["EUR", "USD"], ["High", "Medium"]) == 2
        assert macro.store_events(s, ev, ["EUR", "USD"], ["High", "Medium"]) == 0  # idempotent
        up = macro.upcoming_events(s, NOW, days=8)
        assert [e.title for e in up] == ["Federal Funds Rate", "Main Refinancing Rate"]


def _days(n: int, end: date = date(2026, 10, 21)) -> list[date]:
    return [end - timedelta(days=i) for i in range(n)][::-1]


def test_snapshot_fallback_range_and_context(db_engine, config):
    calls: list[str] = []

    def yf(symbol, start, end):
        calls.append(f"yf:{symbol}")
        if symbol == "^VIX":
            return [(d, 15.0 + (i % 20)) for i, d in enumerate(_days(300))]
        if symbol == "BZ=F":
            return [(d, 96.0 + i) for i, d in enumerate(_days(10))]  # 96 … 105 → > 100
        if symbol == "^TNX":
            return [(date(2026, 10, 21), 50.7)]  # scaled ×10 by mistake → out of range, rejected
        return []

    def tradingview(symbol, start, end):
        calls.append(f"tv:{symbol}")
        return {"cfd/TVC:FR10Y": [(date(2026, 10, 21), 4.85)], "cfd/TVC:DE10Y": [(date(2026, 10, 21), 3.45)]}.get(
            symbol, []
        )

    def ecb(symbol, start, end):
        return [(date(2026, 10, 21), 1.0812)] if symbol == "USD" else []

    with db_session() as s:
        rep = macro.snapshot(s, config, date(2026, 10, 21), {"yfinance": yf, "tradingview": tradingview, "ecb": ecb})
        assert rep.sources["eurusd"] == "ecb:USD" and rep.sources["fr10y"] == "tradingview:cfd/TVC:FR10Y"
        assert "us10y" in rep.missing and any("us10y" in r for r in rep.rejected)
        assert "yf:^TNX" in calls and "tv:cfd/TVC:US10Y" in calls  # rejected, then fallback tried
        assert "hors plage" in rep.note()
        vals = macro.latest(s, date(2026, 10, 21))
        assert vals["brent"].value == 105.0 and vals["brent"].source == "yfinance"
        inputs = macro.conjuncture_inputs(s, config, date(2026, 10, 21))
        assert round(inputs["oat_bund_spread_bp"]) == 140 and inputs["us10y_pct"] is None
        assert inputs["vix_pct_1y"] is not None and inputs["index_5d_change_pct"] is None  # no CAC bars: unknown
        assert macro.brent_srd_mode(s, config, date(2026, 10, 21)) == "restreint"
        ctx = macro.market_context(s, config, date(2026, 10, 21))
        assert ctx.level in ("modere", "eleve") and ctx.gate in ("restreint", "restreint_plus")  # no publication
        assert ctx.components["us10y_pct"] == "inconnu"
        n = macro.snapshot(
            s, config, date(2026, 10, 21), {"yfinance": yf, "tradingview": tradingview, "ecb": ecb}
        ).stored
        assert n == 0  # idempotent


def _quote(price: float) -> Quote:
    return Quote(
        source="eodhd_rest",
        market_perimeter=MarketPerimeter.PRIMARY,
        received_at=NOW,
        processed_at=NOW,
        data_status=DataStatus.DELAYED,
        symbol="X",
        market_timestamp=NOW - timedelta(minutes=15),
        price_type=PriceType.LAST,
        last=price,
    )


def test_quote_at_analysis_plausibility(db_engine, config):
    with db_session() as s:
        for isin in ("FR0000120271", "FR0000131104", "FR0000120578"):
            s.add(Instrument(isin=isin, name=isin, mic="XPAR", currency="EUR"))
            s.add(PriceEod(isin=isin, day=date(2026, 10, 21), open=60, high=61, low=59, close=60.7, volume=1,
                           source="eodhd", received_at=NOW, data_status="eod"))  # fmt: skip
        s.add(NewsItem(source="actusnews", guid="g", dedup_hash="g", fetched_at=NOW, title="Résultats",
                       isins=["FR0000131104"]))  # fmt: skip
        s.flush()
        quotes = {"FR0000120271": _quote(61.0), "FR0000131104": _quote(87.2), "FR0000120578": _quote(87.2)}
        res = quote_at_analysis(s, list(quotes), config, fallback=lambda xs: {i: quotes[i] for i in xs}, now=NOW)
        assert res["FR0000120271"].plausible
        assert res["FR0000131104"].plausible and "nouvelle" in res["FR0000131104"].note
        arkema = res["FR0000120578"]  # référentiel §11.2 : 87,20 transmis pour 60,70 réel
        assert not arkema.plausible and arkema.note.startswith("à vérifier")
        assert arkema.as_fact()["statut"] == "delayed"
        kept = plausible_quotes(s, list(quotes), config, fallback=lambda xs: {i: quotes[i] for i in xs})
        assert set(kept) == {"FR0000120271", "FR0000131104"}


def test_ttf_official_list_and_estimate(tmp_path):
    html = (
        "<table><tr><th>Société</th><th>ISIN</th></tr>"
        "<tr><td>TOTALENERGIES SE</td><td>FR0000120271</td></tr>"
        "<tr><td>AIRBUS SE</td><td>NL0000235190</td></tr></table>"
    )
    rows = ttf_list.parse(html)
    assert rows == [("FR0000120271", "TOTALENERGIES SE"), ("NL0000235190", "AIRBUS SE")]
    path = tmp_path / "ttf.yaml"
    path.write_text("year: 2026\nisins: [FR0000120271, NL0000235190]\n", encoding="utf-8")
    assert ttf_status("NL0000235190", 1e11, 1e9, 2026, path) == (True, "liste officielle 2026 (BOFiP)")
    due, basis = ttf_status("LU1598757687", 4e10, 1e9, 2026, path)  # ArcelorMittal : siège au Luxembourg
    assert not due
    due, basis = ttf_status("FR0000131104", 2e10, 1e9, 2027, path)
    assert due and basis.startswith("estimation")


def test_non_euro_market_cap_converted(config):
    from app.data.dto import ScreenerRow
    from app.scheduler.jobs import universe_refresh as job
    from app.services.universe import SymbolRow

    class FakeTV:
        max_requests = 2
        min_interval = 0

        def __init__(self, **kw):
            pass

        def scan(self, markets, columns=None):
            return [
                ScreenerRow(ticker="OMXSTO:VOLV_B", market_cap=500e9, received_at=NOW,
                            extra={"currency": "SEK", "fundamental_currency_code": "SEK"}),
                ScreenerRow(ticker="OMXCOP:NOVO_B", market_cap=2e12, received_at=NOW, extra={"currency": "DKK"}),
            ]  # fmt: skip

    import app.data.providers.tradingview_screener as tvmod

    orig = tvmod.TradingViewScreener
    tvmod.TradingViewScreener = FakeTV  # type: ignore[misc]
    try:
        rows = [
            SymbolRow(isin="SE0000115446", name="Volvo", code="VOLV_B", exchange="ST", currency="SEK", country="SE"),
            SymbolRow(isin="DK0062498333", name="Novo", code="NOVO_B", exchange="CO", currency="DKK", country="DK"),
        ]
        out = job.enrich_with_screener(rows, config, {"SEK": 1 / 11.42, "DKK": None})
    finally:
        tvmod.TradingViewScreener = orig  # type: ignore[misc]
    assert round(out[0].market_cap_eur / 1e9, 1) == 43.8
    assert out[1].market_cap_eur is None  # no DKK rate → unknown, never guessed


def test_saxo_uic_learned(db_engine):
    from app.scheduler.jobs.portfolio_sync import remember_saxo_uics

    with db_session() as s:
        s.add(Instrument(isin="FR0000120271", name="TTE", mic="XPAR", currency="EUR"))
        s.flush()
        assert remember_saxo_uics(s, [("FR0000120271", "21"), (None, "99")]) == 1
        assert s.get(Instrument, "FR0000120271").saxo_uic == "21"


def test_home_shows_macro_card(db_engine, config, calendars):
    from app.main import create_app

    with db_session() as s:
        s.add(MacroPoint(indicator="brent", day=date(2026, 10, 21), value=107.82, source="yfinance", symbol="BZ=F",
                         received_at=NOW, data_status="eod"))  # fmt: skip
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        html = c.get("/").text
    assert "Contexte macro" in html and "Brent ($)" in html and "107.82" in html and "risque conjoncturel" in html


def test_tradingview_latest_values_and_ttf_file(tmp_path):
    import httpx

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/cfd/scan"
        return httpx.Response(200, json={"data": [{"s": "TVC:FR10Y", "d": [3.48]}, {"s": "TVC:DE10Y", "d": [None]}]})

    from app.data.providers.tradingview_screener import latest_values

    client = httpx.Client(base_url="https://scanner.tradingview.com", transport=httpx.MockTransport(handler))
    assert latest_values(["TVC:FR10Y", "TVC:DE10Y"], "cfd", client) == {"TVC:FR10Y": 3.48}

    from app.cli import main

    page = tmp_path / "bofip.html"
    page.write_text("<table><tr><td>ORANGE</td><td>FR0000133308</td></tr></table>", encoding="utf-8")
    out = tmp_path / "ttf.yaml"
    assert main(["ttf-list-load", "--year", "2026", "--file", str(page), "--out", str(out)]) == 0
    assert ttf_status("FR0000133308", 3e10, 1e9, 2026, out)[0]
