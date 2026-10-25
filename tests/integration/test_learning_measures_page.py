"""/apprentissage « Règles en mesure »: latest backtest segments, committee verdicts and radar labels render."""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from app.db.models import BacktestRun, WeakSignalHit
from app.db.session import db_session


def test_measures_section_renders_with_data(db_engine, config, calendars):
    from app.main import create_app

    seg = {"n": 42, "mean_r": 0.12, "median_r": 0.1, "hit_rate": 0.45, "t_stat": 1.4, "ci_low": -0.05,
           "ci_high": 0.29, "significant": False}  # fmt: skip
    with db_session() as s:
        s.add(BacktestRun(created_at=datetime.now(UTC), params_version="x", start=date(2018, 1, 1),
                          end=date(2026, 10, 23), instruments=784, summary={"segments": {
                              "production:d2_breakout": seg, "detecteur:d2_breakout": seg}, "essais": 12,
                              "portefeuille": {
                              "lignes_simultanees": 6, "note": "R réalisé seulement", "tout": {
                                  "sans_coupe_circuit": {"taken": 50, "total_r": 12.0, "r_per_year": 3.0,
                                                         "max_drawdown_r": -9.0},
                                  "avec_coupe_circuit": {"taken": 44, "total_r": 11.0, "r_per_year": 2.8,
                                                         "max_drawdown_r": -6.0},
                                  "verdict_garde_fou": "adopter"}}},
                          notes=["biais de survie : optimiste"]))  # fmt: skip
        s.add(WeakSignalHit(isin="FR0000120271", day=date.today(), label="attention_corroboree", text="t",
                            computed_at=datetime.now(UTC)))  # fmt: skip
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        html = c.get("/apprentissage").text
    assert "production:d2_breakout" in html and "detecteur:d2_breakout" not in html
    assert "Portefeuille rejoué" in html and "adopter" in html
    assert "+0.120" in html and "biais de survie" in html and "attention corroboree : 1" in html
