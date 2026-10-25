"""``backtest_weekly`` (Sunday 10:30 Paris): replays the detectors on the last 8 years of stored history at
full cost (docs/27); the latest run seeds the ranking's expected R while live history is insufficient."""

from __future__ import annotations

from datetime import date, timedelta

from app.db.session import db_session
from app.scheduler.runner import JobContext
from app.services.backtest import run_backtest


def run(ctx: JobContext) -> int:
    end = ctx.run_date - timedelta(days=1)
    years = int(((ctx.config.raw.get("backtest") or {}).get("years")) or 8)
    with db_session() as s:
        r = run_backtest(s, ctx.config, date(end.year - years, end.month, 1), end)
        prod = {
            k.split(":", 1)[1]: v
            for k, v in (r.summary or {}).get("segments", {}).items()
            if k.startswith("production:")
        }
        ctx.note = f"{r.instruments} valeurs, {(r.summary or {}).get('trades')} trades ; " + ", ".join(
            f"{d} {v['mean_r']:+.2f} R (n {v['n']})" for d, v in prod.items() if v.get("mean_r") is not None
        )
        return int((r.summary or {}).get("trades") or 0)
