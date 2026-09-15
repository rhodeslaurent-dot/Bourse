"""Regression tests for the phase 0–1 adversarial review (docs/ADR/005-revue-adversariale-phase1.md)."""

from datetime import UTC, date, datetime, timedelta  # noqa: I001

import httpx
import pytest
import respx
from sqlalchemy import select

from app.data.dto import DataStatus, MarketPerimeter, PriceType, Quote
from app.data.providers.boursobank_csv import parse_operations
from app.data.providers.saxo import LIVE_BASE, ReadOnlyViolation, SaxoClient, SaxoPortfolio, _check_read_only
from app.db.models import JobRun, PositionRow, Trade
from app.db.session import db_session
from app.domain.orders import Execution, MatchTolerance, Side, TradeKind
from app.notify.base import Notifier
from app.scheduler.jobs.portfolio_sync import STATUS_MAP, sync_saxo
from app.scheduler.jobs.position_monitor import monitor_once
from app.scheduler.runner import JobContext
from app.services.alerts import AlertService
from app.services.portfolio import declare_execution, declare_stop, ensure_accounts, import_confirmed_fills
from app.services.market_data import upsert_fx
from app.services.risk_view import build_risk_view

T = datetime(2026, 9, 15, 7, 30, tzinfo=UTC)


def _ctx(config, calendars, mode="reunion"):
    sent: list = []
    c = JobContext(config, calendars, T.date(), ("XPAR",), "normal")
    c.alerts = AlertService(Notifier(telegram_send=lambda e: sent.append(e)), 15)
    c.mode = mode
    c.sent = sent  # type: ignore[attr-defined]
    return c


def _q(sym, last, at):
    return Quote(
        source="t",
        market_perimeter=MarketPerimeter.PRIMARY,
        received_at=at,
        processed_at=at,
        data_status=DataStatus.REALTIME,
        symbol=sym,
        market_timestamp=at,
        price_type=PriceType.LAST,
        last=last,
    )


# --- C1 / M10 / M11 -------------------------------------------------------------------------


def test_c1_declared_and_import_out_of_tolerance_not_counted_twice(db_engine):
    with db_session() as s:
        acc = ensure_accounts(s)["cto_cash"]
        r = declare_execution(s, acc.id, "FR0000120271", "buy", 100, 42.40, T, "telegram")
        fill = Execution(
            acc.id,
            "FR0000120271",
            Side.BUY,
            100,
            43.00,
            T + timedelta(minutes=30),
            TradeKind.CONFIRMED,
            broker_fill_id="f-out",
            source="saxo_api",
        )  # +1.4 %, +30 min
        rep = import_confirmed_fills(s, [fill], MatchTolerance(0.005, 10))
        assert rep.matched == 0 and rep.suspected_duplicates == 1
        pos = s.get(PositionRow, r.position_id)
        assert pos.qty_held == 100  # never 200
        decl = s.get(Trade, r.trade_id)
        assert "superseded?" in decl.tags
        conf = s.scalar(select(Trade).where(Trade.broker_fill_id == "f-out"))
        assert conf.tags == ["suspected_duplicate?"]


def test_m10_double_click_across_minute_boundary(db_engine):
    with db_session() as s:
        acc = ensure_accounts(s)["pea"]
        t1 = datetime(2026, 9, 15, 7, 30, 59, tzinfo=UTC)
        r1 = declare_execution(s, acc.id, "FR0000120073", "buy", 12, 171.45, t1, "web")
        r2 = declare_execution(s, acc.id, "FR0000120073", "buy", 12, 171.45, t1 + timedelta(seconds=3), "web")
        assert r1.created and not r2.created and r1.trade_id == r2.trade_id
        assert s.get(PositionRow, r1.position_id).qty_held == 12


def test_m11_sale_without_position_is_kept_unassigned(db_engine):
    with db_session() as s:
        acc = ensure_accounts(s)["pea"]
        sale = Execution(
            acc.id,
            "FR0000120578",
            Side.SELL,
            20,
            98.1,
            T,
            TradeKind.CONFIRMED,
            broker_fill_id="bourso-OP-111",
            source="csv_boursobank",
        )
        rep = import_confirmed_fills(s, [sale], MatchTolerance())
        assert rep.unassigned_sales == 1
        row = s.scalar(select(Trade).where(Trade.broker_fill_id == "bourso-OP-111"))
        assert row is not None and row.position_id is None and row.tags == ["sans_position?"]
        assert import_confirmed_fills(s, [sale], MatchTolerance()).duplicates_ignored == 1


# --- C2 ---------------------------------------------------------------------------------------


