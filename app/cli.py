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
        "database": [],  # composed below (host, network, db, user, password → DATABASE_URL)
        "saxo": ["SAXO_APP_KEY", "SAXO_APP_SECRET"],
        "eodhd": ["EODHD_API_TOKEN"],
        "telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_USER_ID"],
        "anthropic": ["ANTHROPIC_API_KEY"],
        "imap": ["SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO", "IMAP_USER", "IMAP_PASSWORD"],
        "healthchecks": ["HEALTHCHECKS_PING_KEY"],
    }
    if args.group not in groups:
        print("groupes possibles : " + ", ".join(groups))
        return 2
    keys = groups[args.group]
    if args.group == "database":
        if current("DATABASE_URL") and "CHANGE_ME" not in current("DATABASE_URL") and not args.force:
            print("DATABASE_URL : déjà renseigné (utiliser --force pour remplacer)")
            return 0
        host = input("Nom du conteneur Postgres existant (docker ps) [postgres] : ").strip() or "postgres"
        network = input("Réseau Docker de ce conteneur (docker network ls) [postgres_default] : ").strip() or (
            "postgres_default"
        )
        network = docker_network_name(network)  # an id typed instead of the name is translated
        db = input("Nom de la base à utiliser [bourse] : ").strip() or "bourse"
        user = input("Rôle Postgres dédié [bourse] : ").strip() or "bourse"
        password = getpass.getpass("Mot de passe de ce rôle (saisie masquée, Entrée pour en générer un) : ").strip()
        if not password:
            import secrets

            password = secrets.token_urlsafe(24)
            print("mot de passe généré (jamais affiché) ; il sera aussi utilisé dans la commande SQL ci-dessous")
        put("DATABASE_URL", database_url(host, db, user, password))
        put("POSTGRES_NETWORK", network)
        env.write_text(text, encoding="utf-8")
        os.chmod(env, 0o600) if os.name != "nt" else None
        print(".env mis à jour (DATABASE_URL, POSTGRES_NETWORK).")
        answer = input(f"Créer maintenant le rôle et la base dans le conteneur {host} ? [O/n] ").strip() or "o"
        if answer.lower() == "o":
            return create_database(host, db, user, password)
        print("Création reportée : relancer `env-setup database --force`, ou exécuter à la main :")
        print(
            f"  docker exec -i {host} psql -U postgres -c \"CREATE ROLE {user} LOGIN PASSWORD '<mot de passe de .env>'\""  # noqa: E501
        )
        print(f'  docker exec -i {host} psql -U postgres -c "CREATE DATABASE {db} OWNER {user}"')
        return 0
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


def set_env_value(text: str, key: str, value: str | None) -> str:
    """Set ``KEY=value`` in a ``.env`` text (replacing a commented ``# KEY=`` line too); ``None`` comments
    the line out so the default applies."""
    import re

    pattern = re.compile(rf"^(#\s*)?{re.escape(key)}=.*$", re.M)
    line = f"# {key}=" if value is None else f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(lambda _m: line, text, count=1)  # callable: backslashes in value are kept verbatim
    return text + ("" if text.endswith("\n") or not text else "\n") + line + "\n"


def cmd_env_set(args: argparse.Namespace) -> int:
    """Non-secret ``.env`` values without an editor: ``env-set BACKUP_HOST_DIR D:\\sauvegardes`` ;
    ``env-set BACKUP_HOST_DIR --unset`` restores the default. The value is echoed (use ``env-setup`` for secrets)."""
    import os
    from pathlib import Path

    env = Path(".env")
    if not env.exists():
        print(".env introuvable : lancer d'abord `env-setup <groupe>` à la racine du dossier")
        return 2
    key = args.key.strip().upper()
    if not key.replace("_", "").isalnum():
        print("nom de variable invalide")
        return 2
    if any(s in key for s in ("TOKEN", "SECRET", "PASSWORD", "KEY", "DATABASE_URL")):
        print(f"{key} est un secret : utiliser `env-setup` (saisie masquée)")
        return 2
    value = None if args.unset else (args.value or "").strip()
    if value is not None and not value:
        print("valeur manquante (ou --unset)")
        return 2
    if value is not None and value.startswith("-"):
        print('la valeur commence par « - » : vérifier la syntaxe (env-set CLE "valeur" ou env-set CLE --unset)')
        return 2
    if value is not None and key.endswith("_DIR"):
        value = value.replace("\\", "/").rstrip("/")  # Docker accepte C:/Users/... ; évite les échappements
        if not Path(value).is_dir():
            print(f"attention : le dossier {value} n'existe pas encore (Docker le créera au démarrage)")
    text = set_env_value(env.read_text(encoding="utf-8"), key, value)
    env.write_text(text, encoding="utf-8")
    os.chmod(env, 0o600) if os.name != "nt" else None
    print(f"{key} : {'remis par défaut' if value is None else value}")
    return 0


