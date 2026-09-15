"""``/watchlist`` (momentum + à l'affût + manual, TradingView export) and ``/api/instruments/<isin>``."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy import select

from app.db.models import Instrument, MarketRegimeRow, UniverseSnapshot, WatchlistRow
from app.db.session import db_session
from app.services.market_data import latest_features
from app.services.universe import tradingview_export
from app.web.routes import templates

router = APIRouter()


@router.get("/watchlist", response_class=HTMLResponse)
def watchlist_page(request: Request) -> HTMLResponse:
    with db_session() as s:
        rows = list(
            s.scalars(
                select(WatchlistRow)
                .where(WatchlistRow.active.is_(True))
                .order_by(WatchlistRow.rs_rank.desc().nulls_last())
            )
        )
        insts = (
            {i.isin: i for i in s.scalars(select(Instrument).where(Instrument.isin.in_([r.isin for r in rows])))}
            if rows
            else {}
        )
        feats = latest_features(s, [r.isin for r in rows]) if rows else {}
        snap = s.scalar(select(UniverseSnapshot).order_by(UniverseSnapshot.id.desc()))
        regime = s.scalar(select(MarketRegimeRow).order_by(MarketRegimeRow.id.desc()))
        items = [
            {
                "isin": r.isin,
                "name": insts[r.isin].name if r.isin in insts else r.isin,
                "reason": r.reason,
                "source": r.source,
                "rs_rank": r.rs_rank,
                "mic": insts[r.isin].mic if r.isin in insts else "?",
                "pea": insts[r.isin].pea_eligible if r.isin in insts else None,
                "srd": insts[r.isin].srd_status if r.isin in insts else "unknown",
                "levels": r.levels or {},
                "feat": feats.get(r.isin),
                "added_at": r.added_at,
            }
            for r in rows
        ]
        ctx = {
            "request": request,
            "items": items,
            "snapshot": snap,
            "regime": regime,
            "universe_count": s.scalar(select(Instrument.isin).where(Instrument.in_universe.is_(True)).limit(1))
            is not None,
        }
        return templates.TemplateResponse(request, "watchlist.html", ctx)


@router.post("/watchlist/add")
def watchlist_add(isin: str = Form(...), reason: str = Form("manual"), note: str = Form("")) -> RedirectResponse:
    isin = isin.strip().upper()
    if len(isin) != 12:
        raise HTTPException(400, "ISIN invalide")
    with db_session() as s:
        if s.scalar(select(WatchlistRow).where(WatchlistRow.isin == isin, WatchlistRow.active.is_(True))) is None:
            s.add(
                WatchlistRow(
                    isin=isin,
                    reason=reason,
                    source="manual",
                    active=True,
                    levels={"note": note} if note else None,
                    added_at=datetime.now(UTC),
                )
            )
    return RedirectResponse("/watchlist", status_code=303)


@router.post("/watchlist/remove")
def watchlist_remove(isin: str = Form(...)) -> RedirectResponse:
    with db_session() as s:
        for row in s.scalars(
            select(WatchlistRow).where(WatchlistRow.isin == isin.strip().upper(), WatchlistRow.active.is_(True))
        ):
            row.active = False
    return RedirectResponse("/watchlist", status_code=303)


@router.get("/watchlist/export.txt", response_class=PlainTextResponse)
def watchlist_export() -> PlainTextResponse:
    with db_session() as s:
        return PlainTextResponse(tradingview_export(s))


@router.get("/api/instruments/{isin}")
def api_instrument(isin: str) -> dict[str, object]:
    with db_session() as s:
        inst = s.get(Instrument, isin.upper())
        if inst is None:
            raise HTTPException(404, "instrument inconnu")
        f = latest_features(s, [inst.isin]).get(inst.isin)
        return {
            "isin": inst.isin,
            "name": inst.name,
            "mic": inst.mic,
            "currency": inst.currency,
            "ticker_eodhd": inst.ticker_eodhd,
            "ticker_tv": inst.ticker_tv,
            "country_of_domicile": inst.country_of_domicile,
            "sector": inst.sector,
            "market_cap_eur": inst.market_cap_eur,
            "adv_eur_20": inst.adv_eur_20,
            "in_universe": inst.in_universe,
            "universe_reasons": inst.universe_reasons,
            "small_cap": inst.small_cap,
            "pea": {
                "eligible_regulatory": inst.pea_eligible,
                "confidence": inst.pea_confidence,
                "source": inst.pea_source,
                "available_boursobank": inst.pea_available_boursobank,
                "order_types_ok": inst.pea_order_types_ok,
            },
            "srd": {
                "status": inst.srd_status,
                "confirmed_by_user": inst.srd_confirmed_by_user,
                "label": f"SRD {inst.srd_status} selon règles — à confirmer chez Saxo"
                if not inst.srd_confirmed_by_user
                else f"SRD {inst.srd_status} (confirmé)",
            },
            "earnings_next_date": inst.earnings_next_date.isoformat() if inst.earnings_next_date else None,
            "features": {
                k: getattr(f, k)
                for k in (
                    "atr14",
                    "adr20",
                    "mm20",
                    "mm50",
                    "mm200",
                    "rvol",
                    "rs_rank",
                    "pivot_60",
                    "high_52w",
                    "consolidation_20",
                    "ti65",
                    "rsi14",
                    "sessions",
                    "gap_in_data",
                )
            }
            if f
            else None,
            "features_day": f.day.isoformat() if f else None,
        }
