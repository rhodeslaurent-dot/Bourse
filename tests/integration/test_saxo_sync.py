"""Saxo read sync with recorded responses: positions → confirmed fills matched with declarations,
broker stop orders → protection, dated portfolio_state; still read-only."""

from datetime import UTC, datetime

import httpx
import respx
from sqlalchemy import select

from app.data.providers.saxo import LIVE_BASE, SaxoClient, SaxoPortfolio
from app.db.models import PortfolioState, PositionRow
from app.db.session import db_session
from app.domain.orders import MatchTolerance
from app.scheduler.jobs.portfolio_sync import sync_saxo
from app.services.portfolio import declare_execution, ensure_accounts

T = datetime(2026, 9, 15, 7, 30, tzinfo=UTC)


@respx.mock
def test_sync_saxo_matches_declaration_and_confirms_stop(db_engine):
    with db_session() as s:
        acc = ensure_accounts(s)["cto_cash"]
        r = declare_execution(s, acc.id, "FR0000120271", "buy", 100, 42.40, T, "telegram")
        pid = r.position_id
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
                            "OpenPrice": 42.45,
                            "ExecutionTimeOpen": "2026-09-15T07:33:10Z",
                            "AssetType": "Stock",
                            "AccountKey": "acc",
                        },
                        "PositionView": {"CurrentPrice": 43.1},
                        "DisplayAndFormat": {"Symbol": "TTE:xpar", "Currency": "EUR"},
                    },
                    {
                        "PositionId": "p2",
                        "PositionBase": {
                            "Uic": 999,
                            "Amount": 3,
                            "OpenPrice": 640.5,
                            "ExecutionTimeOpen": "2026-09-14T09:00:00Z",
                            "AssetType": "Stock",
                            "AccountKey": "acc",
                        },
                        "PositionView": {},
                        "DisplayAndFormat": {"Symbol": "ASML:xams", "Currency": "EUR"},
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
                        "OrderId": "o77",
                        "Uic": 211,
                        "BuySell": "Sell",
                        "Amount": 100,
                        "FilledAmount": 0,
                        "OpenOrderType": "Stop",
                        "Price": 40.6,
                        "Status": "Working",
                        "AccountKey": "acc",
                    },
                ]
            },
        )
    )
    respx.get(f"{LIVE_BASE}/port/v1/balances").mock(return_value=httpx.Response(200, json={"CashBalance": 4100.5}))
    respx.get(f"{LIVE_BASE}/ref/v1/instruments/details/211/Stock").mock(
        return_value=httpx.Response(200, json={"Isin": "FR0000120271"})
    )
    respx.get(f"{LIVE_BASE}/ref/v1/instruments/details/999/Stock").mock(
        return_value=httpx.Response(200, json={"Isin": "NL0010273215"})
    )
    client = SaxoClient(client=httpx.Client(), access_token="tok")
    diffs = sync_saxo(SaxoPortfolio(client, client_key="ck"), MatchTolerance(0.005, 10))
    assert diffs["matched"] == 1 and diffs["discretionary"] == 1 and diffs["stops_updated"] == 1
    assert all(m == "GET" for m, _ in client.calls)
    with db_session() as s:
        pos = s.get(PositionRow, pid)
        assert (
            pos.qty_held == 100
            and pos.protection_state == "protected"
            and pos.stop_current == 40.6
            and pos.avg_price == 42.45
        )
        asml = s.scalar(select(PositionRow).where(PositionRow.isin == "NL0010273215"))
        assert asml.qty_held == 3
        st = s.get(PortfolioState, pos.account_id)
        assert st.cash == 4100.5 and st.declarative is False and st.synced_at is not None
    diffs2 = sync_saxo(SaxoPortfolio(client, client_key="ck"), MatchTolerance(0.005, 10))
    assert diffs2["matched"] == 0 and diffs2["discretionary"] == 0  # idempotent
