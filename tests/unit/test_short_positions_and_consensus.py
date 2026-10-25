"""docs/23 S2 (net short positions published by the authorities) and S4 (TradingView analyst columns,
cross-checked with Yahoo)."""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime

import httpx2 as httpx
import pytest

from app.data.base import ProviderError
from app.data.providers.short_positions import fetch, read_table, rows_from_table

AMF_CSV = (
    "Détenteur de la position courte nette;Emetteur / issuer;code ISIN;Position courte nette (%);"
    "Date de début de position\n"
    "Fonds A;SOCIETE X;FR0000120271;0,55;15/10/2026\n"
    "Fonds B;SOCIETE X;FR0000120271;1,20;16/10/2026\n"
    "Fonds A;SOCIETE X;FR0000120271;0,45;20/10/2026\n"  # below 0.5 %: no longer counts
    "Fonds C;SOCIETE Y;PAS-UN-ISIN;0,70;16/10/2026\n"
).encode()


def test_french_csv_is_read_by_its_header_words():
    rows = rows_from_table(read_table(AMF_CSV, "amf.csv"))
    assert len(rows) == 3 and rows[0].holder == "Fonds A" and rows[0].issuer == "SOCIETE X"
    assert rows[1].pct == pytest.approx(0.012) and rows[1].position_date == date(2026, 10, 16)


def test_german_csv_and_swedish_excel_and_a_refused_file():
    de = "Positionsinhaber,Emittent,ISIN,Position,Datum\nHedge Z,SAP SE,DE0007164600,0.62,2026-10-20\n"
    rows = rows_from_table(read_table(de.encode(), "bafin.csv"))
    assert rows[0].pct == pytest.approx(0.0062) and rows[0].holder == "Hedge Z"
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Finansinspektionen — blankningsregister"])  # title row above the header
    ws.append(["Innehavare av position", "Emittent", "ISIN", "Position i procent", "Positionsdatum"])
    ws.append(["Fond Q", "Volvo AB", "SE0000115446", 0.81, datetime(2026, 10, 21)])
    buf = io.BytesIO()
    wb.save(buf)
    se = rows_from_table(read_table(buf.getvalue(), "fi.xlsx"))
    assert (
        se[0].isin == "SE0000115446"
        and se[0].pct == pytest.approx(0.0081)
        and se[0].position_date == date(2026, 10, 21)
    )
    with pytest.raises(ProviderError, match="colonnes non reconnues"):
        rows_from_table(read_table(b"a;b;c\n1;2;3\n"))


def test_amf_address_is_read_from_the_data_gouv_catalogue():
    def handler(request: httpx.Request) -> httpx.Response:
        if "api/1/datasets" in str(request.url):
            return httpx.Response(200, json={"data": [
                {"title": "Autre jeu", "resources": [{"format": "csv", "url": "https://x/other.csv"}]},
                {"title": "Positions courtes nettes sur actions", "resources": [
                    {"format": "pdf", "url": "https://x/doc.pdf", "last_modified": "2026-10-22"},
                    {"format": "csv", "url": "https://x/old.csv", "last_modified": "2026-09-01"},
                    {"format": "csv", "url": "https://x/last.csv", "last_modified": "2026-10-22"}]}]})  # fmt: skip
        assert str(request.url) == "https://x/last.csv"
        return httpx.Response(200, content=AMF_CSV)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rows, url = fetch({"authority": "amf", "url": "datagouv:positions courtes nettes"}, client)
    assert url == "https://x/last.csv" and len(rows) == 3
    with pytest.raises(ProviderError, match="à renseigner"):
        fetch({"authority": "bafin", "url": "à renseigner"}, client)


def test_declared_total_keeps_each_holders_latest_position_and_penalises_block_r(db_engine):
    from app.db.session import db_session
    from app.domain.scoring import ScoreInput, score
    from app.services.short_positions import short_interest, store

    rows = rows_from_table(read_table(AMF_CSV))
    with db_session() as s:
        assert store(s, "amf", rows, "https://x/last.csv") == 3
        assert store(s, "amf", rows, "https://x/last.csv") == 0  # idempotent
        si = short_interest(s, "FR0000120271", date(2026, 10, 23))
    assert si.total_pct == pytest.approx(0.012) and [h["detenteur"] for h in si.holders] == ["Fonds B"]
    assert si.latest_date == date(2026, 10, 20)
    base = dict(rs_rank=85, above_mm50_mm200=True, mm50_above_mm200=True, above_rising_mm20=True, rvol=1.0,
                clean_trigger=True, over_extended=False, resistance_within_3pct=False, stop_distance_adr=1.0,
                earnings_within_10_days=False, adv_eur=5e6, market_cap_eur=5e9, gap_pct=0.0, non_eur=False,
                regime="vert")  # fmt: skip
    low = score(ScoreInput(**base, short_interest_pct=0.012), ["T", "R", "M"], 5)
    high = score(ScoreInput(**base, short_interest_pct=0.031), ["T", "R", "M"], 5)
    assert low.blocks["R"] - high.blocks["R"] == 3 and "positions courtes déclarées 3.1% ≥ 2% → −3" in high.explanations


