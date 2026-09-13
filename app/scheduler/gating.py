"""Decide whether a scheduled job may run today (docs/02 §2.4). Pure functions.

- ``market_days_only: true`` → the job checks the calendar of the venue(s) it processes.
  A job with no instrument (``brief_premarket``) runs if at least one P0 venue is open.
- Half-days (24 and 31 December) shift ``eod_pipeline`` to its ``half_day`` time and stop
  ``position_monitor`` at ``half_day_end``: the *normal* variant is skipped on half-days and
  the *half_day* variant runs only on half-days.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.config.schema import JobSpec
from app.domain.calendar import MarketCalendars


@dataclass(frozen=True)
class GateDecision:
    run: bool
    reason: str
    open_mics: tuple[str, ...] = ()


def should_run(
    name: str,
    spec: JobSpec,
    run_date: date,
    calendars: MarketCalendars,
    mics: list[str] | None = None,
    variant: str = "normal",
) -> GateDecision:
    if not spec.enabled:
        return GateDecision(False, "job désactivé dans params.yaml")
    if not spec.market_days_only:
        return GateDecision(True, "job système (tous les jours)")
    candidates = mics if mics else calendars.p0_mics
    open_mics = tuple(calendars.open_mics(run_date, candidates))
    if not open_mics:
        return GateDecision(False, f"aucune place ouverte le {run_date.isoformat()} parmi {candidates}")
    if mics and len(open_mics) < len(mics):
        closed = sorted(set(mics) - set(open_mics))
        # Instrument-scoped job: skip only the closed venues (partial run).
        reason = f"places fermées ignorées : {closed}"
    else:
        reason = "place(s) ouverte(s)"
    if spec.half_day or spec.half_day_end:
        half = any(calendars.get(m).is_half_day(run_date) for m in open_mics)
        if variant == "half_day" and not half:
            return GateDecision(False, "variante demi-séance : pas une demi-séance", open_mics)
        if variant == "normal" and half and spec.half_day:
            return GateDecision(False, "demi-séance : la variante half_day tourne à sa place", open_mics)
    return GateDecision(True, reason, open_mics)


def venue_for_instrument(exchange_code: str, calendars: MarketCalendars) -> str:
    """EODHD exchange code (``PA``, ``XETRA``) → MIC, never defaulting to Paris."""
    return calendars.mic_for_eodhd(exchange_code)
