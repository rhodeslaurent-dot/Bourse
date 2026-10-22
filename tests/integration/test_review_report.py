"""Lot 2.5 — portfolio review and the 10-rubric report of the day."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.db.models import DailyReport, PortfolioReview, PositionRow
from app.db.session import db_session
from app.services.portfolio import ensure_accounts
from app.services.portfolio_review import run_review, telegram_summary
from app.services.report import RUBRICS, as_text, build, store, telegram_lines
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars


def _position(s, stop: float):
    acc = ensure_accounts(s)["pea"]
    s.add(PositionRow(account_id=acc.id, isin=ISIN, qty_held=40, qty_protected=40, avg_price=90.0,
                      execution_state="filled", protection_state="protected", stop_initial=85.0,
                      stop_current=stop, opened_at=NOW - timedelta(days=30), last_price=100.8))  # fmt: skip
    s.flush()


def test_review_lines_and_synthesis(db_engine, config):
    with db_session() as s:
        _seed(s, breakout_bars())
        _position(s, 88.0)
        rev = run_review(s, config, now=NOW)
        ln = rev.lines[0]
        assert ln["verdict"] in ("CONSERVER", "ALLÉGER") and ln["stop_propose"] >= 88.0  # never lowered
        syn = rev.synthesis
        assert syn["positions"] == 1 and syn["exposition"]["compte"]["PEA"] > 0
        assert syn["risque"]["plafond_eur"] > 0
        assert len(telegram_summary(rev)) <= 11


def test_crossed_stop_sells_and_pockets_excluded(db_engine, config):
    config.params.pockets["tax_harvest"] = [{"isin": ISIN, "target_date": "2026-11-27"}]
    try:
        with db_session() as s:
            _seed(s, breakout_bars())
            _position(s, 101.0)
            rev = run_review(s, config, now=NOW)
            assert rev.lines[0]["verdict"] == "CESSION FISCALE"
            assert rev.synthesis["risque"]["convention_b_eur"] == 0  # tax-harvest line outside the caps
    finally:
        config.params.pockets["tax_harvest"] = []
    with db_session() as s:
        s.query(PortfolioReview).delete()
        rev = run_review(s, config, now=NOW)
        assert rev.lines[0]["verdict"] == "VENDRE" and rev.lines[0]["signaux"][0].startswith("S1")


def test_report_ten_rubrics_archived_and_page(db_engine, config, calendars):
    with db_session() as s:
        _seed(s, breakout_bars())
        _position(s, 88.0)
        c = build(s, config, DAY, NOW)
        assert [r["titre"] for r in c["rubriques"]] == RUBRICS
        assert "Rapport du" in as_text(c) and len(telegram_lines(c)) <= 11
        store(s, DAY, c)
        store(s, DAY, {"jour": "x", "rubriques": []})  # archived once, never corrected
        assert s.query(DailyReport).one().content["jour"] == DAY.isoformat()
    from app.main import create_app

    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as cl:
        html = cl.get(f"/rapport?day={DAY.isoformat()}").text
        assert "Ordres manuels potentiels" in html and "Analyse du portefeuille" in html
        r = cl.post("/revue/lancer", follow_redirects=True)
        assert r.status_code == 200 and "Revue de portefeuille" in r.text and "TotalEnergies" in r.text
    _ = datetime.now(UTC)
