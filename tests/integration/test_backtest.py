"""docs/27 — the backtest replays the production detectors on stored history at full cost."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

from app.db.models import BacktestRun, BacktestTrade, Instrument, PriceEod
from app.db.session import db_session
from app.services.backtest import prior_expected_r, run_backtest


def _series(isin, n, drift, seed, start=date(2023, 1, 2)):
    import random

    rng = random.Random(seed)
    c, out, d = 50.0, [], start
    for _ in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        o = c
        c = c * (1 + drift + rng.gauss(0, 0.018))
        hi, lo = max(o, c) * (1 + abs(rng.gauss(0, 0.006))), min(o, c) * (1 - abs(rng.gauss(0, 0.006)))
        out.append(PriceEod(isin=isin, day=d, open=o, high=hi, low=lo, close=c, adj_close=c,
                            volume=int(200000 * (1 + abs(rng.gauss(0, 0.8)))), source="eodhd",
                            received_at=datetime.now(UTC), data_status="eod"))  # fmt: skip
        d += timedelta(days=1)
    return out


def test_backtest_runs_on_history_and_reports_segments_biases_and_prior(db_engine, config):
    with db_session() as s:
        isins = [f"FR00000000{k:02d}" for k in range(12)]
        for k, isin in enumerate(isins):
            s.add(Instrument(isin=isin, name=f"V{k}", mic="XPAR", currency="EUR", in_universe=True, market_cap_eur=2e9))
            s.add_all(_series(isin, 700, 0.0012 if k % 2 == 0 else -0.0004, seed=k))
        s.flush()
        run = run_backtest(s, config, date(2024, 1, 1), date(2025, 9, 30))
        trades = s.query(BacktestTrade).filter(BacktestTrade.run_id == run.id).all()
        assert run.instruments == 12 and run.summary["trades"] == len(trades)
        assert any("biais de survie" in n for n in run.notes) and run.summary["essais"] >= 1
        for t in trades:  # every simulated trade respects the conservative rules
            assert t.signal_day < t.entry_day <= t.exit_day and t.status in ("admis", "refuse")
            assert math.isfinite(t.r_net) and t.r_net <= t.r_gross + 1e-9  # costs never improve a trade
            assert t.reason in ("target", "stop", "gap_stop", "time")
            assert t.mae_r <= 0.0 + 1e-9 or t.reason == "target"
        seg = run.summary["segments"]
        assert all(v["n"] >= 1 for v in seg.values())
        if any(k.startswith("production:") for k in seg):
            det = next(k for k in seg if k.startswith("production:")).split(":", 1)[1]
            r, n = prior_expected_r(s, det)
            assert n == seg[f"production:{det}"]["n"] and abs(r) <= abs(seg[f"production:{det}"]["mean_r"]) + 1e-9
        assert s.query(BacktestRun).count() == 1
