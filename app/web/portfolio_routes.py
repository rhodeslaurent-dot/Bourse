"""Pages ``/positions`` and ``/import`` (docs/09 §9.5)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.portfolio_routes import get_positions
from app.data.providers.boursobank_csv import BoursoFormatError, parse_operations, parse_positions
from app.db.session import db_session
from app.domain.orders import Execution, MatchTolerance, Side, TradeKind
from app.services.instruments import name_of
from app.services.portfolio import (
    account_by_type,
    declare_execution,
    declare_stop,
    import_confirmed_fills,
    update_portfolio_state,
)
from app.web.routes import templates

router = APIRouter()


@router.get("/positions", response_class=HTMLResponse)
def positions_page(request: Request) -> HTMLResponse:
    data = get_positions(request)
    for p in data["positions"]:
        p["name"] = name_of(p["isin"])
    return templates.TemplateResponse(request, "positions.html", {"request": request, **data, "now": datetime.now(UTC)})


@router.post("/positions/execute")
def execute_form(
    request: Request,
    account: str = Form(...),
    isin: str = Form(...),
    side: str = Form("buy"),
    qty: int = Form(...),
    price: float = Form(...),
) -> RedirectResponse:
    with db_session() as s:
        acc = account_by_type(s, account)
        declare_execution(
            s,
            acc.id,
            isin.upper().strip(),
            side,
            qty,
            price,
            datetime.now(UTC),
            "web",
            mode="srd" if account == "cto_srd" else "comptant",
        )
    return RedirectResponse("/positions", status_code=303)


@router.post("/positions/stop")
def stop_form(
    request: Request,
    position_id: int = Form(...),
    level: float = Form(...),
    order_type: str = Form("seuil"),
    qty_covered: int = Form(...),
    kind: str = Form("initial"),
) -> RedirectResponse:
    with db_session() as s:
        try:
            declare_stop(s, position_id, level, order_type, qty_covered, kind, "web")
        except ValueError as exc:
            return RedirectResponse(f"/positions?error={exc}", status_code=303)
    return RedirectResponse("/positions", status_code=303)


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request, message: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "import.html", {"request": request, "message": message})


@router.post("/import/boursobank")
async def import_boursobank(
    request: Request,
    kind: str = Form(...),
    file: UploadFile = File(...),  # noqa: B008
) -> RedirectResponse:
    text = (await file.read()).decode("utf-8-sig", errors="replace")
    tol = request.app.state.config.params.accounts.cto.match_tolerance
    try:
        with db_session() as s:
            pea = account_by_type(s, "pea")
            if kind == "operations":
                ops = parse_operations(text)
                fills = [
                    Execution(
                        pea.id,
                        o.isin,
                        Side.BUY if o.side == "buy" else Side.SELL,
                        o.quantity,
                        o.price,
                        o.date,
                        TradeKind.CONFIRMED,
                        broker_fill_id=f"bourso-{o.reference}",
                        fees=o.fees,
                        source="csv_boursobank",
                    )
                    for o in ops
                ]
                rep = import_confirmed_fills(
                    s, fills, MatchTolerance(float(tol.get("price_pct", 0.005)), int(tol.get("minutes", 10)))
                )
                update_portfolio_state(s, pea.id, pea.cash, [], [], "csv_boursobank", declarative=True)
                msg = (
                    f"{len(ops)} opérations lues : {rep.matched} rapprochées, "
                    f"{rep.discretionary} sans déclaration, {rep.duplicates_ignored} doublons ignorés"
                )
            else:
                rows = parse_positions(text)
                update_portfolio_state(
                    s, pea.id, pea.cash, [r.__dict__ for r in rows], [], "csv_boursobank", declarative=True
                )
                msg = f"{len(rows)} positions lues (rapprochement affiché sur /positions)"
    except BoursoFormatError as exc:
        msg = f"Format non reconnu : {exc}"
    return RedirectResponse(f"/import?message={msg}", status_code=303)