def test_c2_boursobank_dates_are_paris_local():
    csv = "Date;Opération;Libellé;ISIN;Quantité;Prix\n12/09/2026 09:31:07;Achat;X;FR0000120271;1;1,0\n15/12/2026 09:31:07;Achat;X;FR0000120271;1;1,0\n"  # noqa: E501
    ops = parse_operations(csv)
    assert ops[0].date == datetime(2026, 9, 12, 7, 31, 7, tzinfo=UTC)
    assert ops[1].date == datetime(2026, 12, 15, 8, 31, 7, tzinfo=UTC)


# --- C3 / M8 -----------------------------------------------------------------------------------


@respx.mock
def test_c3_saxo_closed_positions_become_sales_and_missing_positions_alert(db_engine, config, calendars):
    respx.get(f"{LIVE_BASE}/port/v1/positions").mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": [
                    {
                        "PositionId": "p1",
                        "PositionBase": {
                            "Uic": 211,
                            "Amount": 100,
                            "OpenPrice": 42.40,
                            "ExecutionTimeOpen": "2026-09-14T07:33:10Z",
                            "AssetType": "Stock",
                        },
                        "PositionView": {},
                        "DisplayAndFormat": {"Currency": "EUR"},
                    },
                ]
            },
        )
    )
    respx.get(f"{LIVE_BASE}/port/v1/orders").mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": [
                    {
                        "OrderId": "o1",
                        "Uic": 211,
                        "BuySell": "Sell",
                        "Amount": 100,
                        "FilledAmount": 0,
                        "OpenOrderType": "Stop",
                        "Price": 40.6,
                        "Status": "Parked",
                    },
                ]
            },
        )
    )
    respx.get(f"{LIVE_BASE}/port/v1/balances").mock(return_value=httpx.Response(200, json={"CashBalance": 1000}))
    respx.get(f"{LIVE_BASE}/port/v1/closedpositions").mock(return_value=httpx.Response(200, json={"Data": []}))
    respx.get(f"{LIVE_BASE}/ref/v1/instruments/details/211/Stock").mock(
        return_value=httpx.Response(200, json={"Isin": "FR0000120271"})
    )
    respx.get(f"{LIVE_BASE}/ref/v1/instruments/details/999/Stock").mock(
        return_value=httpx.Response(200, json={"Isin": "NL0010273215"})
    )
    client = SaxoClient(client=httpx.Client(), access_token="tok")
    ctx = _ctx(config, calendars)
    with db_session() as s:
        acc = ensure_accounts(s)["cto_cash"]
        declare_execution(
            s, acc.id, "NL0010273215", "buy", 3, 640.0, T, "web"
        )  # held in the tool, absent at the broker
    d1 = sync_saxo(SaxoPortfolio(client, "ck"), MatchTolerance(), alerts=ctx.alerts, mode="reunion")
    assert d1["missing_at_broker"] == ["NL0010273215"] and any(e.kind == "broker_discrepancy" for e in ctx.sent)
    with db_session() as s:
        pos = s.scalar(select(PositionRow).where(PositionRow.isin == "FR0000120271"))
        assert pos.protection_state == "protected"  # "Parked" mapped to active (M8), no crash
    # day 2: the position was sold at the broker
    respx.get(f"{LIVE_BASE}/port/v1/positions").mock(return_value=httpx.Response(200, json={"Data": []}))
    respx.get(f"{LIVE_BASE}/port/v1/orders").mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": [
                    {
                        "OrderId": "o1",
                        "Uic": 211,
                        "BuySell": "Sell",
                        "Amount": 100,
                        "FilledAmount": 100,
                        "OpenOrderType": "Stop",
                        "Price": 40.6,
                        "Status": "Filled",
                    },
                ]
            },
        )
    )
    respx.get(f"{LIVE_BASE}/port/v1/closedpositions").mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": [
                    {
                        "ClosedPosition": {
                            "Uic": 211,
                            "Amount": 100,
                            "ClosingPrice": 40.55,
                            "ExecutionTimeClose": "2026-09-15T09:10:00Z",
                            "OpeningPositionId": "p1",
                            "ClosingPositionId": "c1",
                            "AssetType": "Stock",
                        },
                        "DisplayAndFormat": {"Currency": "EUR"},
                    },
                ]
            },
        )
    )
    d2 = sync_saxo(SaxoPortfolio(client, "ck"), MatchTolerance(), alerts=ctx.alerts, mode="reunion")
    assert d2["closed_fills"] == 1
    with db_session() as s:
        pos = s.scalar(select(PositionRow).where(PositionRow.isin == "FR0000120271"))
        assert pos.qty_held == 0 and pos.closed_at is not None and pos.execution_state == "closed"
    assert STATUS_MAP["notworking"] == "rejected" and STATUS_MAP["filled"] == "executed"


