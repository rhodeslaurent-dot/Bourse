"""03/11/2026 — ``regime-check``: where the market regime is missing (daily rows per market, proposals' snapshots,
comparable history, latest backtest). Counts and dates only."""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.db.models import BacktestRun, BacktestTrade, MarketRegimeRow
from app.db.session import db_session
from app.services.regime_check import regime_check


def test_the_check_says_where_the_regime_is_missing(config, db_engine):
    from tests.integration.test_daily_review import _seed

    with db_session() as s:
        _seed()  # proposals created before any regime row: their snapshots carry no regime
        s.add(MarketRegimeRow(day=date(2026, 11, 2), market="XSTO", ts=datetime.now(UTC), regime="orange"))
        run = BacktestRun(created_at=datetime.now(UTC), params_version="t", start=date(2018, 1, 1),
                          end=date(2026, 10, 1), instruments=1)  # fmt: skip
        s.add(run)
        s.flush()
        for reg in (None, None, "haussier"):
            s.add(BacktestTrade(run_id=run.id, isin="X", detector="d7_trend_quality", signal_day=date(2020, 1, 2),
                                entry_day=date(2020, 1, 3), exit_day=date(2020, 1, 9), entry=1, exit=1, stop=0.9,
                                target=1.2, reason="temps", status="admis", regime=reg, r_gross=0, r_net=0, mfe_r=0,
                                mae_r=0, sessions=5))  # fmt: skip
        s.flush()
        txt = "\n".join(regime_check(s, datetime.now(UTC).date()))
    assert "XSTO   1 jour(s) du 2026-11-02 au 2026-11-02 · dernier : orange" in txt
    assert "inconnu" in txt.split("2. ")[1].split("3. ")[0]  # snapshots without regime (no EU row)
    assert "d7_trend_quality       inconnu    2" in txt and "d7_trend_quality       haussier   1" in txt
