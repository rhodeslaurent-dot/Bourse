"""``/performance`` (docs/17 §17.5): realized, latent with status, quality, costs, equity curve."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.db.session import db_session
from app.services.broker_plan import analysis as plan_analysis
from app.services.performance import build_performance
from app.web.charts import bars, line_chart
from app.web.routes import templates

router = APIRouter()


@router.get("/performance", response_class=HTMLResponse)
def performance_page(request: Request) -> HTMLResponse:
    with db_session() as s:
        data = build_performance(s, request.app.state.config)
        v = data["v"]
        eq = [(d, e) for d, e in v.equity]
        ctx = {
            "request": request,
            **data,
            "equity_svg": line_chart({"main": eq}) if len(eq) >= 2 else "",
            "months_svg": bars(data["months"]) if data["months"] else "",
            "plan": plan_analysis(s, request.app.state.config),
        }
        return templates.TemplateResponse(request, "performance.html", ctx)
