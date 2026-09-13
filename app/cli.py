"""Command line: ``uv run python -m app.cli <command>``."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date

from app.config import ConfigError, load_params


def cmd_check_config(args: argparse.Namespace) -> int:
    try:
        cfg = load_params(args.params)
    except ConfigError as exc:
        print(f"REFUS DE DÉMARRER : {exc}")
        return 2
    print(f"OK params v{cfg.params.version} (sha {cfg.sha256[:12]})")
    for f in cfg.features:
        print(f"  {'✅' if f.enabled else '⛔'} {f.label} [{f.key}]{'' if f.enabled else ' — ' + f.reason}")
    return 0


def cmd_seed_calendar(args: argparse.Namespace) -> int:
    from app.db.repo import seed_market_calendar, seed_srd_calendar
    from app.db.session import db_session
    from app.domain.calendar import SrdCalendar, load_market_calendars

    cfg = load_params(args.params)
    cals = load_market_calendars(args.calendars)
    with db_session() as s:
        n = seed_market_calendar(s, cals, [int(y) for y in args.years.split(",")])
        m = 0
        if cfg.params.srd:
            srd = SrdCalendar.from_params(cfg.params.srd.calendar_2026, cfg.params.srd.calendar_2027_provisional)
            m = seed_srd_calendar(s, srd, "params.yaml › srd (Boursorama/abcbourse/Bourse Direct/Saxo)")
    print(f"market_calendar: {n} lignes ajoutées ; srd_calendar: {m} lignes ajoutées")
    return 0


def cmd_run_job(args: argparse.Namespace) -> int:
    from app.domain.calendar import load_market_calendars
    from app.scheduler.runner import execute_job

    cfg = load_params(args.params)
    cals = load_market_calendars(args.calendars)
    run_date = date.fromisoformat(args.date) if args.date else None
    run = execute_job(
        args.job, cfg, cals, run_date=run_date, variant=args.variant, mics=args.mics.split(",") if args.mics else None
    )
    print(f"{args.job}: {run.status} rows={run.rows} note={run.note} error={(run.error or '')[:200]}")
    return 0 if run.status in ("ok", "skipped") else 1


def cmd_test_telegram(args: argparse.Namespace) -> int:
    from app.notify.base import Event
    from app.notify.telegram import STANDARD_BUTTONS, send_once, telegram_settings

    tg = telegram_settings()
    if tg is None:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_USER_ID absents de l'environnement")
        return 2
    ev = Event(
        "P3",
        "test",
        "Message de test BOURSE-PILOT",
        ["Aucun ordre n'est jamais passé.", "Cliquez un bouton."],
        STANDARD_BUTTONS,
    )
    asyncio.run(send_once(tg[0], tg[1], ev))
    print("envoyé")
    return 0


def cmd_test_email(args: argparse.Namespace) -> int:
    from app.notify.email import send_email

    send_email("[BOURSE-PILOT] e-mail de test", "Si vous lisez ceci, le SMTP fonctionne.")
    print("envoyé")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config("alembic.ini"), "head")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bourse-pilot")
    p.add_argument("--params", default=None, help="chemin de params.yaml (défaut: $PARAMS_PATH ou config/params.yaml)")
    p.add_argument("--calendars", default="config/market_calendars.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check-config").set_defaults(fn=cmd_check_config)
    sc = sub.add_parser("seed-calendar")
    sc.add_argument("--years", default="2026,2027")
    sc.set_defaults(fn=cmd_seed_calendar)
    rj = sub.add_parser("run-job")
    rj.add_argument("job")
    rj.add_argument("--date", default=None)
    rj.add_argument("--variant", default="normal")
    rj.add_argument("--mics", default=None, help="places concernées, ex. XPAR,XETR")
    rj.set_defaults(fn=cmd_run_job)
    sub.add_parser("test-telegram").set_defaults(fn=cmd_test_telegram)
    sub.add_parser("test-email").set_defaults(fn=cmd_test_email)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
