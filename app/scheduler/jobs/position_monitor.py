"""``position_monitor`` (every minute in session): S1 threshold crossed → P1 (then SELL at market
proposal after ``risk.stop.unconfirmed_exit_minutes``), unprotected quantity → P1 in every mode,
declarations unmatched for 24 h → P3. Quotes come from the best configured source with their
status; a delayed quote is shown as such, never as realtime."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from sqlalchemy import select

from app.data.dto import Quote
from app.db.models import PositionRow
from app.db.session import db_session
from app.domain.monitor import StopEvent, check_stop, check_unprotected
from app.domain.timeutil import from_store
from app.notify.base import Button, Event
from app.scheduler.runner import JobContext
from app.services.portfolio import _domain_position, unmatched_declarations_older_than

log = logging.getLogger("bourse.jobs.position_monitor")

BTN_EXEC = [Button("Exécuté", "execute"), Button("Stop saisi", "stop_saisi"), Button("Vu", "vu")]


def quotes_for(isins: list[str], config=None) -> dict[str, Quote]:  # type: ignore[no-untyped-def]
    """Phase 1: EODHD REST (delayed) unless Saxo prices are configured. Empty dict if none."""
    if not isins:
        return {}
    out: dict[str, Quote] = {}
    if os.environ.get("EODHD_API_TOKEN"):
        from app.data.providers.eodhd import EodhdProvider, eodhd_from
        from app.services.instruments import eodhd_symbols_for

        symbols = eodhd_symbols_for(isins)
        try:
            provider = eodhd_from(config) if config is not None else EodhdProvider()
            for q in provider.fetch_quotes(list(symbols)):
                isin = symbols.get(q.symbol)
                if isin:
                    out[isin] = q
        except Exception as exc:  # noqa: BLE001
            log.warning("quotes indisponibles : %s", exc)
    return out


def venue_of(s, isin: str) -> str | None:  # type: ignore[no-untyped-def]
    """MIC from the referential (or the YAML map); ``None`` when unknown — never Paris by default."""
    from app.db.models import Instrument
    from app.services.instruments import _load as instruments_map

    inst = s.get(Instrument, isin)
    if inst is not None and inst.mic:
        return inst.mic
    return instruments_map().get(isin, {}).get("mic")


def monitor_once(ctx: JobContext, quotes: dict[str, Quote], now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    alerts = ctx.alerts
    mode = ctx.mode
    unconfirmed = ctx.config.params.risk.stop.unconfirmed_exit_minutes
    n = 0
    with db_session() as s:
        for row in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0)):
            p = _domain_position(s, row)
            q = quotes.get(row.isin)
            if q is not None and q.last is not None:
                row.last_price, row.last_price_at, row.last_price_status = (
                    q.last,
                    q.market_timestamp,
                    q.data_status.value,
                )
            price = row.last_price
            status = row.last_price_status or "indisponible"
            u = check_unprotected(p, row.id)
            if u.incident_id:
                ev = Event(
                    "P1",
                    "unprotected_fill",
                    f"{row.isin} : {u.qty_unprotected} titre(s) sans protection",
                    [
                        u.message,
                        f"Quantité détenue {p.qty_held}, couverte {p.qty_protected}",
                        "Saisir le stop chez le courtier puis « Stop saisi »",
                    ],
                    BTN_EXEC,
                    isin=row.isin,
                    incident_id=u.incident_id,
                )
                if alerts and alerts.emit(s, ev, mode, True, now, row.account_id, row.id):
                    n += 1
            elif alerts:
                alerts.close_incident(s, f"unprotected:{row.id}")
            mic = venue_of(s, row.isin)
            if mic is None or mic not in ctx.calendars.venues:
                session_open = False  # unknown venue: threshold alert only, never a SELL-at-market proposal
                log.warning("place inconnue pour %s : pas de proposition SELL au marché", row.isin)
            else:
                session_open = ctx.calendars.get(mic).is_open_at(now)
            crossed = from_store(row.crossed_since) if row.crossed_since else None
            chk = check_stop(p, row.id, price, now, crossed, False, unconfirmed, session_open)
            if chk.event == StopEvent.NONE:
                row.crossed_since = None
                if alerts:
                    alerts.close_incident(s, f"stop_crossed:{row.id}")
                continue
            if row.crossed_since is None:
                row.crossed_since = now
            title = (
                f"{row.isin} : seuil de stop franchi"
                if chk.event == StopEvent.THRESHOLD_CROSSED
                else f"{row.isin} : SELL au marché proposé"
            )
            ev = Event(
                "P1",
                "stop_threshold",
                title,
                [
                    chk.message,
                    f"Cours {price} ({status}, marché {row.last_price_at:%H:%M} UTC)"
                    if row.last_price_at
                    else f"Cours {price} ({status})",
                    "Vérifier l'exécution chez le courtier ; déclarer « Exécuté » si le stop est passé",
                ],
                BTN_EXEC,
                isin=row.isin,
                incident_id=chk.incident_id,
            )
            if alerts and alerts.emit(s, ev, mode, True, now, row.account_id, row.id):
                n += 1
        hours = int(ctx.config.params.accounts.cto.match_tolerance.get("unmatched_alert_hours", 24))
        for t in unmatched_declarations_older_than(s, hours, now):
            ev = Event(
                "P3",
                "declaration_unmatched",
                f"{t.isin} : exécution déclarée non retrouvée chez le courtier",
                [
                    f"{t.side} {t.qty} @ {t.price} déclaré le {t.ts:%d/%m %H:%M}",
                    "Vérifier l'import Saxo / CSV BoursoBank",
                ],
                isin=t.isin,
            )
            if alerts and alerts.emit(s, ev, mode, False, now, t.account_id, t.position_id):
                n += 1
    return n


def run(ctx: JobContext) -> int:
    with db_session() as s:
        isins = [
            r.isin
            for r in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0))
        ]
    return monitor_once(ctx, quotes_for(isins, ctx.config))
