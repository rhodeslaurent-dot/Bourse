"""``/assistant`` (docs/17 §17.4), ``/valeur/<isin>`` (§17.8) and ``/recherche``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from app.db.models import AssistantMessage, Instrument, NewsItem, PositionRow, PriceEod, Proposal, Signal, WatchlistRow
from app.db.session import db_session
from app.services.assistant import PAGES, ask, build_context, page_key, resolve_isin, search_instruments
from app.services.explain import explain_proposal
from app.services.market_data import latest_features
from app.services.notes import get_note
from app.web.charts import line_chart
from app.web.routes import templates

router = APIRouter()


def _note_view(n):  # type: ignore[no-untyped-def]
    return {"text": n.text, "updated_at": n.updated_at} if n else None


@router.get("/assistant", response_class=HTMLResponse)
def assistant_page(request: Request, isin: str | None = None) -> HTMLResponse:
    st = request.app.state
    with db_session() as s:
        q = select(AssistantMessage).order_by(AssistantMessage.id.desc()).limit(20)
        if isin:
            q = (
                select(AssistantMessage)
                .where(AssistantMessage.isin == isin)
                .order_by(AssistantMessage.id.desc())
                .limit(20)
            )
        history = list(s.scalars(q))
        names = (
            {
                i.isin: i.name
                for i in s.scalars(select(Instrument).where(Instrument.isin.in_([m.isin for m in history if m.isin])))
            }
            if history
            else {}
        )
        choices = [
            (w.isin, names.get(w.isin) or w.isin)
            for w in s.scalars(select(WatchlistRow).where(WatchlistRow.active.is_(True)).limit(60))
        ]
        choices += [
            (p.isin, p.isin)
            for p in s.scalars(select(PositionRow).where(PositionRow.closed_at.is_(None), PositionRow.qty_held > 0))
        ]
        inst = s.get(Instrument, isin) if isin else None
        llm = getattr(st, "llm", None)
        return templates.TemplateResponse(
            request,
            "assistant.html",
            {
                "request": request,
                "history": history,
                "names": names,
                "isin": isin,
                "inst": inst,
                "choices": choices,
                "llm_on": bool(llm and llm.enabled),
            },
        )


def _ask_from_form(request: Request, question: str, isin: str, page: str):  # type: ignore[no-untyped-def]
    if not question.strip():
        raise HTTPException(400, "question vide")
    st = request.app.state
    cfg = st.config
    with db_session() as s:
        target = resolve_isin(s, isin) or (resolve_isin(s, question) if not isin else None)
        ask(
            s,
            getattr(st, "llm", None),
            question,
            target,
            float(cfg.params.capital.capital_pilote_eur),
            cfg.params.llm.assistant_max_tokens if cfg.params.llm else 800,
            page=page or None,
            config=cfg,
        )
    return target


@router.post("/assistant")
def assistant_ask(
    request: Request, question: str = Form(...), isin: str = Form(""), page: str = Form("")
) -> RedirectResponse:
    target = _ask_from_form(request, question, isin, page)
    return RedirectResponse(f"/assistant{'?isin=' + target if target else ''}", status_code=303)


def _thread(request: Request, page: str, isin: str | None) -> HTMLResponse:
    key = page_key(page)
    with db_session() as s:
        q = select(AssistantMessage).order_by(AssistantMessage.id.desc())
        q = q.where(AssistantMessage.page == key) if key else q.where(AssistantMessage.page.is_(None))
        q = q.where(AssistantMessage.isin == isin) if isin else q.where(AssistantMessage.isin.is_(None))
        msgs = list(reversed(list(s.scalars(q.limit(3)))))
        inst = s.get(Instrument, isin) if isin else None
        st = request.app.state
        return templates.TemplateResponse(
            request,
            "_assistant_thread.html",
            {
                "request": request,
                "msgs": msgs,
                "inst": inst,
                "page_name": PAGES.get(key or "", "") if key != "/valeur" else "Fiche valeur",
                "llm_on": bool(getattr(st, "llm", None) and st.llm.enabled),
            },
        )


@router.get("/assistant/panel", response_class=HTMLResponse)
def assistant_panel(request: Request, page: str = "", isin: str = "") -> HTMLResponse:
    """Fragment for the floating panel (docs/18 §18.3): last exchanges of this page / value."""
    return _thread(request, page, isin.upper() or None)


@router.post("/assistant/panel", response_class=HTMLResponse)
def assistant_panel_ask(
    request: Request, question: str = Form(...), isin: str = Form(""), page: str = Form("")
) -> HTMLResponse:
    target = _ask_from_form(request, question, isin, page)
    return _thread(request, page, target)


@router.get("/recherche", response_class=HTMLResponse)
def search(request: Request, q: str = "") -> HTMLResponse:
    with db_session() as s:
        rows = search_instruments(s, q)
        if len(rows) == 1:
            return RedirectResponse(f"/valeur/{rows[0].isin}", status_code=303)  # type: ignore[return-value]
        return templates.TemplateResponse(request, "recherche.html", {"request": request, "q": q, "rows": rows})


@router.get("/valeur/{isin}", response_class=HTMLResponse)
def instrument_page(request: Request, isin: str) -> HTMLResponse:
    isin = isin.upper()
    cfg = request.app.state.config
    with db_session() as s:
        inst = s.get(Instrument, isin)
        closes = list(
            reversed(
                list(s.scalars(select(PriceEod).where(PriceEod.isin == isin).order_by(PriceEod.day.desc()).limit(120)))
            )
        )
        feat = latest_features(s, [isin]).get(isin)
        if inst is None and not closes:
            raise HTTPException(404, "valeur inconnue du référentiel")
        series = [(c.day, c.close) for c in closes]
        levels = {}
        if feat and feat.pivot_60:
            levels["plus haut 60 j"] = feat.pivot_60
        positions = list(
            s.scalars(
                select(PositionRow).where(
                    PositionRow.isin == isin, PositionRow.closed_at.is_(None), PositionRow.qty_held > 0
                )
            )
        )
        for p in positions:
            if p.stop_current:
                levels["stop"] = p.stop_current
        chart = line_chart({"main": series}, levels) if len(series) >= 2 else ""
        props = list(
            s.scalars(
                select(Proposal)
                .where(Proposal.isin == isin, Proposal.deleted_at.is_(None))
                .order_by(Proposal.id.desc())
                .limit(5)
            )
        )
        cap = float(cfg.params.capital.capital_pilote_eur)
        prop_view = [
            {"p": p, "headline": explain_proposal(p, s.get(Signal, p.signal_id), None, cap).summary} for p in props
        ]
        since = datetime.now(UTC) - timedelta(days=7)
        news = [
            n
            for n in s.scalars(
                select(NewsItem).where(NewsItem.fetched_at >= since).order_by(NewsItem.id.desc()).limit(300)
            )
            if isin in (n.isins or [])
        ][:8]
        wl = s.scalar(select(WatchlistRow).where(WatchlistRow.isin == isin, WatchlistRow.active.is_(True)))
        last = closes[-1] if closes else None
        prev = closes[-2] if len(closes) >= 2 else None
        ctx = {
            "request": request,
            "isin": isin,
            "inst": inst,
            "chart": chart,
            "feat": feat,
            "last": last,
            "change": (last.close / prev.close - 1) if last and prev and prev.close else None,
            "positions": positions,
            "props": prop_view,
            "news": news,
            "wl": wl,
            "note": _note_view(get_note(s, "isin", isin)),
            "n_closes": len(closes),
            "facts": build_context(s, isin, cap),
            "assistant_isin": isin,
        }
        from app.services.social_content import facts as content_facts
        from app.services.social_content import latest as content_latest

        ctx["forum"] = content_latest(s, isin)
        ctx["forum_facts"] = content_facts(ctx["forum"])
        return templates.TemplateResponse(request, "valeur.html", ctx)


@router.post("/valeur/{isin}/echanges")
def instrument_forum(request: Request, isin: str) -> RedirectResponse:
    """docs/30 — read and label the value's recent forum messages now (on request)."""
    import httpx

    from app.services.social_content import analyse

    isin = isin.upper()
    with db_session() as s, httpx.Client(follow_redirects=True) as client:
        inst = s.get(Instrument, isin)
        if inst is None:
            raise HTTPException(404, "valeur inconnue du référentiel")
        analyse(s, request.app.state.config, client, inst, "demande", getattr(request.app.state, "llm", None))
    return RedirectResponse(f"/valeur/{isin}", status_code=303)