def test_tradingview_columns_are_checked_one_by_one():
    from app.data.providers.tradingview_screener import analyst_values, check_columns

    def handler(request: httpx.Request) -> httpx.Response:
        col = json.loads(request.content)["columns"][1]
        if col == "nope":
            return httpx.Response(400, json={"error": "Unknown field"})
        return httpx.Response(200, json={"data": [{"s": "EURONEXT:MC", "d": ["MC", 812.5]}]})

    res = check_columns(
        "france",
        ["price_target_average", "nope"],
        httpx.Client(base_url="https://t", transport=httpx.MockTransport(handler)),
    )  # noqa: E501
    assert res["price_target_average"] == (True, "MC=812.5") and res["nope"][0] is False and "400" in res["nope"][1]
    assert analyst_values({"pt": 812.5, "n": "x"}, {"objectif_moyen": "pt", "nb_analystes": "n"}) == {
        "objectif_moyen": 812.5
    }


def test_tradingview_fills_a_missing_yahoo_target_and_divergent_sources_are_not_used(db_engine):
    from app.db.models import AnalystConsensus, Fundamentals, Instrument
    from app.db.session import db_session
    from app.scheduler.jobs.universe_refresh import store_analyst_consensus
    from app.services.fundamentals import analyst_target, facts
    from app.services.universe import SymbolRow

    now = datetime.now(UTC)
    with db_session() as s:
        for isin in ("FR0000121014", "FR0000120271"):
            s.add(Instrument(isin=isin, name=isin, mic="XPAR", currency="EUR", in_universe=True))
        r1 = SymbolRow(isin="FR0000121014", name="LVMH", code="MC", exchange="PA", currency="EUR")
        r1.extra["analystes_tv"] = {"objectif_moyen": 800.0, "nb_analystes": 25.0}
        r2 = SymbolRow(isin="FR0000120271", name="TTE", code="TTE", exchange="PA", currency="EUR")
        r2.extra["analystes_tv"] = {"objectif_moyen": 80.0, "nb_analystes": 20.0}
        assert store_analyst_consensus(s, [r1, r2], date(2026, 10, 23)) == 2
        yahoo = {"targetMeanPrice": 62.0, "numberOfAnalystOpinions": 21, "currency": "EUR"}
        s.add(Fundamentals(isin="FR0000120271", day=date(2026, 10, 23), source="yahoo", symbol="TTE.PA",
                           received_at=now, data=yahoo))  # fmt: skip
        s.flush()
        _, lv = facts(s, s.get(Instrument, "FR0000121014"))
        _, tt = facts(s, s.get(Instrument, "FR0000120271"))
        assert s.query(AnalystConsensus).count() == 2
    assert lv["source"] == "tradingview" and lv["objectif_analystes"] == 800.0 and analyst_target(lv, 3, "EUR") == 800.0
    assert tt["ecart_sources_pct"] == pytest.approx(80 / 62 - 1, abs=1e-4) and tt["sources_divergentes"] is True
    assert analyst_target(tt, 3, "EUR") is None  # 29 % apart: neither target is retained


def test_tradingview_target_with_accounts_in_another_currency_is_shown_not_used(db_engine):
    """25/10/2026: TotalEnergies' TradingView target (88.37) may be in USD, its accounts' currency."""
    from app.db.models import AnalystConsensus, Instrument
    from app.db.session import db_session
    from app.services.fundamentals import analyst_target, facts

    with db_session() as s:
        s.add(Instrument(isin="FR0000120271", name="TTE", mic="XPAR", currency="EUR", in_universe=True))
        data = {"objectif_moyen": 88.37, "nb_analystes": 26, "devise": "EUR", "devise_comptes": "USD"}
        s.add(AnalystConsensus(isin="FR0000120271", day=date(2026, 10, 26), source="tradingview",
                               received_at=datetime.now(UTC), data=data))  # fmt: skip
        s.flush()
        _, shown = facts(s, s.get(Instrument, "FR0000120271"))
    assert shown["tradingview"]["devise_incertaine"] is True and shown.get("objectif_analystes") is None
    assert analyst_target(shown, 3, "EUR") is None
