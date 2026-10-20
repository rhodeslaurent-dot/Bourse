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


def cmd_env_setup(args: argparse.Namespace) -> int:
    """Create/complete ``.env`` interactively (values are never echoed nor printed)."""
    import getpass
    import os
    import re
    import shutil
    from pathlib import Path

    env = Path(".env")
    if not env.exists():
        if not Path(".env.example").exists():
            print("lancer la commande à la racine du dossier Bourse (.env.example introuvable)")
            return 2
        shutil.copy(".env.example", env)
        print(".env créé à partir de .env.example")
    text = env.read_text(encoding="utf-8")

    def current(key: str) -> str:
        m = re.search(rf"^{key}=([^\n#]*)", text, re.M)
        return m.group(1).strip() if m else ""

    def put(key: str, value: str) -> None:
        nonlocal text
        line = f"{key}={value}"
        if re.search(rf"^{key}=.*$", text, re.M):
            text = re.sub(rf"^{key}=.*$", line, text, count=1, flags=re.M)
        else:
            text += f"\n{line}\n"

    groups = {
        "saxo": ["SAXO_APP_KEY", "SAXO_APP_SECRET"],
        "eodhd": ["EODHD_API_TOKEN"],
        "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID"],
        "anthropic": ["ANTHROPIC_API_KEY"],
        "imap": ["SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO", "IMAP_USER", "IMAP_PASSWORD"],
        "healthchecks": ["HEALTHCHECKS_PING_KEY"],
    }
    keys = groups.get(args.group, [])
    if not keys:
        print("groupes possibles : " + ", ".join(groups))
        return 2
    for key in keys:
        if current(key) and not args.force:
            print(f"{key} : déjà renseigné (utiliser --force pour remplacer)")
            continue
        value = getpass.getpass(f"{key} (saisie masquée, Entrée pour ignorer) : ").strip()
        if value:
            put(key, value)
    if args.group == "saxo":
        if not current("SAXO_TOKEN_KEY") or args.force:
            from cryptography.fernet import Fernet

            put("SAXO_TOKEN_KEY", Fernet.generate_key().decode())
            print("SAXO_TOKEN_KEY : nouvelle clé générée")
        if not current("SAXO_REDIRECT_URI"):
            put("SAXO_REDIRECT_URI", "http://localhost/callback")
    env.write_text(text, encoding="utf-8")
    os.chmod(env, 0o600) if os.name != "nt" else None
    print(".env mis à jour (aucune valeur affichée).")
    return 0


def _wait_for_code(redirect_uri: str, timeout_s: int = 300) -> str | None:
    """Listen on the redirect URI (localhost only) and capture ``?code=`` automatically.
    Falls back to manual paste when the port cannot be bound or the host is not localhost."""
    import urllib.parse
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    u = urllib.parse.urlparse(redirect_uri)
    if u.hostname not in ("localhost", "127.0.0.1"):
        return None
    port = u.port or 80
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q:
                captured["code"] = q["code"][0]
                body = (
                    "<h2>Autorisation reçue — vous pouvez fermer cette page et revenir à la fenêtre de commande.</h2>"
                )
            else:
                body = "<h2>Paramètre code absent : recommencer l'autorisation.</h2>"
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: object) -> None:  # silence; never log the code
            return

    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        print(f"(port {port} indisponible : collez le code manuellement)")
        return None
    server.timeout = timeout_s
    print(f"2) En attente de la redirection sur {redirect_uri} (≤ {timeout_s // 60} min)…")
    try:
        webbrowser.open_new_tab  # noqa: B018 — the user may open the URL from another device
        server.handle_request()
    finally:
        server.server_close()
    return captured.get("code")


def cmd_saxo_auth(args: argparse.Namespace) -> int:
    """OAuth Authorization Code (read-only app): print the URL, paste the code, store the refresh token encrypted."""
    import os
    import urllib.parse

    import httpx

    from app.data.providers.saxo import LIVE_AUTH, SIM_AUTH, TokenStore

    app_key = os.environ.get("SAXO_APP_KEY")
    secret = os.environ.get("SAXO_APP_SECRET")
    redirect = os.environ.get("SAXO_REDIRECT_URI", "http://localhost/callback")
    missing = [k for k in ("SAXO_APP_KEY", "SAXO_APP_SECRET", "SAXO_TOKEN_KEY") if not os.environ.get(k)]
    if missing:
        print(f"variables manquantes dans .env : {', '.join(missing)}")
        print("→ ouvrir .env à la racine du dossier Bourse et renseigner ces lignes (voir .env.example)")
        print("  SAXO_TOKEN_KEY se génère avec :")
        print('  uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"')
        return 2
    base = SIM_AUTH if args.sim else LIVE_AUTH
    url = f"{base}/authorize?" + urllib.parse.urlencode(
        {"response_type": "code", "client_id": app_key, "redirect_uri": redirect, "state": "bourse-pilot"}
    )
    print("1) Ouvrir dans le navigateur :\n" + url)
    code = _wait_for_code(redirect) or input("2) Coller le paramètre `code` de l'URL de retour : ").strip()
    r = httpx.post(
        f"{base}/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect},
        auth=(app_key, secret),
        timeout=30,
    )
    if r.status_code >= 400:
        print(f"échec : {r.status_code} {r.text[:200]}")
        return 1
    data = r.json()
    TokenStore().save(data["refresh_token"])
    print("refresh token chiffré et enregistré ; accès en lecture seule uniquement.")
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    """``backfill --provider eodhd --from 2016-01-01 [--isin FR…]`` : EOD history for the referential."""
    from app.data.providers.eodhd import EodhdProvider
    from app.scheduler.jobs.eod_backfill_check import backfill_history

    if args.provider != "eodhd":
        print("seul le provider eodhd est retenu pour l'EOD (ADR-002)")
        return 2
    start = date.fromisoformat(args.from_date)
    end = date.fromisoformat(args.to) if args.to else date.today()
    n = backfill_history(EodhdProvider(), start, end, args.isin.split(",") if args.isin else None)
    print(f"{n} barres EOD insérées/mises à jour")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    from alembic.config import Config

    from alembic import command

    command.upgrade(Config("alembic.ini"), "head")
    return 0


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()  # .env at the repo root; never overrides variables already set by Docker/the shell
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
    bf = sub.add_parser("backfill")
    bf.add_argument("--provider", default="eodhd")
    bf.add_argument("--from", dest="from_date", required=True)
    bf.add_argument("--to", default=None)
    bf.add_argument("--isin", default=None, help="liste d'ISIN séparés par des virgules (défaut : tout le référentiel)")
    bf.set_defaults(fn=cmd_backfill)
    es = sub.add_parser("env-setup", help="créer/compléter .env de façon interactive (saisie masquée)")
    es.add_argument("group", help="saxo | eodhd | telegram | anthropic | imap | healthchecks")
    es.add_argument("--force", action="store_true", help="remplacer une valeur déjà renseignée")
    es.set_defaults(fn=cmd_env_setup)
    sa = sub.add_parser("saxo-auth")
    sa.add_argument("--sim", action="store_true", help="environnement de simulation (aucune donnée de marché)")
    sa.set_defaults(fn=cmd_saxo_auth)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
