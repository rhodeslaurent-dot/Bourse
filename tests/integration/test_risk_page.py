"""docs/28 — /risque: recommended level with its explanation, override of the day (with a rescan),
reference risk set in the interface and used by the engine."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.db.models import Proposal, RiskDialDaily, RiskSetting
from app.db.session import db_session
from app.services import engine
from app.services.risk_level import reference
from tests.integration.test_engine import DAY, ISIN, NOW, _seed, breakout_bars


def test_page_override_and_reference(db_engine, config, calendars):
    from app.main import create_app

    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        html = c.get("/risque").text
        assert "Niveau de risque du jour" in html and "300 €" in html
        r = c.post("/risque/niveau", data={"level": "1,1", "reason": "beaucoup d'opportunités"}, follow_redirects=False)
        assert r.status_code == 303
        assert c.post("/risque/niveau", data={"level": "3"}).status_code == 400  # out of bounds
        r = c.post("/risque/reference", data={"capital": "50 000", "risk_pct": "0,8"}, follow_redirects=False)
        assert r.status_code == 303 and "400" in r.headers["location"]
        assert c.post("/risque/reference", data={"capital": "50000", "risk_pct": "5"}).status_code == 400
        html = c.get("/risque").text
        assert "modifié par vous" in html and "beaucoup d" in html
    with db_session() as s:
        assert s.query(RiskDialDaily).one().override == 1.1
        assert s.query(RiskSetting).count() == 1 and abs(reference(s, config).r_eur - 400) < 1e-9


def test_engine_uses_the_reference_and_the_override(db_engine, config):
    from app.services.risk_level import set_override, set_reference

    with db_session() as s:
        _seed(s, breakout_bars())
        set_reference(s, config, 40000, 0.005, "test", now=datetime(2026, 10, 1, tzinfo=UTC))  # R = 200 €
        set_override(s, config, DAY, 0.5, "prudence")
        engine.scan(s, config, DAY, now=NOW)
        props = s.query(Proposal).filter(Proposal.isin == ISIN).all()
        assert props
        for p in props:
            lv = p.details["niveau_risque"]
            assert lv["niveau_applique"] == 0.5 and lv["modifie_par_vous"] and lv["r_reference_eur"] == 200.0
            for row in (p.details.get("comptes") or {}).values():
                if row.get("risque_eur") is not None:
                    assert row["risque_eur"] <= 200 * 0.5 + 1e-6
        n_before = len(props)
        set_override(s, config, DAY, 1.0, "retour")
        created = engine.rescan_today(s, config, DAY, now=NOW)
        live = s.query(Proposal).filter(Proposal.isin == ISIN, Proposal.deleted_at.is_(None)).all()
        assert created == n_before and len(live) == n_before
        assert all(p.details["niveau_risque"]["niveau_applique"] == 1.0 for p in live)