def database_url(host: str, db: str, user: str, password: str, port: int = 5432) -> str:
    """SQLAlchemy URL for the existing Postgres instance; the password is percent-encoded."""
    from urllib.parse import quote

    return f"postgresql+psycopg://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{db}"


def _docker(*args: str) -> str | None:
    """stdout of a ``docker`` command, ``None`` when docker is missing or the command fails."""
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        return None
    r = subprocess.run(["docker", *args], capture_output=True)  # noqa: S603 — fixed argv
    return r.stdout.decode("utf-8", "replace").strip() if r.returncode == 0 else None


def docker_network_name(value: str) -> str:
    """Compose needs the network *name*; translate an id (``c8927f481f45``) when docker can resolve it."""
    out = _docker("network", "inspect", "--format", "{{.Name}}", value)
    return out.splitlines()[0].strip() if out else value


def docker_postgres_superuser(container: str) -> str:
    """``POSTGRES_USER`` of the container's environment (the official image makes it the superuser),
    ``postgres`` otherwise."""
    out = _docker("inspect", "--format", "{{range .Config.Env}}{{println .}}{{end}}", container) or ""
    for line in out.splitlines():
        if line.startswith("POSTGRES_USER=") and line.split("=", 1)[1].strip():
            return line.split("=", 1)[1].strip()
    return "postgres"


def create_database(host: str, db: str, user: str, password: str, superuser: str | None = None) -> int:
    """Create the dedicated role and database in the existing Postgres container through ``docker exec``;
    the SQL (with the password) goes through stdin, never through the command line or the terminal."""
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        print("docker introuvable dans ce terminal : ouvrir un terminal où `docker ps` fonctionne")
        return 2
    superuser = superuser or docker_postgres_superuser(host)
    pw = password.replace("'", "''")
    sql = (
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{user}') THEN "
        f"CREATE ROLE {user} LOGIN PASSWORD '{pw}'; ELSE ALTER ROLE {user} WITH LOGIN PASSWORD '{pw}'; END IF; END $$;\n"  # noqa: E501
        f"SELECT 'CREATE DATABASE {db} OWNER {user}' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '{db}')\\gexec\n"  # noqa: E501
    )
    r = subprocess.run(  # noqa: S603 — fixed argv, SQL on stdin
        ["docker", "exec", "-i", host, "psql", "-U", superuser, "-v", "ON_ERROR_STOP=1", "-q"],
        input=sql.encode("utf-8"),
        capture_output=True,
    )
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").replace(pw, "******").strip()
        print(f"échec de la création (code {r.returncode}) : {err[:400]}")
        print(
            f"→ vérifier le nom du conteneur (`docker ps`) et le superutilisateur ({superuser}) ; relancer avec --force"
        )
        return 1
    print(f"rôle {user} et base {db} prêts dans {host} (via le superutilisateur {superuser} ; relançable sans risque)")
    return 0


