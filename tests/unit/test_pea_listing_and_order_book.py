"""22/10/2026 review of /opportunites: AMD (US ISIN listed on Xetra) was proposed in the PEA, and the
order book listed JCDecaux twice and orders the scoring had sent to WATCH."""

from __future__ import annotations

from types import SimpleNamespace

from app.db.models import Instrument
from app.scheduler.jobs.universe_refresh import symbol_rows_from_eodhd
from app.services.engine import _account_options
from app.web.opportunities_routes import order_book


class Xetra:
    def exchange_symbols(self, exchange, types=None):
        return [
            {"Code": "AMD", "Name": "AMD", "Isin": "US0079031078", "Currency": "EUR", "Country": "Germany"},
            {"Code": "SAP", "Name": "SAP", "Isin": "DE0007164600", "Currency": "EUR", "Country": "Germany"},
        ]  # fmt: skip


def test_exchange_country_is_never_taken_for_the_issuer_domicile():
    rows = symbol_rows_from_eodhd(Xetra(), ["XETRA"], {})  # type: ignore[arg-type]
    assert [r.country for r in rows] == [None, None]


def test_pea_presumed_from_the_exchange_country_is_ignored_when_the_isin_disagrees(config):
    amd = Instrument(isin="US0079031078", name="AMD", mic="XETR", pea_eligible=True, pea_source="domicile")
    sap = Instrument(isin="DE0007164600", name="SAP", mic="XETR", pea_eligible=True, pea_source="domicile")
    assert "pea" not in _account_options(amd, config)
    assert "pea" in _account_options(sap, config)


def _item(pid, isin, rank, blocked, account="pea", titres=100, last=10.0):
    p = SimpleNamespace(
        id=pid, isin=isin, rank=rank, status="open", action="WATCH", stop=9.5, account_recommended=account,
        gates={"blocked": blocked},
        details={"admission": "refuse", "p_max": 10.0, "comptes": {account: {"titres": titres}}},
    )  # fmt: skip
    return {"p": p, "name": isin, "snapshot_price": {"last": last}}


def test_order_book_one_line_per_value_and_only_price_waits():
    wait = "WATCH : P_max 10 dépassé de 0.3% — limite posée en attente d'un repli"
    rows = order_book(
        [
            _item(1, "FR0000077919", None, [wait]),
            _item(2, "FR0000077919", None, [wait]),  # same value, second detector
            _item(3, "DE0007500001", 1, ["WATCH : ratio d'admission 0.62 < 0.80 (scoring)"]),
            _item(4, "FR0000050809", None, ["WATCH : stop trop large (10.0% sous l'entrée)", wait]),
        ]
    )
    assert [r["p"].id for r in rows] == [1] and rows[0]["titres"] == 100
