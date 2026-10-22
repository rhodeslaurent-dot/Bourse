"""docs/23 S2 — published net short positions: storage per authority and the current declared total per
value (latest position of each holder; a holder whose latest notice is under 0.5 % no longer counts)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.providers.short_positions import ShortRow
from app.db.models import ShortPosition

PUBLIC_THRESHOLD = 0.005  # EU 236/2012: positions are public from 0.5 % of the share capital


def store(s: Session, authority: str, rows: list[ShortRow], url: str | None) -> int:
    """Insert the positions not yet stored (same authority, value, holder and day); returns the count."""
    seen = {
        (x.isin, x.holder, x.position_date)
        for x in s.scalars(select(ShortPosition).where(ShortPosition.authority == authority))
    }
    now, n = datetime.now(UTC), 0
    for r in rows:
        key = (r.isin, r.holder, r.position_date)
        if key in seen:
            continue
        seen.add(key)
        s.add(
            ShortPosition(
                authority=authority, isin=r.isin, issuer=(r.issuer or None) and r.issuer[:160], holder=r.holder,
                pct=r.pct, position_date=r.position_date, source_url=(url or "")[:500] or None, received_at=now,
            )
        )  # fmt: skip
        n += 1
    s.flush()
    return n


@dataclass
class ShortInterest:
    total_pct: float = 0.0
    holders: list[dict[str, Any]] = field(default_factory=list)
    latest_date: date | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_pct": round(self.total_pct, 5),
            "detenteurs": self.holders,
            "derniere_declaration": self.latest_date.isoformat() if self.latest_date else None,
        }


def short_interest(s: Session, isin: str, today: date, max_age_days: int = 730) -> ShortInterest:
    """Sum of the latest declared position of each holder (≥ 0.5 %), over all authorities. Only public
    positions are known: the real total can be higher — the figure is a floor, shown as such."""
    latest: dict[tuple[str, str], ShortPosition] = {}
    since = today - timedelta(days=max_age_days)
    for p in s.scalars(select(ShortPosition).where(ShortPosition.isin == isin, ShortPosition.position_date >= since)):
        k = (p.authority, p.holder)
        if k not in latest or p.position_date > latest[k].position_date:
            latest[k] = p
    out = ShortInterest()
    for p in sorted(latest.values(), key=lambda x: -x.pct):
        out.latest_date = max(out.latest_date or p.position_date, p.position_date)
        if p.pct >= PUBLIC_THRESHOLD:
            out.total_pct += p.pct
            out.holders.append(
                {
                    "detenteur": p.holder,
                    "pct": round(p.pct, 5),
                    "date": p.position_date.isoformat(),
                    "autorite": p.authority,
                }
            )
    return out