def init_params(example: str, target: str, telegram_user_id: str, email: str, update: bool = False) -> bool:
    """Create ``config/params.yaml`` from the example and fill ``user.telegram_user_id`` / ``user.email``
    by line substitution (comments are kept). Returns False when the target already exists, unless
    ``update`` is set: then only those two ``user.*`` lines are rewritten in the existing file."""
    import re
    from pathlib import Path

    src, dst = Path(example), Path(target)
    if dst.exists() and not update:
        return False
    text = (dst if dst.exists() else src).read_text(encoding="utf-8")
    text = re.sub(r"^(\s*telegram_user_id:)\s*\S+", rf"\g<1> {int(telegram_user_id)}", text, count=1, flags=re.M)
    text = re.sub(r'^(\s*email:)\s*"[^"]*"', rf'\g<1> "{email}"', text, count=1, flags=re.M)
    if update:
        example_text = src.read_text(encoding="utf-8")
        text, added = merge_missing_jobs(example_text, text)
        if added:
            print("jobs ajoutés depuis params.example.yaml : " + ", ".join(added))
        text, blocks = merge_missing_blocks(example_text, text, ["workflow", "learning"])
        if blocks:
            print("blocs ajoutés depuis params.example.yaml : " + ", ".join(blocks))
        text, steps = merge_missing_workflow_steps(example_text, text)
        if steps:
            print("étapes de la journée ajoutées : " + ", ".join(steps))
    dst.write_text(text, encoding="utf-8")
    return True


def _jobs_block(text: str) -> tuple[int, int, dict[str, str]]:
    """(start, end) line indexes of the top-level ``jobs:`` block and its ``  name: {...}`` lines."""
    import re

    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if re.match(r"^jobs:", ln)), -1)
    if start < 0:
        return -1, -1, {}
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].strip() and not lines[i].startswith((" ", "\t", "#")):
            end = i
            break
    entries = {}
    for ln in lines[start + 1 : end]:
        m = re.match(r"^  ([A-Za-z0-9_]+):", ln)
        if m:
            entries[m.group(1)] = ln
    return start, end, entries


def merge_missing_workflow_steps(example: str, target: str) -> tuple[str, list[str]]:
    """Insert the example's ``workflow.steps`` lines whose key the target lacks, after the
    target's last step line. Existing steps are never modified."""
    import re

    step_re = re.compile(r"^    - \{key: ([A-Za-z0-9_]+),")
    have = {m.group(1) for ln in target.splitlines() if (m := step_re.match(ln))}
    missing = [(m.group(1), ln) for ln in example.splitlines() if (m := step_re.match(ln)) and m.group(1) not in have]
    if not missing or not have:
        return target, []
    lines = target.splitlines(keepends=True)
    last = max(i for i, ln in enumerate(lines) if step_re.match(ln))
    lines[last + 1 : last + 1] = [ln + "\n" for _, ln in missing]
    return "".join(lines), [k for k, _ in missing]


def merge_missing_blocks(example: str, target: str, keys: list[str]) -> tuple[str, list[str]]:
    """Append top-level blocks (``workflow:`` …) of the example that the target lacks, verbatim
    with their comments. Existing blocks are never modified."""
    import re

    ex_lines = example.splitlines(keepends=True)
    added: list[str] = []
    for key in keys:
        if re.search(rf"^{re.escape(key)}:", target, re.M):
            continue
        start = next((i for i, ln in enumerate(ex_lines) if re.match(rf"^{re.escape(key)}:", ln)), None)
        if start is None:
            continue
        end = len(ex_lines)
        for i in range(start + 1, len(ex_lines)):
            if ex_lines[i].strip() and not ex_lines[i].startswith((" ", "\t", "#")):
                end = i
                break
        block = "".join(ex_lines[start:end]).rstrip() + "\n"
        target = target.rstrip("\n") + "\n\n" + block
        added.append(key)
    return target, added


