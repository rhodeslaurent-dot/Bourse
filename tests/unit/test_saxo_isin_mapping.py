"""ADR-006: Saxo OpenAPI returns no ISIN (licensing). Positions are mapped by ``Uic`` + ``Symbol``
(``VK:xpar`` → ticker + MIC) through the ``instruments`` referential or ``config/instruments_map.yaml``;
anything unmapped is ``isin=None`` with a reason — never a guess."""

from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select

from app.data.providers.saxo import LIVE_BASE, NO_VENUE, SaxoClient, SaxoPortfolio, parse_saxo_symbol
from app.db.models import Instrument, InstrumentAlias
from app.db.session import db_session
from app.services import instruments as svc


@pytest.mark.parametrize(
    "symbol, expected",
    [
        ("VK:xpar", ("VK", "XPAR")),
        ("alvio:XPAR", ("ALVIO", "XPAR")),
        ("ASML:xams", ("ASML", "XAMS")),
        ("LOCK-12806:xxxx", None),
        ("TTE", None),
        ("", None),
        (":xpar", None),
        ("VK:", None),
    ],
)
def test_parse_saxo_symbol(symbol, expected):
    assert parse_saxo_symbol(symbol) == expected


def test_resolver_uses_referential_and_persists_uic_alias(db_engine):
    with db_session() as s:
        s.add(Instrument(isin="FR0000125486", name="Vallourec", ticker_local="VK", mic="XPAR"))
        s.add(Instrument(isin="FR00XXXXXXXX", name="VK ailleurs", ticker_local="VK", mic="XAMS"))
    assert svc.resolve_broker_isin("saxo", "117274", "VK:xpar") == "FR0000125486"
    with db_session() as s:
        alias = s.scalar(select(InstrumentAlias).where(InstrumentAlias.provider == "saxo_uic"))
        assert alias is not None and alias.alias == "117274" and alias.isin == "FR0000125486"
        assert s.get(Instrument, "FR0000125486").ticker_saxo == "VK:xpar"
        # second call hits the persisted alias even if the symbol changed at the broker
    assert svc.resolve_broker_isin("saxo", "117274", "VK-NEW:xpar") == "FR0000125486"


def test_resolver_refuses_ambiguity_and_unknown(db_engine, monkeypatch, tmp_path):
    monkeypatch.setenv("INSTRUMENTS_MAP_PATH", str(tmp_path / "absent.yaml"))
    svc._load.cache_clear()
    with db_session() as s:
        s.add(Instrument(isin="FR0000000001", name="A", ticker_local="DUP", mic="XPAR"))
        s.add(Instrument(isin="FR0000000002", name="B", ticker_local="DUP", mic="XPAR"))
    assert svc.resolve_broker_isin("saxo", "1", "DUP:xpar") is None  # two candidates → never a guess
    assert svc.resolve_broker_isin("saxo", "2", "NOPE:xpar") is None
    assert svc.resolve_broker_isin("saxo", "3", f"LOCK-12806:{NO_VENUE}") is None
    with db_session() as s:
        assert s.scalar(select(InstrumentAlias)) is None


def test_resolver_falls_back_to_yaml_map(db_engine, monkeypatch, tmp_path):
    path = tmp_path / "instruments_map.yaml"
    path.write_text(
        "instruments:\n"
        "  FR0000120271: {name: TotalEnergies, eodhd: TTE.PA, mic: XPAR}\n"
        "  FR0000120073: {name: Air Liquide, eodhd: AI.PA, mic: XPAR, saxo: 'AI:xpar'}\n"
        "  NL0010273215: {name: ASML, eodhd: ASML.AS, mic: XAMS, saxo_uic: 999}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INSTRUMENTS_MAP_PATH", str(path))
    svc._load.cache_clear()
    try:
        assert svc.resolve_broker_isin("saxo", "10", "AI:xpar") == "FR0000120073"  # explicit saxo key
        assert svc.resolve_broker_isin("saxo", "11", "TTE:xpar") == "FR0000120271"  # eodhd ticker + mic
        assert svc.resolve_broker_isin("saxo", "12", "TTE:xams") is None  # wrong venue
        assert svc.resolve_broker_isin("saxo", "999", "") == "NL0010273215"  # explicit saxo_uic, no symbol
    finally:
        svc._load.cache_clear()


@respx.mock
def test_portfolio_marks_unmapped_positions_with_reason():
    respx.get(f"{LIVE_BASE}/port/v1/positions").mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": [
                    {
                        "PositionId": "p1",
                        "PositionBase": {"Uic": 117274, "Amount": 10, "OpenPrice": 15.62, "AssetType": "Stock"},
                        "PositionView": {},
                        "DisplayAndFormat": {"Symbol": "VK:xpar", "Currency": "EUR"},
                    },
                    {
                        "PositionId": "p2",
                        "PositionBase": {"Uic": 54739499, "Amount": 8, "OpenPrice": 58.9, "AssetType": "Stock"},
                        "PositionView": {},
                        "DisplayAndFormat": {"Symbol": "LOCK-12806:xxxx", "Currency": "EUR"},
                    },
                    {
                        "PositionId": "p3",
                        "PositionBase": {"Uic": 35916664, "Amount": 335, "OpenPrice": 6.877, "AssetType": "Stock"},
                        "PositionView": {},
                        "DisplayAndFormat": {"Symbol": "ALVIO:xpar", "Currency": "EUR"},
                    },
                ]
            },
        )
    )
    seen: list[tuple[str, str, str]] = []

    def resolver(uic: str, symbol: str, asset_type: str) -> str | None:
        seen.append((uic, symbol, asset_type))
        return "FR0000125486" if symbol == "VK:xpar" else None

    client = SaxoClient(client=httpx.Client(), access_token="tok")
    pf = SaxoPortfolio(client, "ck", isin_resolver=resolver)
    pos = pf.positions()
    assert [p.isin for p in pos] == ["FR0000125486", None, None]
    assert seen == [("117274", "VK:xpar", "Stock"), ("35916664", "ALVIO:xpar", "Stock")]  # no call for xxxx
    assert "radié ou bloqué" in pf.isin_errors["54739499"]
    assert "ALVIO (XPAR) absent du référentiel" in pf.isin_errors["35916664"]
    assert "saxo: ALVIO:xpar" in pf.isin_errors["35916664"]
    assert all(m == "GET" for m, _ in client.calls) and not any("instruments/details" in p for _, p in client.calls)
