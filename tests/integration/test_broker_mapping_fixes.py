"""18/11/2026 — two user reports: US lines bought at Saxo never appeared (Saxo « TICKER:xnas », the referential files
US values under XNYS), and a Saxo line had been tied to another company's ISIN. Fictional values only: no holding of
the user is ever written in the repository."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select

from app.db.models import Account, BrokerSyncRun, Instrument, InstrumentAlias, PositionRow, Trade
from app.db.session import db_session
from app.services import instruments as svc
from app.services.broker_remap import name_key, remap, suggest

WRONG, RIGHT = "FR0000000WR1", "FR0000000RI2"
NOW = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)


def test_a_nasdaq_symbol_at_saxo_finds_the_us_value_filed_under_xnys(db_engine, monkeypatch):
    monkeypatch.setattr(svc, "_load", lambda: {})
    with db_session() as s:
        s.add(Instrument(isin="US0000000AA1", name="Alpha Corp", ticker_local="AAAA", mic="XNYS", currency="USD"))
        s.add(Instrument(isin="US0000000BB2", name="Beta Inc", ticker_eodhd="BBBB.US", mic="XNYS", currency="USD"))
    assert svc.resolve_broker_isin("saxo", "1001", "AAAA:xnas") == "US0000000AA1"
    assert svc.resolve_broker_isin("saxo", "1002", "BBBB:xnas") == "US0000000BB2"  # by its EODHD ticker
    assert svc.resolve_broker_isin("saxo", "1003", "AAAA:xpar") is None  # another venue: never a guess
    with db_session() as s:
        assert s.scalar(select(InstrumentAlias.isin).where(InstrumentAlias.alias == "1001")) == "US0000000AA1"


def _wrong_tie(s) -> tuple[int, int]:
    s.add(Instrument(isin=WRONG, name="Gamma Vins SA", ticker_local="GAMV", mic="XPAR", ticker_saxo="ALXYZ:xpar"))
    s.add(Instrument(isin=RIGHT, name="Delta Therapeutics", ticker_local="ALXYZ", mic="XPAR"))
    saxo = Account(broker="saxo", type="cto_srd", label="Saxo")
    bourso = Account(broker="boursobank", type="pea", label="PEA")
    s.add_all([saxo, bourso])
    s.flush()
    s.add(InstrumentAlias(isin=WRONG, provider="saxo_uic", alias="9001"))
    s.add(PositionRow(account_id=saxo.id, isin=WRONG, qty_held=10, avg_price=5.0, opened_at=NOW))
    s.add(Trade(account_id=saxo.id, isin=WRONG, side="buy", qty=10, price=5.0, ts=NOW, kind="confirmed",
                broker_fill_id="F1", source="saxo"))  # fmt: skip
    s.add(PositionRow(account_id=bourso.id, isin=WRONG, qty_held=1, avg_price=7.0, opened_at=NOW))  # never touched
    s.flush()
    return saxo.id, bourso.id


def test_a_saxo_symbol_tied_to_the_wrong_isin_is_moved_after_a_dry_run(db_engine, monkeypatch):
    monkeypatch.setattr(svc, "_load", lambda: {})
    with db_session() as s:
        saxo_id, bourso_id = _wrong_tie(s)
        dry = remap(s, "ALXYZ:xpar", RIGHT)
        assert "rattaché à tort à Gamma Vins SA (FR0000000WR1) → Delta Therapeutics (FR0000000RI2)" in dry[0]
        assert "exécutions : 1" in dry[1] and "positions : 1" in dry[1] and "simulation" in dry[-1]
        assert s.scalar(select(PositionRow.isin).where(PositionRow.account_id == saxo_id)) == WRONG  # unchanged
        assert remap(s, "ALXYZ:xpar", RIGHT, apply=True)[-1].startswith("  corrigé")
        assert s.scalar(select(PositionRow.isin).where(PositionRow.account_id == saxo_id)) == RIGHT
        assert s.scalar(select(Trade.broker_fill_id).where(Trade.isin == RIGHT)) == "F1"  # reference kept
        assert s.scalar(select(PositionRow.isin).where(PositionRow.account_id == bourso_id)) == WRONG
        assert s.scalar(select(InstrumentAlias.isin).where(InstrumentAlias.alias == "9001")) == RIGHT
        assert s.get(Instrument, RIGHT).ticker_saxo == "ALXYZ:xpar" and s.get(Instrument, WRONG).ticker_saxo is None
        assert "absent du référentiel" in remap(s, "ALXYZ:xpar", "FR9999999999")[0]


def test_the_right_isin_is_found_from_the_name_saxo_gives_the_line(db_engine, monkeypatch):
    monkeypatch.setattr(svc, "_load", lambda: {})
    assert name_key("Acme Corp.") == name_key("ACME Corporation") == "acme"
    assert name_key("Société Générale SA") == "societe"
    with db_session() as s:
        _wrong_tie(s)
        right, why = suggest(s, "ALXYZ:xpar", "Delta Therapeutics", WRONG)
        assert right == RIGHT and "« Gamma Vins SA »" in why and "« Delta Therapeutics »" in why
        assert suggest(s, "ALXYZ:xpar", "Delta Therapeutics SA", RIGHT) == (None, "nom conforme")
        s.add(Instrument(isin="FR0000000DE3", name="Delta Industries", mic="XPAR"))
        s.flush()
        assert suggest(s, "ALXYZ:xpar", "Delta Therapeutics", WRONG)[0] is None  # two « delta »: never a guess


def test_an_unrecognised_saxo_line_is_asked_first_on_the_home_page(db_engine, config):
    from app.services import daily_review

    with db_session() as s:
        unmapped = ["AAAA:xnas (Uic 1001)", "LOCK-1:xxxx (Uic 2)"]
        s.add(BrokerSyncRun(broker="saxo", ts=NOW, status="ok", positions_count=2, diffs={"unmapped": unmapped}))
    with db_session() as s:
        asks = daily_review.build(s, config, date(2026, 11, 18), NOW)["attentes"]
    texts = [a["texte"] for a in asks if a["urgence"] == 1]
    assert any("Ligne Saxo non reconnue par l'outil : AAAA:xnas" in t for t in texts)
    assert not any("LOCK-1" in t for t in texts)  # a line Saxo itself blocks
