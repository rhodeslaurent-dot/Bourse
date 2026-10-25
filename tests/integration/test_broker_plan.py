"""The plan review reads the real PEA orders (or projects the engine's PEA proposals) and recommends."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db.models import Account, Proposal, Trade
from app.db.session import db_session
from app.services.broker_plan import analysis, current_fees


def test_current_formula_drives_the_pea_fees(config):
    fees = current_fees(config, pea=True)
    assert fees is not None and fees.known and abs(fees.commission(5000) - 25.0) < 1e-9


def test_review_on_real_orders(db_engine, config):
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    with db_session() as s:
        acc = Account(broker="boursobank", type="pea", label="PEA")
        s.add(acc)
        s.flush()
        for k in range(12):
            s.add(Trade(account_id=acc.id, isin="FR0000120271", side="buy" if k % 2 == 0 else "sell", qty=100,
                        price=50.0, ts=now - timedelta(days=5 * k), kind="confirmed", source="import"))  # fmt: skip
        s.flush()
        a = analysis(s, config, now)
        assert a["basis"] == "vos ordres réels sur le PEA" and a["typical_order"] == 5000
        c = a["comparison"]
        assert c.best == "trader" and c.costs["decouverte"] > c.costs["trader"]


def test_projection_on_proposals_before_any_history(db_engine, config):
    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    with db_session() as s:
        for k in range(6):
            pea = {"comptes": {"pea": {"montant_eur": 6000}}}
            s.add(Proposal(signal_id=k, isin="FR0000120271", action="BUY", account_recommended="pea",
                           created_at=now - timedelta(days=k), details=pea))  # fmt: skip
        s.flush()
        a = analysis(s, config, now)
        assert a["basis"].startswith("projection") and a["comparison"].orders == 6


def test_pages_show_the_plan(db_engine, config, calendars):
    from fastapi.testclient import TestClient

    from app.main import create_app

    now = datetime.now(UTC)
    with db_session() as s:
        s.add(Proposal(signal_id=1, isin="FR0000120271", action="BUY", account_recommended="pea", created_at=now,
                       details={"comptes": {"pea": {"montant_eur": 6000}}}))  # fmt: skip
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        perf, learn = c.get("/performance").text, c.get("/apprentissage").text
    for html in (perf, learn):
        assert "Formule de courtage BoursoBank" in html and "Préconisation" in html and "(actuelle)" in html