def merge_missing_jobs(example: str, target: str) -> tuple[str, list[str]]:
    """Append to the target's ``jobs:`` block the job lines of the example that it lacks (a job
    added to the example after the target was created, e.g. ``saxo_token_keepalive``). Existing
    lines and comments are untouched; a multi-line job (``news_scan``) is only copied when its
    first line is missing, with its indented continuation lines."""
    import re

    es, ee, ex_entries = _jobs_block(example)
    ts, te, t_entries = _jobs_block(target)
    if es < 0 or ts < 0:
        return target, []
    ex_lines = example.splitlines(keepends=True)[es + 1 : ee]
    to_add: list[str] = []
    added: list[str] = []
    i = 0
    while i < len(ex_lines):
        ln = ex_lines[i]
        m = re.match(r"^  ([A-Za-z0-9_]+):", ln)
        if m and m.group(1) not in t_entries:
            block = [ln]
            j = i + 1
            while j < len(ex_lines) and (ex_lines[j].startswith("    ") or not ex_lines[j].strip()):
                if ex_lines[j].strip():
                    block.append(ex_lines[j])
                j += 1
            to_add.extend(block)
            added.append(m.group(1))
            i = j
            continue
        i += 1
    if not to_add:
        return target, []
    t_lines = target.splitlines(keepends=True)
    insert_at = te
    while insert_at > ts + 1 and not t_lines[insert_at - 1].strip():
        insert_at -= 1  # keep the blank line(s) separating the next top-level key
    if t_lines and not t_lines[insert_at - 1].endswith("\n"):
        t_lines[insert_at - 1] += "\n"
    t_lines[insert_at:insert_at] = to_add
    return "".join(t_lines), added


def cmd_params_init(args: argparse.Namespace) -> int:
    """``config/params.yaml`` from ``params.example.yaml`` with the user's Telegram id (from .env) and e-mail."""
    import os

    uid = os.environ.get("TELEGRAM_USER_ID", "0")
    if uid in ("", "0"):
        print("TELEGRAM_USER_ID absent de .env : lancer d'abord `env-setup telegram`")
        return 2
    email = args.email or input("Adresse e-mail (rapports, Cloudflare Access) : ").strip()
    if "@" not in email:
        print("adresse e-mail invalide")
        return 2
    if not init_params("config/params.example.yaml", "config/params.yaml", uid, email, update=args.update):
        print("config/params.yaml existe déjà : rien n'est modifié (relancer avec --update pour corriger user.*)")
        return 0
    verb = "mis à jour" if args.update else "créé"
    print(
        f"config/params.yaml {verb} (user.telegram_user_id et user.email renseignés) ; vérifier availability.schedule"
    )
    return cmd_check_config(argparse.Namespace(params="config/params.yaml"))


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
    TokenStore(live=not args.sim).save(data["refresh_token"], data.get("refresh_token_expires_in"))
    print("refresh token chiffré et enregistré ; accès en lecture seule uniquement.")
    print(f"→ vérifier la lecture du compte : uv run python -m app.cli saxo-check{' --sim' if args.sim else ''}")
    return 0


