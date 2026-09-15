"""Telegram bot: long polling (outbound only), single authorised ``user_id``, inline buttons,
``/mode`` command (docs/03, docs/14 §14.1). Never any order.

Pure helpers (:func:`is_authorised`, :func:`build_keyboard`, :func:`handle_text`) are unit-tested
without network; the :class:`TelegramBot` wraps python-telegram-bot.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.domain.availability import parse_mode_command
from app.domain.timeutil import format_dual
from app.notify.base import Button, Event

log = logging.getLogger("bourse.telegram")

STANDARD_BUTTONS = [
    Button("Vu", "vu"),
    Button("Watch", "watch"),
    Button("Ordre saisi", "ordre_saisi"),
    Button("Exécuté", "execute"),
    Button("Stop saisi", "stop_saisi"),
    Button("Ignorer", "ignorer"),
]


def is_authorised(user_id: int | None, allowed_user_id: int) -> bool:
    return bool(allowed_user_id) and user_id == allowed_user_id


@dataclass
class ModeStore:
    """Callbacks injected by the application (DB writes live outside this module)."""

    set_mode: Callable[[str, datetime | None, str], None]
    current_mode: Callable[[], str]
    log_event: Callable[[int, bool, str, str | None, str | None, bool], None]


@dataclass
class Reply:
    text: str
    handled: bool


def handle_text(text: str, user_id: int, allowed_user_id: int, store: ModeStore, now: datetime | None = None) -> Reply:
    """Route a text message/command. Unauthorised users get *no* reply (logged only)."""
    now = now or datetime.now(UTC)
    auth = is_authorised(user_id, allowed_user_id)
    if not auth:
        store.log_event(user_id, False, "command", text, None, False)
        log.warning("ignored Telegram message from unauthorised user_id=%s", user_id)
        return Reply("", False)
    if text.startswith("/mode"):
        cmd = parse_mode_command(text)
        if cmd is None:
            store.log_event(user_id, True, "command", text, None, False)
            return Reply(
                "Usage : /mode disponible|reunion|absent [durée]  (ex. /mode reunion 2h)\n"
                f"Mode courant : {store.current_mode()}",
                True,
            )
        until = now + cmd.duration if cmd.duration else None
        store.set_mode(cmd.mode, until, "telegram")
        store.log_event(user_id, True, "command", text, None, True)
        if until:
            return Reply(f"Mode « {cmd.mode} » jusqu'à {format_dual(until)}.", True)
        return Reply(f"Mode « {cmd.mode} » (sans limite ; /mode pour changer).", True)
    if text.startswith("/start") or text.startswith("/help"):
        store.log_event(user_id, True, "command", text, None, True)
        return Reply(
            "BOURSE-PILOT — aucun ordre n'est jamais passé.\n"
            "/mode disponible|reunion|absent [durée]\n/test — message de test avec boutons\n"
            f"Mode courant : {store.current_mode()}",
            True,
        )
    if text.startswith(("/exec", "/stop", "/ordre")):
        store.log_event(user_id, True, "command", text, None, True)
        return Reply(_declaration(text), True)
    if text.startswith("/test"):
        store.log_event(user_id, True, "command", text, None, True)
        return Reply("__TEST__", True)
    store.log_event(user_id, True, "message", text, None, False)
    return Reply(f"Commande inconnue. Mode courant : {store.current_mode()} — /help", True)


def _declaration(text: str) -> str:
    """``/exec <pea|cto_cash|cto_srd> <ISIN> <qty> <prix>``, ``/stop <position_id> <niveau> <qty> [seuil|plage]``,
    ``/ordre <compte> <ISIN> <qty> <limite|plage> [prix]``. Pure parsing; the app's ``declare`` hook writes."""
    parts = text.split()
    try:
        if parts[0] == "/exec":
            _, account, isin, qty, price = parts[:5]
            return _DECLARE(
                "execution",
                {"account": account, "isin": isin.upper(), "qty": int(qty), "price": float(price.replace(",", "."))},
            )
        if parts[0] == "/stop":
            _, pid, level, qty = parts[:4]
            otype = parts[4] if len(parts) > 4 else "seuil"
            return _DECLARE(
                "protection",
                {
                    "position_id": int(pid),
                    "level": float(level.replace(",", ".")),
                    "qty_covered": int(qty),
                    "order_type": otype,
                },
            )
        if parts[0] == "/ordre":
            _, account, isin, qty, otype = parts[:5]
            price = float(parts[5].replace(",", ".")) if len(parts) > 5 else None
            return _DECLARE(
                "order",
                {"account": account, "isin": isin.upper(), "qty": int(qty), "order_type": otype, "limit_price": price},
            )
    except (ValueError, IndexError):
        pass
    return (
        "Usage :\n/exec <pea|cto_cash|cto_srd> <ISIN> <qté> <prix>\n"
        "/stop <position_id> <niveau> <qté> [seuil|plage]\n"
        "/ordre <compte> <ISIN> <qté> <limite|plage> [prix]"
    )


