"""Earnings announcements history (docs/34 §34.4): dates, time of day, reported and estimated EPS.

Two sources, the first that answers: the EODHD calendar API (``/calendar/earnings``, one call per request for
many symbols — may be outside the subscribed plan: a 403 says so) and Yahoo Finance through ``yfinance``
(``get_earnings_dates``, 100 announcements per page; unofficial, throttled by the caller). Every record keeps its
source; a missing field stays missing (rule 2). Announcement *dates* carry no price: loading them for a period
kept for confirmation reads no outcome.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from app.data.base import ProviderError, ProviderUnavailable


@dataclass(frozen=True)
class EarningsRecord:
    symbol: str
    day: date  # local date of the announcement
    ts: datetime | None  # UTC time when the source gives one
    when: str  # bmo | amc | intraday | unknown (relative to the venue's session, when known)
    eps_actual: float | None
    eps_estimate: float | None
    surprise_pct: float | None
    source: str


def _num(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


EODHD_WHEN = {"BeforeMarket": "bmo", "AfterMarket": "amc", "DuringMarket": "intraday"}


def eodhd_earnings(get: Callable[..., Any], symbols: Sequence[str], start: date, end: date) -> list[EarningsRecord]:
    """``get`` is ``EodhdProvider._get``. Raises ProviderUnavailable when the plan does not include the calendar."""
    try:
        span = {"from": start.isoformat(), "to": end.isoformat()}
        data = get("/calendar/earnings", symbols=",".join(symbols), **span)
    except ProviderError as exc:
        if "EODHD 403" in str(exc) or "EODHD 401" in str(exc):
            raise ProviderUnavailable(f"calendrier EODHD hors forfait : {exc}") from exc
        raise
    out: list[EarningsRecord] = []
    for r in (data or {}).get("earnings", []) if isinstance(data, dict) else []:
        try:
            day = date.fromisoformat(str(r.get("report_date")))
        except ValueError:
            continue
        actual, est = _num(r.get("actual")), _num(r.get("estimate"))
        out.append(
            EarningsRecord(
                str(r.get("code")),
                day,
                None,
                EODHD_WHEN.get(str(r.get("before_after_market")), "unknown"),
                actual,
                est,
                _num(r.get("percent")),
                "eodhd",
            )
        )
    return out


def when_of(local: datetime, open_hour: float, close_hour: float) -> str:
    """bmo / amc / intraday from the local time of the announcement; midnight means « time unknown »."""
    h = local.hour + local.minute / 60
    if local.hour == 0 and local.minute == 0:
        return "unknown"
    if h < open_hour:
        return "bmo"
    if h >= close_hour:
        return "amc"
    return "intraday"


def yahoo_earnings(
    symbol: str,
    pages: int = 3,
    fetch: Callable[[str, int, int], Any] | None = None,
    local_tz: Any | None = None,
    session_hours: tuple[float, float] = (9.0, 17.5),
) -> list[EarningsRecord]:
    """Up to ``pages`` × 100 announcements (newest first). ``fetch(symbol, limit, offset)`` returns the
    ``get_earnings_dates`` DataFrame (index: tz-aware announcement time)."""
    if fetch is None:
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover
            raise ProviderUnavailable("yfinance non installé") from exc

        def fetch(sym: str, limit: int, offset: int) -> Any:
            return yf.Ticker(sym).get_earnings_dates(limit=limit, offset=offset)

    out: list[EarningsRecord] = []
    for page in range(pages):
        try:
            df = fetch(symbol, 100, page * 100)
        except Exception as exc:  # noqa: BLE001 — unofficial source: an error means « indisponible »
            if out:
                break
            raise ProviderUnavailable(f"yahoo {symbol}: {exc.__class__.__name__}") from exc
        if df is None or len(df) == 0:
            break
        for idx, row in df.iterrows():
            ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
            if not isinstance(ts, datetime):
                continue
            local = ts.astimezone(local_tz) if (local_tz is not None and ts.tzinfo) else ts
            out.append(
                EarningsRecord(
                    symbol,
                    local.date(),
                    ts.astimezone(UTC) if ts.tzinfo else None,
                    when_of(local, *session_hours),
                    _num(row.get("Reported EPS")),
                    _num(row.get("EPS Estimate")),
                    _num(row.get("Surprise(%)")),
                    "yahoo",
                )
            )
        if len(df) < 100:
            break
    return out
