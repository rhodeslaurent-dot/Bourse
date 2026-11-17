"""Step 2, lot D1 (user's decision of 17/11/2026) — a caution when a manager of the company has sold recently.

The step 1 measure (docs/40 §40.3 bis) kept one source out of all those measured: after a manager's sale published
to the AMF, the value did on average ``insider_caution.measured_gap_20d`` less over 20 sessions than its own later
periods. Information of caution on an idea card and on a held position — **never an order, never a block**. The
declarant's name is never shown: the role only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.timeutil import PARIS, from_store


@dataclass(frozen=True)
class CautionSettings:
    window_days: int
    measured_gap_20d: float
    measured_on: str
    t_stat: float


def settings(config: Any) -> CautionSettings | None:
    """``insider_caution`` (params.yaml); None when the group is absent or disabled (rule 8: no group, no feature)."""
    g = (getattr(config, "raw", None) or {}).get("insider_caution") or {}
    if not g or not g.get("enabled", False):
        return None
    return CautionSettings(
        int(g.get("window_days", 28)),
        float(g["measured_gap_20d"]),
        str(g.get("measured_on") or "?"),
        float(g.get("t_stat") or 0.0),
    )


@dataclass(frozen=True)
class InsiderSale:
    published: date
    role: str | None
    amount_eur: float | None


def recent_sales(s: Session, isin: str, day: date, window_days: int) -> list[InsiderSale]:
    """AMF declarations of a sale (« cession ») on the value published in the ``window_days`` before ``day``
    (included), newest first. Facts only: the role and the amount when declared, never the name."""
    from app.db.models import InsiderDeclaration

    start = datetime.combine(day - timedelta(days=window_days), time.min, tzinfo=PARIS).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=PARIS).astimezone(UTC)
    rows = s.scalars(
        select(InsiderDeclaration)
        .where(
            InsiderDeclaration.isin == isin,
            InsiderDeclaration.nature == "cession",
            InsiderDeclaration.market_timestamp >= start,
            InsiderDeclaration.market_timestamp < end,
        )
        .order_by(InsiderDeclaration.market_timestamp.desc())
    )
    out = []
    for r in rows:
        amount = r.price * r.volume if r.price and r.volume and (r.currency in (None, "", "EUR")) else None
        out.append(InsiderSale(from_store(r.market_timestamp).astimezone(PARIS).date(), r.role, amount))
    return out


def caution_text(sales: list[InsiderSale], st: CautionSettings) -> str | None:
    """One line for a card or a position (None without a recent sale)."""
    if not sales:
        return None
    last = sales[0]
    role = f" ({last.role})" if last.role else ""
    more = f", et {len(sales) - 1} autre(s) dans la fenêtre" if len(sales) > 1 else ""
    gap = f"{st.measured_gap_20d * 100:+.1f}".replace(".", ",")
    return (
        f"Vente de dirigeant publiée le {last.published:%d/%m}{role}{more} — prudence : après une vente, la valeur a "
        f"fait en moyenne {gap} point(s) sur 20 séances par rapport à ses propres périodes suivantes (mesure du "
        f"{st.measured_on}, t {st.t_stat:.1f}). Information, pas un ordre."
    )


def caution_for(s: Session, config: Any, isin: str, day: date) -> str | None:
    st = settings(config)
    if st is None:
        return None
    return caution_text(recent_sales(s, isin, day, st.window_days), st)