DECLARE_HOOK: dict[str, Callable[[str, dict], str]] = {}


def _DECLARE(kind: str, payload: dict) -> str:
    hook = DECLARE_HOOK.get("fn")
    if hook is None:
        return f"déclaration {kind} reçue : {payload} (non enregistrée : application non démarrée)"
    return hook(kind, payload)


def handle_callback(data: str, user_id: int, allowed_user_id: int, store: ModeStore) -> Reply:
    auth = is_authorised(user_id, allowed_user_id)
    store.log_event(user_id, auth, "callback", None, data, auth)
    if not auth:
        return Reply("", False)
    label = next((b.label for b in STANDARD_BUTTONS if data.endswith(b.data)), data)
    return Reply(f"Enregistré : {label}", True)


def build_keyboard(buttons: list[Button], prefix: str = ""):  # type: ignore[no-untyped-def]
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    row: list[InlineKeyboardButton] = []
    for b in buttons:
        row.append(InlineKeyboardButton(b.label, callback_data=f"{prefix}{b.data}"[:64]))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


class TelegramBot:
    def __init__(self, token: str, allowed_user_id: int, store: ModeStore) -> None:
        self.token = token
        self.allowed_user_id = allowed_user_id
        self.store = store
        self._app = None

    async def start(self) -> None:
        from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

        self._app = Application.builder().token(self.token).build()
        self._app.add_handler(MessageHandler(filters.TEXT, self._on_text))
        self._app.add_handler(CallbackQueryHandler(self._on_callback))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=False)
        log.info("Telegram bot polling started")

    async def stop(self) -> None:
        if self._app is None:
            return
        await self._app.updater.stop()
        await self._app.stop()
        await self._app.shutdown()

    async def _on_text(self, update, context) -> None:  # type: ignore[no-untyped-def]
        uid = update.effective_user.id if update.effective_user else None
        reply = handle_text(update.message.text or "", uid or 0, self.allowed_user_id, self.store)
        if not reply.handled:
            return
        if reply.text == "__TEST__":
            await self.send_event(
                Event(
                    "P3",
                    "test",
                    "Message de test",
                    ["Cliquez un bouton : le clic est enregistré en base."],
                    STANDARD_BUTTONS,
                )
            )
            return
        await update.message.reply_text(reply.text)

    async def _on_callback(self, update, context) -> None:  # type: ignore[no-untyped-def]
        q = update.callback_query
        uid = q.from_user.id if q and q.from_user else 0
        reply = handle_callback(q.data or "", uid, self.allowed_user_id, self.store)
        if reply.handled:
            await q.answer(reply.text)
        else:
            await q.answer()

    async def send_event(self, event: Event) -> None:
        assert self._app is not None
        kb = build_keyboard(event.buttons, prefix=f"{event.kind}:") if event.buttons else None
        await self._app.bot.send_message(chat_id=self.allowed_user_id, text=event.telegram_text(), reply_markup=kb)

    def send_event_sync(self, event: Event) -> None:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(self.send_event(event), loop)
        else:
            loop.run_until_complete(self.send_event(event))


def telegram_settings() -> tuple[str, int] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    uid = os.environ.get("TELEGRAM_USER_ID")
    if not token or not uid:
        return None
    return token, int(uid)


async def send_once(token: str, chat_id: int, event: Event) -> None:
    """One-shot send (CLI test) without polling."""
    from telegram import Bot

    kb = build_keyboard(event.buttons, prefix=f"{event.kind}:") if event.buttons else None
    async with Bot(token) as bot:
        await bot.send_message(chat_id=chat_id, text=event.telegram_text(), reply_markup=kb)
