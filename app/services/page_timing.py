"""18/11/2026 — « the Cloudflare link takes several minutes to load »: what is slow, measured, never guessed.

- every request slower than ``SLOW_REQUEST_S`` (5 s) is kept in ``data/slow_requests.jsonl`` (path, duration, code,
  time — never a parameter value), the last ``KEEP`` of them;
- the home page (« Revue du jour ») is built once with each of its parts timed;
- the jobs running at that moment and, on PostgreSQL, the queries running for more than 5 s are listed.

Read by ``sources-health --pages`` (an allowlisted command of the PC relay)."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

KEEP = 300


def slow_path() -> str:
    return os.environ.get("SLOW_REQUESTS_PATH", "data/slow_requests.jsonl")


def record_slow(method: str, path: str, seconds: float, status: int, now: datetime | None = None) -> None:
    """Append one slow request; the file is cut back to the last ``KEEP`` lines when it doubles. Never raises."""
    try:
        p = slow_path()
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        line = json.dumps({"at": (now or datetime.now(UTC)).isoformat(timespec="seconds"), "method": method,
                           "path": path, "s": round(seconds, 1), "code": status})  # fmt: skip
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        with open(p, encoding="utf-8") as fh:
            lines = fh.readlines()
        if len(lines) > 2 * KEEP:
            with open(p, "w", encoding="utf-8") as fh:
                fh.writelines(lines[-KEEP:])
    except OSError:
        pass


def recent_slow(n: int = 20) -> list[dict[str, Any]]:
    try:
        with open(slow_path(), encoding="utf-8") as fh:
            lines = fh.readlines()[-n:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


@contextmanager
def timed(targets: list[tuple[Any, str]], into: dict[str, list[float]]) -> Iterator[None]:
    """Wrap ``module.name`` for each target so each call's duration is added to ``into[name]``; restored after."""
    saved = []
    for mod, name in targets:
        fn = getattr(mod, name)
        saved.append((mod, name, fn))

        def wrap(*a: Any, _fn: Callable[..., Any] = fn, _n: str = name, **kw: Any) -> Any:
            t0 = time.monotonic()
            try:
                return _fn(*a, **kw)
            finally:
                into.setdefault(_n, []).append(time.monotonic() - t0)

        setattr(mod, name, wrap)
    try:
        yield
    finally:
        for mod, name, fn in saved:
            setattr(mod, name, fn)


def time_home(s: Session, config: Any, day: date) -> tuple[float, list[tuple[str, float, int]]]:
    """The home page built once: total seconds and, per part, (name, seconds, calls), slowest first."""
    from app.services import circuit, daily_review, idea_ai, insider_caution, macro, risk_view

    parts = ["_ideas", "split_unvalidated", "_market", "_portfolio", "_core_asks", "_experts", "_expert_state",
             "_cap", "_sources"]  # fmt: skip
    targets = [(daily_review, n) for n in parts] + [
        (circuit, "circuit_state"), (macro, "upcoming_events"), (risk_view, "build_risk_view"),
        (insider_caution, "caution_for"), (idea_ai, "latest_for"),
    ]  # fmt: skip
    into: dict[str, list[float]] = {}
    t0 = time.monotonic()
    with timed(targets, into):
        daily_review.build(s, config, day)
    total = time.monotonic() - t0
    rows = sorted(((n, sum(v), len(v)) for n, v in into.items()), key=lambda x: -x[1])
    return total, rows


def time_sources(s: Session, config: Any) -> tuple[float, list[tuple[str, float, int]]]:
    """The « Sources » page's parts, each timed once, slowest first (same calls as the page)."""
    from app.services import event_measure, source_health

    st = event_measure.settings(config)
    h = 5 if 5 in st.horizons else st.horizons[0]
    out: list[tuple[str, float, int]] = []
    t_all = time.monotonic()
    t0 = time.monotonic()
    day, _ = source_health.latest(s)
    out.append(("latest", time.monotonic() - t0, 1))
    t0 = time.monotonic()
    if day:
        source_health.history(s, day)
    out.append(("history", time.monotonic() - t0, 1))
    t0 = time.monotonic()
    event_measure.cached_scoreboard(s, h, 5)  # empty cache in this process: the full computation
    out.append(("scoreboard (1er calcul)", time.monotonic() - t0, 1))
    t0 = time.monotonic()
    event_measure.cached_scoreboard(s, h, 5)  # what the page's next visits cost
    out.append(("scoreboard (en mémoire)", time.monotonic() - t0, 1))
    t0 = time.monotonic()
    source_health.job_states(s, source_health.feeders())
    out.append(("job_states", time.monotonic() - t0, 1))
    return time.monotonic() - t_all, sorted(out, key=lambda x: -x[1])


def running_jobs(s: Session) -> list[tuple[str, datetime]]:
    from app.db.models import JobRun
    from app.domain.timeutil import from_store

    rows = s.execute(select(JobRun.job, JobRun.started_at).where(JobRun.status == "running")).all()
    return [(j, from_store(t)) for j, t in rows]


def long_queries(s: Session, min_seconds: int = 5) -> list[tuple[float, str, str]]:
    """PostgreSQL only: the statements running for more than ``min_seconds`` (other sessions), text cut short."""
    if s.get_bind().dialect.name != "postgresql":
        return []
    q = text(
        "select extract(epoch from now() - query_start), coalesce(state, ''), left(query, 140) "
        "from pg_stat_activity where pid <> pg_backend_pid() and state <> 'idle' "
        "and now() - query_start > make_interval(secs => :m) order by 1 desc limit 10"
    )
    return [(float(a), str(b), " ".join(str(c).split())) for a, b, c in s.execute(q, {"m": min_seconds})]
