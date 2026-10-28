"""docs/33 §33.3 — prospective shadow tracking of registration n° 2 on the European venues (and, with
``plan=PLAN_REG3``, of registration n° 3's confirmatory cells C1 / C2, docs/34 §34.5).

Each evening, the frozen cells of the plan (n° 2: C1, C2 momentum; X2 52-week-high proximity — never the placebo)
are evaluated on the day with the research engine itself, on the European groups (P0 / P1 / P2), and their
signals are stored. Nothing is shown, alerted or traded: the series serves only a pre-declared analysis (the
suspension rule of an eventual production ADR). The lockouts are outcome-free: a monthly cell skips the
values it selected in its previous ``hold − 1`` months; an event cell skips a value signalled within its
horizon (calendar days ≈ sessions × 7/5)."""

from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import LoadedConfig
from app.db.models import ShadowSignal

log = logging.getLogger("bourse.shadow")

PREREG_DIR = Path(__file__).resolve().parents[2] / "research" / "prereg"
PLAN = PREREG_DIR / "2026-10-28_reg2.yaml"
SHADOW_CELLS = ("C1", "C2", "X2.t03.gatet13.h21")  # the placebo X1 is not tracked
# registration n° 3 (docs/34 §34.5): its two confirmatory cells (t14 after an earnings announcement, h 10 / 20)
PLAN_REG3 = PREREG_DIR / "2026-10-28_reg3.yaml"
SHADOW_CELLS_BY_PLAN = {PLAN.name: SHADOW_CELLS, PLAN_REG3.name: ("C1", "C2")}


def shadow_cells(plan: Path) -> tuple[str, ...]:
    """Cells tracked for a plan: the declared list, else its confirmatory cells (never a placebo)."""
    if plan.name in SHADOW_CELLS_BY_PLAN:
        return SHADOW_CELLS_BY_PLAN[plan.name]
    from app.services.research import load_prereg

    return tuple(c.id for c in load_prereg(plan).confirmatory)


def _is_month_end(config: LoadedConfig, calendars: Any, group: str, day: date) -> bool:
    u = config.params.universe or {}
    codes = u.get({"P0": "markets_p0", "P1": "markets_p1", "P2": "markets_p2"}.get(group, "")) or []
    for code in codes:
        mic = calendars.eodhd_to_mic.get(code)
        if mic and mic in calendars.venues:
            try:
                return bool(calendars.get(mic).next_session(day).month != day.month)
            except ValueError:  # calendar year unknown: never guessed
                return False
    return False


def _recent(s: Session, plan_id: str, cell: str, since: date) -> set[str]:
    return set(
        s.scalars(
            select(ShadowSignal.isin).where(
                ShadowSignal.plan_id == plan_id, ShadowSignal.cell == cell, ShadowSignal.day >= since
            )
        )
    )


def record_shadow_signals(
    s: Session, config: LoadedConfig, calendars: Any, today: date, plan: Path = PLAN
) -> dict[str, int]:
    """Signals of the tracked cells on the last official session of each European group (the official closes
    of day D are loaded the next morning by eod_backfill_check). A session already recorded is skipped; a
    group whose last session is more than 5 days old is skipped (data late, never guessed). Returns cell → n."""
    from app.domain.research.pipeline import cell_signals, prepare_group
    from app.services.research import load_panels, load_prereg, resolve_settings

    prereg = load_prereg(plan)
    tracked = shadow_cells(Path(plan))
    cells = [c for c in prereg.cells if c.id in tracked]
    st, _ = resolve_settings(config, prereg, "all")
    start = today - timedelta(days=700)  # 252 sessions of warm-up and the 12-month momentum, with a margin
    panels, _ = load_panels(s, config, calendars, prereg, start, today, "all", None, None, default_groups=True)
    out: dict[str, int] = {c.id: 0 for c in cells}
    now = datetime.now(UTC)
    for k, p in enumerate(panels):
        if not p.days.size:
            continue
        day = date.fromisoformat(str(p.days[-1])[:10])
        if day < today - timedelta(days=5):
            log.info("ombre : groupe %s, dernière séance %s trop ancienne", p.group, day)
            continue
        g = prepare_group(k, p, st)
        t = int(p.days.shape[0]) - 1
        # the research engine never knows the very last row to be a month end (no next row); live, the calendar
        # of the group's first venue says it (the next session falls in another month)
        g.f.month_end[t] = _is_month_end(config, calendars, p.group, day)
        for c in cells:
            done = s.scalar(
                select(ShadowSignal.id).where(
                    ShadowSignal.plan_id == prereg.id, ShadowSignal.cell == c.id, ShadowSignal.day == day,
                    ShadowSignal.venue_group == p.group,
                )
            )  # fmt: skip
            if done is not None:
                continue
            sig = cell_signals(g, c, st, (t, t))
            if not sig.cols.size:
                continue
            isins = [p.isins[int(i)] for i in sig.cols]
            if c.mode != "event":  # monthly: top N by score, the values still held skipped
                hold_months = max(1, math.ceil(c.h / 21))
                held = (
                    _recent(s, prereg.id, c.id, day - timedelta(days=31 * (hold_months - 1) + 5))
                    if hold_months > 1
                    else set()
                )
                score = sig.score if sig.score is not None else np.zeros(len(isins))
                order = np.argsort(-np.nan_to_num(score, nan=-1.0), kind="stable")
                isins = [isins[int(i)] for i in order if isins[int(i)] not in held][: st.top_n]
            else:
                recent = _recent(s, prereg.id, c.id, day - timedelta(days=int(c.h * 7 / 5) + 1))
                isins = [x for x in isins if x not in recent]
            for isin in isins:
                s.add(ShadowSignal(plan_id=prereg.id, cell=c.id, day=day, isin=isin, venue_group=p.group,
                                   created_at=now))  # fmt: skip
                out[c.id] += 1
        s.flush()
    return out