def test_m8_rejected_stop_unprotects_and_alerts(db_engine, config, calendars):
    ctx = _ctx(config, calendars, mode="absent")
    with db_session() as s:
        acc = ensure_accounts(s)["cto_cash"]
        r = declare_execution(s, acc.id, "FR0000120271", "buy", 10, 42.4, T, "web")
        declare_stop(s, r.position_id, 40.6, "seuil", 10, "initial", "web", broker_status="rejected")
        assert s.get(PositionRow, r.position_id).protection_state == "unprotected"
    assert monitor_once(ctx, {}, T) == 1 and ctx.sent[0].kind == "unprotected_fill"


# --- C4 / M12 / M13 -----------------------------------------------------------------------------


def test_c4_fx_used_and_unknown_fx_marks_incomplete(db_engine, config):
    with db_session() as s:
        accs = ensure_accounts(s)
        r = declare_execution(s, accs["pea"].id, "SE0000108656", "buy", 50, 200.0, T, "web", currency="SEK")
        pos = s.get(PositionRow, r.position_id)
        pos.last_price, pos.last_price_at, pos.last_price_status = 230.0, T, "delayed"
        assert pos.currency == "SEK"
        view = build_risk_view(s, config, T)
        assert view["incomplete"] and view["missing_fx"] == ["SE0000108656 (SEK)"] and view["risk"].positions_count == 0
        upsert_fx(s, "EURSEK", date(2026, 9, 15), 11.0, "test")
        view = build_risk_view(s, config, T)
        assert not view["missing_fx"] and view["risk"].positions_count == 1
        assert view["risk"].exposure_eur == pytest.approx(50 * 230 / 11.0) and view["risk"].fx_exposure_pct == 1.0


def test_m12_missing_price_excluded_never_estimated(db_engine, config):
    with db_session() as s:
        acc = ensure_accounts(s)["cto_cash"]
        declare_execution(s, acc.id, "FR0000120271", "buy", 100, 42.4, T, "web")
        view = build_risk_view(s, config, T)
        assert view["incomplete"] and view["missing_price"] == ["FR0000120271"]
        assert view["risk"].open_risk_current_eur == 0 and view["risk"].positions_count == 0
        assert [p.isin for p in view["unprotected"]] == ["FR0000120271"]  # still a P1 decision


def test_m13_reserved_risk_uses_proposal_stop_or_full_budget(db_engine, config):
    from app.services.decisions import create_proposal, record_signal
    from app.services.portfolio import declare_order

    with db_session() as s:
        acc = ensure_accounts(s)["cto_cash"]
        sig = record_signal(s, "FR0000120271", "d2", "test", T, "eod", "eod", "v")
        prop = create_proposal(s, sig, "BUY", None, None, "v", stop=39.50)
        declare_order(s, acc.id, "FR0000120271", "buy", 100, "plage", 42.90, 42.40, "day", "web", proposal_id=prop.id)
        view = build_risk_view(s, config, T)
        assert view["risk"].open_risk_reserved_eur == pytest.approx(100 * (42.90 - 39.50 * 0.998) + 3.432, abs=0.5)
        declare_order(s, acc.id, "NL0010273215", "buy", 3, "limite", 650.0, None, "day", "web")
        view = build_risk_view(s, config, T)
        assert view["reserved_unknown"] == ["NL0010273215"] and view["risk"].open_risk_reserved_eur >= 300


# --- C5 / M6 --------------------------------------------------------------------------------------


def test_m6_c5_whit_monday_xetra_closed_no_sell_proposal(db_engine, config, calendars):
    from app.db.models import Instrument

    assert not calendars.get("XETR").is_open(date(2026, 5, 25)) and calendars.get("XPAR").is_open(date(2026, 5, 25))
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)  # 14:00 Paris
    ctx = _ctx(config, calendars, mode="disponible")
    with db_session() as s:
        s.add(Instrument(isin="DE0007164600", name="SAP", mic="XETR"))
        s.add(Instrument(isin="FR0000120271", name="TTE", mic="XPAR"))
        acc = ensure_accounts(s)["cto_cash"]
        r1 = declare_execution(s, acc.id, "DE0007164600", "buy", 10, 200.0, now - timedelta(days=3), "web")
        r2 = declare_execution(s, acc.id, "FR0000120271", "buy", 10, 42.4, now - timedelta(days=3), "web")
        for pid, lvl in ((r1.position_id, 195.0), (r2.position_id, 40.6)):
            declare_stop(s, pid, lvl, "seuil", 10, "initial", "web")
            s.get(PositionRow, pid).crossed_since = now - timedelta(minutes=10)
    quotes = {"DE0007164600": _q("SAP.XETRA", 190.0, now), "FR0000120271": _q("TTE.PA", 40.0, now)}
    monitor_once(ctx, quotes, now)
    titles = {e.isin: e.title for e in ctx.sent if e.kind == "stop_threshold"}
    assert "SELL au marché" in titles["FR0000120271"] and "SELL au marché" not in titles["DE0007164600"]