def cmd_saxo_check(args: argparse.Namespace) -> int:
    """Read-only check: clients/me, balances, positions, open orders. Writes SAXO_CLIENT_KEY into .env (live)."""
    import os
    import re
    from pathlib import Path

    from app.data.base import ProviderError
    from app.data.providers.saxo import SaxoClient, SaxoPortfolio, TokenStore

    live = not args.sim
    store = TokenStore(live=live)
    legacy = Path("data/saxo_token.enc")
    if not live and not Path(store.path).exists() and legacy.exists():
        legacy.rename(store.path)  # token saved by an earlier `saxo-auth --sim`
    client = SaxoClient(live=live, token_store=store)
    try:
        me = client.me()
    except ProviderError as exc:
        print(f"échec : {exc}")
        print("→ relancer l'autorisation : uv run python -m app.cli saxo-auth" + (" --sim" if args.sim else ""))
        return 1
    client_key = str(me.get("ClientKey", ""))
    print(
        f"environnement : {'SIMULATION' if args.sim else 'LIVE'} · client {me.get('Name', '?')} · ClientKey …{client_key[-6:]}"  # noqa: E501
    )
    pf = SaxoPortfolio(client, client_key)
    cash = pf.cash()
    positions = pf.positions()
    orders = pf.orders()
    print(
        f"cash : {cash if cash is not None else 'indisponible'} · positions : {len(positions)} · ordres en attente : {len(orders)}"  # noqa: E501
    )
    for p in positions[:10]:
        print(f"  {p.symbol:14} {p.isin or 'non suivie':12} qty {p.qty:>6} @ {p.open_price:<10.4g} {p.currency}")
    for o in orders[:10]:
        print(f"  ordre {o.order_id} {o.side} {o.qty} {o.isin or o.uic} {o.order_type} {o.price or ''} {o.status}")
    for uic, why in pf.isin_errors.items():
        print(f"  non suivie (Uic {uic}) : {why}")
    if pf.isin_errors:
        print("  (Saxo ne fournit pas l'ISIN ; correspondance par ticker + place via le référentiel — ADR-006)")
    env = Path(".env")
    if env.exists() and client_key and live:
        text = env.read_text(encoding="utf-8")
        line = f"SAXO_CLIENT_KEY={client_key}"
        if re.search(r"^SAXO_CLIENT_KEY=", text, re.M):
            text = re.sub(r"^SAXO_CLIENT_KEY=.*$", line, text, flags=re.M)
        else:
            text += f"\n{line}\n"
        env.write_text(text, encoding="utf-8")
        print("SAXO_CLIENT_KEY enregistré dans .env")
    else:
        print("(simulation : SAXO_CLIENT_KEY sera enregistré avec le compte live)")
    print("appels effectués, tous en GET : " + ", ".join(p for _, p in client.calls))
    return 0 if os.environ.get("SAXO_TOKEN_KEY") else 2


def cmd_backfill(args: argparse.Namespace) -> int:
    """``backfill --provider eodhd --from 2016-01-01 [--isin FR…]`` : EOD history for the referential."""
    import os

    from app.data.providers.eodhd import EodhdProvider
    from app.scheduler.jobs.eod_backfill_check import backfill_history

    if args.provider != "eodhd":
        print("seul le provider eodhd est retenu pour l'EOD (ADR-002)")
        return 2
    if not os.environ.get("EODHD_API_TOKEN"):
        print(
            "EODHD_API_TOKEN absent : lancer `env-setup eodhd` puis recréer le conteneur (docker compose up -d --force-recreate app)"  # noqa: E501
        )  # noqa: E501
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
    from app.env import load_env

    load_env()  # .env at the repo root; never overrides variables already set by Docker/the shell
    import logging
    import os

    # human-readable job logs on the console (the app itself logs JSON); a skipped job says why
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s : %(message)s")
    for noisy in ("httpx", "httpcore", "apscheduler", "urllib3"):
        logging.getLogger(noisy).setLevel("WARNING")
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
    pi = sub.add_parser("params-init", help="créer config/params.yaml depuis l'exemple (user.* renseignés)")
    pi.add_argument("--email", default=None)
    pi.add_argument(
        "--update", action="store_true", help="réécrire user.telegram_user_id / user.email d'un fichier existant"
    )
    pi.set_defaults(fn=cmd_params_init)
    ev = sub.add_parser("env-set", help="régler une variable non secrète de .env (ex. BACKUP_HOST_DIR)")
    ev.add_argument("key")
    ev.add_argument("value", nargs="?", default=None)
    ev.add_argument("--unset", action="store_true", help="commenter la ligne (valeur par défaut)")
    ev.set_defaults(fn=cmd_env_set)
    es = sub.add_parser("env-setup", help="créer/compléter .env de façon interactive (saisie masquée)")
    es.add_argument("group", help="database | saxo | eodhd | telegram | anthropic | imap | healthchecks")
    es.add_argument("--force", action="store_true", help="remplacer une valeur déjà renseignée")
    es.set_defaults(fn=cmd_env_setup)
    sc = sub.add_parser("saxo-check", help="lecture seule : clients/me, cash, positions, ordres")
    sc.add_argument("--sim", action="store_true")
    sc.set_defaults(fn=cmd_saxo_check)
    sa = sub.add_parser("saxo-auth")
    sa.add_argument("--sim", action="store_true", help="environnement de simulation (aucune donnée de marché)")
    sa.set_defaults(fn=cmd_saxo_auth)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
