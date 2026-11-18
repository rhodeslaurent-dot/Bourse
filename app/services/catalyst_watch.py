"""18/11/2026 — a second way into the watchlist, beside momentum (strongest values), the letters and manual adds:
**catalyst**, from stored facts only, whatever the value's trend over the last months.

- *results welcomed by the market*: a results publication (AMF communiqué or SEC 8-K item 2.02, ``official_filings``
  family ``resultats``) in the last ``lookback_days``, followed by a clear, traded rise (``app.domain.catalyst``);
- *news and attention*: the radar's labels of the last ``radar_days`` (``attention_corroboree``: unusual attention
  backed by news).

Watching only — never an order, never a proposal; the row says the facts and leaves after ``keep_days``. Values of
the universe only; a value already in the watchlist for another reason is left as it is."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Instrument, OfficialFiling, PriceEod, WatchlistRow
from app.domain.catalyst import DayBar, results_reaction, welcomed
from app.domain.timeutil import from_store


@dataclass
class CatalystSettings:
    enabled: bool = False
    lookback_days: int = 5
    min_move_pct: float = 0.03
    min_rel_volume: float = 1.5
    keep_days: int = 14
    radar_labels: list[str] = field(default_factory=lambda: ["attention_corroboree"])
    radar_days: int = 2


def settings(config: Any) -> CatalystSettings:
    raw = (getattr(config, "raw", None) or {}).get("watchlist_catalyst") or {}
    st = CatalystSettings()
    for k in st.__dataclass_fields__:
        if raw.get(k) is not None:
            setattr(st, k, raw[k])
    return st


def _row(s: Session, isin: str) -> WatchlistRow | None:
    return s.scalar(select(WatchlistRow).where(WatchlistRow.isin == isin, WatchlistRow.active.is_(True)))


def _put(s: Session, isin: str, source: str, levels: dict[str, Any], now: datetime) -> bool:
    row = _row(s, isin)
    if row is not None and row.reason != "catalyseur":
        return False  # already watched for another reason: left as it is
    if row is None:
        s.add(WatchlistRow(isin=isin, reason="catalyseur", source=source, active=True, levels=levels, added_at=now))
        return True
    row.source, row.levels = source, levels  # the newest fact, the expiry pushed back
    return False


def refresh(s: Session, config: Any, calendars: Any, today: date, now: datetime | None = None) -> tuple[int, int]:
    """(rows added, rows that expired). Nothing when the group is absent or disabled."""
    from app.db.models import WeakSignalHit

    now = now or datetime.now(UTC)
    st = settings(config)
    if not st.enabled:
        return 0, 0
    universe = {i.isin: i for i in s.scalars(select(Instrument).where(Instrument.in_universe.is_(True),
                                                                      Instrument.active.is_(True)))}  # fmt: skip
    expire = (today + timedelta(days=int(st.keep_days))).isoformat()
    added = 0
    since = now - timedelta(days=int(st.lookback_days))
    latest: dict[str, OfficialFiling] = {}
    q = select(OfficialFiling).where(OfficialFiling.family == "resultats", OfficialFiling.market_timestamp >= since)
    for f in s.scalars(q.order_by(OfficialFiling.market_timestamp)):
        if f.isin in universe:
            latest[f.isin] = f  # the newest publication of each value
    for isin, f in latest.items():
        inst = universe[isin]
        venue = getattr(calendars, "venues", {}).get(inst.mic)
        if venue is None:
            continue
        pub = from_store(f.market_timestamp).astimezone(venue.tz)
        rows = s.execute(
            select(PriceEod.day, PriceEod.close, PriceEod.volume)
            .where(PriceEod.isin == isin, PriceEod.day >= pub.date() - timedelta(days=45), PriceEod.day <= today)
            .order_by(PriceEod.day)
        ).all()
        r = results_reaction(pub.replace(tzinfo=None), venue.close_time, [DayBar(d, c, v or 0) for d, c, v in rows])
        if welcomed(r, float(st.min_move_pct), float(st.min_rel_volume)) and r is not None:
            move = f"{r.move:+.1%}".replace(".", ",")
            vol = f"{r.rel_volume:.1f}".replace(".", ",")
            levels = {"fait": f"résultats publiés le {pub:%d/%m}, salués par le marché : {move} le {r.day:%d/%m} "
                              f"avec {vol} fois le volume habituel", "expire_le": expire}  # fmt: skip
            added += _put(s, isin, "resultats", levels, now)
    labels = list(st.radar_labels or [])
    if labels:
        q2 = select(WeakSignalHit).where(WeakSignalHit.day >= today - timedelta(days=int(st.radar_days)),
                                         WeakSignalHit.label.in_(labels))  # fmt: skip
        for h in s.scalars(q2):
            if h.isin in universe and h.isin not in latest:
                levels = {
                    "fait": f"le {h.day:%d/%m}, attention inhabituelle du public appuyée par des nouvelles",
                    "expire_le": expire,
                }
                added += _put(s, h.isin, "radar", levels, now)
    gone = 0
    for row in s.scalars(
        select(WatchlistRow).where(WatchlistRow.reason == "catalyseur", WatchlistRow.active.is_(True))
    ):
        end = (row.levels or {}).get("expire_le")
        if end and str(end) < today.isoformat():
            row.active = False
            gone += 1
    s.flush()
    return added, gone