# --- M16 / M17 / M18 ------------------------------------------------------------------------------


def test_m16_llm_levels_absent_from_letter_are_rejected(db_engine, config):
    from pathlib import Path

    from app.data.providers.imap_gmail import NewsletterMail
    from app.llm.gateway import ClaudeGateway, LlmSettings
    from app.llm.schemas import NewsletterExtraction, NewsletterValue
    from app.services.newsletters import _number_in_text, ingest_mail, session_day_for

    assert (
        _number_in_text(52.5, "stop à 52,50 €")
        and _number_in_text(60.0, "objectif de 60 €")
        and not _number_in_text(58.0, "objectif de 60 €")
    )

    def fake(model, system, user, schema, temperature, max_tokens):
        return (
            NewsletterExtraction(
                values=[
                    NewsletterValue(
                        name="TOTALENERGIES",
                        isin="FR0000120271",
                        direction="buy",
                        levels={"objectif": 60.0, "stop": 58.0},
                    )
                ]
            ),
            10,
            10,
        )

    gw = ClaudeGateway(
        LlmSettings(
            "claude-haiku-4-5",
            "x",
            0,
            15,
            5,
            {"claude-haiku-4-5": {"input": 1, "output": 5}},
            Path("app/llm/prompts"),
            False,
        ),
        call=fake,
    )
    mail = NewsletterMail(
        "zonebourse",
        "m1",
        "s",
        "x@zonebourse.com",
        T,
        T,
        "TOTALENERGIES (FR0000120271) : achat, objectif de 60 €.",
        "h1",
    )
    with db_session() as s:
        ingest_mail(s, mail, config, gw)
        from app.db.models import NewsletterItem

        item = s.scalar(select(NewsletterItem))
        levels = item.parsed["values"][0]["levels"]
        assert levels == {"objectif": 60.0} and item.parsed["rejected_llm_levels"] == ["TOTALENERGIES:stop=58.0"]
    from app.domain.calendar import MarketCalendars

    assert session_day_for(datetime(2026, 9, 12, 8, 0, tzinfo=UTC), None) == date(
        2026, 9, 12
    )  # Saturday without calendar → date
    cal = MarketCalendars.from_dict(__import__("yaml").safe_load(open("config/market_calendars.yaml")))
    assert session_day_for(datetime(2026, 9, 12, 8, 0, tzinfo=UTC), cal) == date(2026, 9, 14)
    assert session_day_for(datetime(2026, 1, 14, 23, 30, tzinfo=UTC), cal) == date(2026, 1, 15)  # 00:30 Paris


def test_m17_m18_llm_schema_and_unknown_model_price(db_engine):
    from pathlib import Path

    from app.llm.gateway import ClaudeGateway, LlmSettings, LlmUnavailable
    from app.llm.schemas import NewsClassification

    assert "surprise" not in NewsClassification.model_fields
    gw = ClaudeGateway(
        LlmSettings("claude-unknown", "x", 0, 15, 5, {}, Path("app/llm/prompts"), False), call=lambda *a: (None, 0, 0)
    )
    with db_session() as s, pytest.raises(LlmUnavailable):
        gw.parse(s, "x", "news_classify", "t", NewsClassification)


# --- M22 / m24 -----------------------------------------------------------------------------------


def test_m22_running_jobs_marked_interrupted_at_startup(config, calendars, db_engine, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    for var in ("CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    with db_session() as s:
        s.add(JobRun(job="eod_pipeline", started_at=T, status="running"))
    with TestClient(create_app(config=config, calendars=calendars, start_scheduler=False, start_telegram=False)):
        pass
    with db_session() as s:
        run = s.scalar(select(JobRun))
        assert run.status == "interrupted" and run.ended_at is not None


def test_m24_path_traversal_and_redaction():
    from app.logging_setup import redact

    with pytest.raises(ReadOnlyViolation):
        _check_read_only("POST", "/trade/v1/infoprices/subscriptions/../../../trade/v2/" + "orders")
    assert redact("wss://x?api_token=abcdef123 failed") == "wss://x?api_token=[redacted] failed"
    assert redact("Authorization: Bearer abc.def") == "Authorization: Bearer [redacted]"
