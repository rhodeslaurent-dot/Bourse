"""FastAPI application factory. Startup refuses to run if a required config group is missing."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from app.api.portfolio_routes import router as portfolio_api_router
from app.api.routes import router as api_router
from app.api.security import AccessMiddleware
from app.config import LoadedConfig, load_params
from app.db.repo import current_override, log_telegram_event, record_params_version, set_override
from app.db.session import database_url, db_session
from app.domain.availability import resolve_mode
from app.domain.calendar import MarketCalendars, load_market_calendars
from app.logging_setup import setup_logging
from app.notify.base import Notifier
from app.notify.email import send_event_email, smtp_settings
from app.notify.telegram import ModeStore, TelegramBot, telegram_settings
from app.scheduler.service import create_scheduler
from app.services.alerts import AlertService
from app.web.portfolio_routes import router as portfolio_web_router
from app.web.routes import router as web_router
from app.web.watchlist_routes import router as watchlist_router

log = logging.getLogger("bourse")


def _mode_store(config: LoadedConfig) -> ModeStore:
    def set_mode(mode: str, until: datetime | None, source: str) -> None:
        with db_session() as s:
            set_override(s, mode, until, source)

    def current_mode() -> str:
        av = config.params.availability
        schedule = av.schedule if av and config.feature_enabled("availability.schedule") else []
        with db_session() as s:
            ov = current_override(s)
        return resolve_mode(datetime.now(UTC), schedule, av.default_mode if av else "reunion", ov).mode

    def log_event(uid: int, auth: bool, kind: str, text: str | None, data: str | None, handled: bool) -> None:
        with db_session() as s:
            log_telegram_event(s, uid, auth, kind, text, data, handled)

    from app.notify import telegram as tg_mod

    def declare(kind: str, payload: dict) -> str:
        from app.services.portfolio import account_by_type, declare_execution, declare_order, declare_stop

        with db_session() as s:
            try:
                if kind == "execution":
                    acc = account_by_type(s, payload["account"])
                    r = declare_execution(
                        s,
                        acc.id,
                        payload["isin"],
                        "buy",
                        payload["qty"],
                        payload["price"],
                        datetime.now(UTC),
                        "telegram",
                        mode="srd" if payload["account"] == "cto_srd" else "comptant",
                    )
                    return r.message + (
                        f" (position #{r.position_id}, NON PROTÉGÉE : /stop {r.position_id} <niveau> {payload['qty']})"
                        if r.created
                        else ""
                    )
                if kind == "protection":
                    p = declare_stop(
                        s,
                        payload["position_id"],
                        payload["level"],
                        payload["order_type"],
                        payload["qty_covered"],
                        "initial",
                        "telegram",
                    )
                    return f"stop enregistré : {p.protection_state.value} ({p.qty_protected} couverts sur {p.qty_held})"
                acc = account_by_type(s, payload["account"])
                o = declare_order(
                    s,
                    acc.id,
                    payload["isin"],
                    "buy",
                    payload["qty"],
                    payload["order_type"],
                    payload.get("limit_price"),
                    None,
                    "day",
                    "telegram",
                )
                return f"ordre saisi #{o.id} enregistré (aucune position créée)"
            except (ValueError, KeyError) as exc:
                return f"refusé : {exc}"

    tg_mod.DECLARE_HOOK["fn"] = declare
    return ModeStore(set_mode=set_mode, current_mode=current_mode, log_event=log_event)


def create_app(
    config: LoadedConfig | None = None,
    calendars: MarketCalendars | None = None,
    start_scheduler: bool = True,
    start_telegram: bool = True,
) -> FastAPI:
    setup_logging()
    config = config or load_params()  # raises ConfigError → process exits (docs/14 §14.2)
    calendars = calendars or load_market_calendars(
        os.environ.get("MARKET_CALENDARS_PATH", "config/market_calendars.yaml")
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        with db_session() as s:
            record_params_version(
                s,
                config.params.version,
                config.sha256,
                config.path.read_text() if config.path.exists() else "",
                config.params.capital.effective_from,
            )
        tg = telegram_settings()
        store = _mode_store(config)
        if start_telegram and tg:
            app.state.telegram = TelegramBot(tg[0], tg[1], store)
            await app.state.telegram.start()
        notifier = Notifier(
            telegram_send=app.state.telegram.send_event_sync if app.state.telegram else None,
            email_send=send_event_email if smtp_settings() else None,
        )
        app.state.alerts = AlertService(notifier, config.params.notifications.p1_repeat_minutes)
        if start_scheduler:
            app.state.scheduler = create_scheduler(
                config, calendars, alerts=app.state.alerts, mode_fn=store.current_mode
            )
            app.state.scheduler.start()
        for f in config.disabled_features:
            log.warning("fonctionnalité désactivée : %s — %s", f.label, f.reason)
        yield
        if getattr(app.state, "telegram", None):
            await app.state.telegram.stop()
        if getattr(app.state, "scheduler", None):
            app.state.scheduler.shutdown(wait=False)
        await asyncio.sleep(0)

    app = FastAPI(title="BOURSE-PILOT", version=config.params.version, lifespan=lifespan, docs_url="/api/docs")
    app.state.config = config
    app.state.calendars = calendars
    app.state.scheduler = None
    app.state.telegram = None
    app.state.telegram_enabled = telegram_settings() is not None
    app.state.email_enabled = smtp_settings() is not None
    app.state.db_kind = database_url().split(":", 1)[0]
    app.add_middleware(AccessMiddleware)
    app.state.alerts = None
    app.include_router(api_router)
    app.include_router(portfolio_api_router)
    app.include_router(web_router)
    app.include_router(portfolio_web_router)
    app.include_router(watchlist_router)
    return app


def app_factory() -> FastAPI:
    return create_app()
