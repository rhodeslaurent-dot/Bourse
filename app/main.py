"""FastAPI application factory. Startup refuses to run if a required config group is missing."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI

from app.api.routes import router as api_router
from app.api.security import AccessMiddleware
from app.config import LoadedConfig, load_params
from app.db.repo import current_override, log_telegram_event, record_params_version, set_override
from app.db.session import database_url, db_session
from app.domain.availability import resolve_mode
from app.domain.calendar import MarketCalendars, load_market_calendars
from app.logging_setup import setup_logging
from app.notify.email import smtp_settings
from app.notify.telegram import ModeStore, TelegramBot, telegram_settings
from app.scheduler.service import create_scheduler
from app.web.routes import router as web_router

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
        from datetime import UTC

        return resolve_mode(datetime.now(UTC), schedule, av.default_mode if av else "reunion", ov).mode

    def log_event(uid: int, auth: bool, kind: str, text: str | None, data: str | None, handled: bool) -> None:
        with db_session() as s:
            log_telegram_event(s, uid, auth, kind, text, data, handled)

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
        if start_scheduler:
            app.state.scheduler = create_scheduler(config, calendars)
            app.state.scheduler.start()
        tg = telegram_settings()
        if start_telegram and tg:
            app.state.telegram = TelegramBot(tg[0], tg[1], _mode_store(config))
            await app.state.telegram.start()
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
    app.include_router(api_router)
    app.include_router(web_router)
    return app


def app_factory() -> FastAPI:
    return create_app()
