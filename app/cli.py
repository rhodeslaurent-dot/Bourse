"""Command line: ``uv run python -m app.cli <command>``."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta

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
        "zonebourse": ["ZONEBOURSE_COOKIE"],
        "cloudflare": ["CLOUDFLARE_TUNNEL_TOKEN", "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "CF_ACCESS_EMAIL"],
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


def init_params(
    example: str, target: str, telegram_user_id: str, email: str, update: bool = False, reset: list[str] | None = None
) -> bool:
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
        text, blocks = merge_missing_blocks(example_text, text, NEW_TOP_BLOCKS)
        if blocks:
            print("blocs ajoutés depuis params.example.yaml : " + ", ".join(blocks))
        for parent in NEW_CHILD_PARENTS:
            text, children = merge_missing_children(example_text, text, parent)
            if children:
                print(f"clés ajoutées sous {parent} : " + ", ".join(children))
        text, steps = merge_missing_workflow_steps(example_text, text)
        if steps:
            print("étapes de la journée ajoutées : " + ", ".join(steps))
        for path in reset or []:
            text, done = reset_block(example_text, text, path)
            print(f"bloc {path} remplacé par celui de l'exemple" if done else f"bloc {path} introuvable : inchangé")
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


# Blocks / keys added to params.example.yaml after a user's params.yaml was created: copied
# verbatim (with their comments) by ``params-init --update``; existing values are never touched.
NEW_TOP_BLOCKS = [
    "workflow",
    "learning",
    "letters_review",
    "admission",  # docs/07 §7.9 — référentiel de gestion du 22/10/2026
    "market_gate",
    "conjuncture",
    "events",
    "entry",
    "zones",
    "ranking",
    "pockets",
    "crypto",
    "derivatives",
    "media",  # docs/23 §23.7
    "weak_signals",  # docs/26
    "committee",  # docs/24 v2
]
NEW_CHILD_PARENTS = [
    "risk",
    "risk.stop",
    "risk.circuit_breaker",  # rolling windows and peak give-back (25/10/2026)
    "risk.risk_dial",  # docs/28
    "llm",
    "srd",
    "data",
    "data.imap",
    "events",
    "letters_review",
    "detectors",
    "scoring",
    "scoring.admission_blocks",
]


def _block_range(lines: list[str], path: str) -> tuple[int, int, int]:
    """(start, end, indent) of the mapping at dotted ``path`` (2-space YAML); (-1, -1, 0) if absent."""
    import re

    lo, hi, indent = 0, len(lines), 0
    start = -1
    for seg in path.split("."):
        pat = re.compile(rf"^{' ' * indent}{re.escape(seg)}:")
        start = next((i for i in range(lo, hi) if pat.match(lines[i])), -1)
        if start < 0:
            return -1, -1, 0
        end = hi
        for i in range(start + 1, hi):
            ln = lines[i]
            if ln.strip() and not ln.lstrip().startswith("#") and len(ln) - len(ln.lstrip(" ")) <= indent:
                end = i
                break
        lo, hi, indent = start + 1, end, indent + 2
    return start, hi, indent


def merge_missing_children(example: str, target: str, path: str) -> tuple[str, list[str]]:
    """Append to the target's mapping at ``path`` (e.g. ``risk.stop``) the direct child keys of the
    example that it lacks, with their indented continuation lines. Existing keys are untouched."""
    import re

    ex = example.splitlines(keepends=True)
    tg = target.splitlines(keepends=True)
    es, ee, ind = _block_range(ex, path)
    ts, te, _ = _block_range(tg, path)
    if es < 0 or ts < 0:
        return target, []
    child = re.compile(rf"^{' ' * ind}([A-Za-z0-9_]+):")
    have = {m.group(1) for ln in tg[ts + 1 : te] if (m := child.match(ln))}
    add: list[str] = []
    added: list[str] = []
    i = es + 1
    while i < ee:
        m = child.match(ex[i])
        if not m:
            i += 1
            continue
        j = i + 1
        while j < ee and (not ex[j].strip() or len(ex[j]) - len(ex[j].lstrip(" ")) > ind) and not child.match(ex[j]):
            j += 1
        if m.group(1) not in have:
            add.extend(ln if ln.endswith("\n") else ln + "\n" for ln in ex[i:j] if ln.strip())
            added.append(m.group(1))
        i = j
    if not add:
        return target, []
    insert_at = te
    while insert_at > ts + 1 and not tg[insert_at - 1].strip():
        insert_at -= 1
    if insert_at > 0 and not tg[insert_at - 1].endswith("\n"):
        tg[insert_at - 1] += "\n"
    return "".join(tg[:insert_at] + add + tg[insert_at:]), added


def reset_block(example: str, target: str, path: str) -> tuple[str, bool]:
    """Replace the mapping at dotted ``path`` (e.g. ``data.macro``) of the target by the example's,
    verbatim. Used only on explicit request (``params-init --update --reset data.macro``) when the
    example's version of a block must replace an older copy (other blocks are untouched)."""
    ex = example.splitlines(keepends=True)
    tg = target.splitlines(keepends=True)
    es, ee, _ = _block_range(ex, path)
    ts, te, _ = _block_range(tg, path)
    if es < 0 or ts < 0:
        return target, False
    while ee > es + 1 and not ex[ee - 1].strip():
        ee -= 1
    while te > ts + 1 and not tg[te - 1].strip():
        te -= 1
    block = [ln if ln.endswith("\n") else ln + "\n" for ln in ex[es:ee]]
    return "".join(tg[:ts] + block + tg[te:]), True


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
    if not init_params(
        "config/params.example.yaml", "config/params.yaml", uid, email, update=args.update, reset=args.reset
    ):
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


def ingest_known(s, mail) -> bool:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    from app.db.models import NewsletterItem

    return s.scalar(select(NewsletterItem.id).where(NewsletterItem.dedup_hash == mail.dedup_hash)) is not None


def cmd_archive_mentions(args: argparse.Namespace) -> int:
    """Pending decision (docs/15-status): soft-archive (``deleted_at``) the proposals created from a
    mere *mention* (direction « neutral ») during the historical import of the letters. Dry run by
    default; nothing is deleted; ``--apply`` is required to archive."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.db.models import NewsletterValue, Proposal, Signal
    from app.db.session import db_session

    with db_session() as s:
        sig_ids = {
            v.signal_id
            for v in s.scalars(select(NewsletterValue).where(NewsletterValue.direction == "neutral"))
            if v.signal_id
        }
        props = (
            [
                p
                for p in s.scalars(
                    select(Proposal).where(Proposal.signal_id.in_(sig_ids), Proposal.deleted_at.is_(None))
                )
                if (s.get(Signal, p.signal_id) or Signal()).detector == "external"
            ]
            if sig_ids
            else []
        )
        print(f"{len(props)} proposition(s) issues de simples mentions (sens « neutre »)")
        for p in props[:10]:
            print(f"  #{p.id} {p.isin} {p.created_at:%d/%m/%Y} statut {p.status}")
        if not args.apply:
            print("aperçu seulement : relancer avec --apply pour les archiver (réversible : deleted_at)")
            return 0
        now = datetime.now(UTC)
        for p in props:
            p.deleted_at = now
        print(f"{len(props)} proposition(s) archivée(s)")
    return 0


def cmd_ttf_list_load(args: argparse.Namespace) -> int:
    """docs/22 R11 — download the official BOFiP list of companies subject to the French TTF and
    write ``data/ttf_list.yaml`` (year, source, ISINs). Nothing is guessed: no ISIN found → error."""
    from datetime import UTC, datetime
    from pathlib import Path

    import yaml

    from app.data.providers import ttf_list

    if args.file:  # page saved from the browser (Ctrl+S) when the site refuses scripted downloads
        rows = ttf_list.parse(Path(args.file).read_text(encoding="utf-8", errors="replace"))
    else:
        try:
            rows = ttf_list.fetch(args.url)
        except Exception as exc:  # noqa: BLE001
            print(f"téléchargement impossible ({exc}) : ouvrir la page dans le navigateur, l'enregistrer (Ctrl+S)")
            print("puis relancer avec --file <fichier.html>")
            return 2
    if not rows:
        print("aucun ISIN trouvé : vérifier l'adresse (--url) ou le fichier (--file) de l'annexe BOI-ANNX-000467")
        return 1
    data = {
        "year": args.year,
        "source": args.file or args.url,
        "loaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "count": len(rows),
        "isins": [i for i, _ in rows],
        "names": {i: n for i, n in rows},
    }
    Path(args.out).write_text(
        "# Liste officielle TTF (BOFiP BOI-ANNX-000467) — générée par ttf-list-load, ne pas éditer à la main\n"
        + yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"{len(rows)} sociétés soumises à la TTF en {args.year} → {args.out}")
    return 0


def _feeds(cfg, name: str | None) -> list[dict]:  # type: ignore[no-untyped-def]
    return [f for f in cfg.params.data.rss_feeds if not name or f.get("name") == name]


def cmd_sources_discover(args: argparse.Namespace) -> int:
    """docs/23 §23.4 — list the feeds a site announces (homepage of the sources still « à renseigner »,
    or the given URL). Read-only; the address to keep is then copied into params.yaml."""
    import httpx

    from app.data.providers.feed_discovery import discover_feeds, podcast_feeds
    from app.data.providers.rss import BROWSER_UA

    podcasts: list[dict] = []
    if args.url:
        targets = [("(adresse donnée)", args.url)]
    else:
        cfg = load_params(args.params)
        todo = [f for f in _feeds(cfg, args.name) if args.name or "renseigner" in str(f.get("url", ""))]
        targets = [(str(f["name"]), str(f["homepage"])) for f in todo if f.get("homepage")]
        podcasts = [f for f in todo if f.get("apple_id") or f.get("itunes_term")]
    if not targets and not podcasts:
        print("aucune source à découvrir (toutes les adresses sont renseignées, ou --name inconnu)")
        return 1
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": BROWSER_UA}) as c:
        for f in podcasts:  # docs/23 §23.7: the podcast feed is read from Apple's public directory
            country = {"DE": "de", "NL": "nl", "BE": "be", "ES": "es", "IT": "it"}.get(str(f.get("market")), "fr")
            try:
                found = podcast_feeds(c, f.get("apple_id"), f.get("itunes_term"), country)
            except httpx.HTTPError as exc:
                print(f"\n{f['name']} — annuaire Apple inaccessible ({exc.__class__.__name__})")
                continue
            print(f"\n{f['name']} — podcast, annuaire Apple : {len(found)} flux trouvé(s)")
            for feed, label in found:
                print(f"  {feed}   [{label}]")
        for name, url in targets:
            try:
                r = c.get(url)
                found = discover_feeds(r.text, str(r.url)) if r.status_code < 400 else []
                print(f"\n{name} — {url} : HTTP {r.status_code}, {len(found)} flux trouvé(s)")
            except httpx.HTTPError as exc:
                print(f"\n{name} — {url} : inaccessible ({exc.__class__.__name__})")
                continue
            for feed, label in found:
                print(f"  {feed}   [{label}]")
    print("\nRecopier l'adresse retenue dans config/params.yaml (data.rss_feeds), puis lancer sources-check.")
    return 0


def cmd_sources_check(args: argparse.Namespace) -> int:
    """docs/23 §23.4 — read every configured feed once and report status, item count, newest date and
    first title. Nothing is written to the database."""
    from app.data.providers.rss import fetch_feed

    cfg = load_params(args.params)
    feeds = [f for f in _feeds(cfg, args.name) if f.get("url") and "renseigner" not in str(f.get("url"))]
    pending = [str(f["name"]) for f in _feeds(cfg, args.name) if "renseigner" in str(f.get("url", ""))]
    ok = 0
    for f in feeds:
        try:
            items = fetch_feed(str(f["name"]), str(f["url"]))
        except Exception as exc:  # noqa: BLE001 — a diagnostic: every failure is printed, none stops the others
            print(f"ÉCHEC  {f['name']:<22} {str(exc)[:100]}")
            continue
        dates = [i.published_at for i in items if i.published_at]
        newest = max(dates).strftime("%d/%m/%Y %H:%M") if dates else "sans date"
        first = items[0].title[:70] if items else "—"
        print(f"OK     {f['name']:<22} {len(items):>3} article(s), plus récent {newest} · {first}")
        ok += 1
    print(f"\n{ok}/{len(feeds)} flux lisible(s)" + (f" ; à renseigner : {', '.join(pending)}" if pending else ""))
    return 0 if ok == len(feeds) else 1


def cmd_tv_columns_check(args: argparse.Namespace) -> int:
    """docs/23 S4 — which analyst columns the TradingView screener accepts (largest caps of a market),
    with sample values; the accepted ones go into data.tradingview_screener.analyst_columns."""
    from app.data.providers.tradingview_screener import ANALYST_COLUMN_CANDIDATES, check_columns

    cols = [c.strip() for c in args.columns.split(",")] if args.columns else ANALYST_COLUMN_CANDIDATES
    res = check_columns(args.market, cols)
    for col, (ok, info) in res.items():
        print(f"{'OK    ' if ok else 'REFUS '} {col:<24} {info[:110]}")
    print(f"\n{sum(ok for ok, _ in res.values())}/{len(res)} colonne(s) acceptée(s) sur {args.market}")
    return 0


def cmd_shorts_check(args: argparse.Namespace) -> int:
    """docs/23 S2 — read one authority's short-position file (configured URL, given URL or local file) and
    show what is recognised; ``--store`` keeps it (manual load when the site refuses the download)."""
    from pathlib import Path

    from app.data.providers.short_positions import fetch, read_table, rows_from_table

    cfg = load_params(args.params)
    sources = ((getattr(cfg.params.data, "short_positions", None) or {}).get("sources")) or []
    src = next((x for x in sources if x.get("authority") == args.authority), {"authority": args.authority})
    try:
        if args.file:
            path = Path(args.file)
            rows, url = rows_from_table(read_table(path.read_bytes(), path.name)), f"fichier {path.name}"
        else:
            rows, url = fetch({**src, **({"url": args.url} if args.url else {})})
    except Exception as exc:  # noqa: BLE001 — a diagnostic
        print(f"{args.authority} : échec — {exc}")
        return 1
    latest: dict[tuple[str, str], object] = {}
    for r in rows:  # same rule as the cards: latest position of each holder, counted from 0.5 %
        k = (r.isin, r.holder)
        if k not in latest or r.position_date > latest[k].position_date:  # type: ignore[attr-defined]
            latest[k] = r
    by_isin: dict[str, tuple[float, int, str]] = {}
    for r in latest.values():
        if r.pct >= 0.005:  # type: ignore[attr-defined]
            tot, n, issuer = by_isin.get(r.isin, (0.0, 0, r.issuer or ""))  # type: ignore[attr-defined]
            by_isin[r.isin] = (tot + r.pct, n + 1, issuer)  # type: ignore[attr-defined]
    print(f"{args.authority} : {len(rows)} position(s) lue(s) depuis {url}")
    if rows:
        print(f"dates : {min(r.position_date for r in rows)} → {max(r.position_date for r in rows)}")
        print("positions courtes déclarées actuelles (dernière position de chaque détenteur ≥ 0,5 %) :")
        for isin, (pct, n, issuer) in sorted(by_isin.items(), key=lambda kv: -kv[1][0])[:5]:
            print(f"  {isin}  {issuer[:30]:<30} {pct * 100:.2f} % ({n} détenteur(s))")
    if args.store and rows:
        from app.db.session import db_session
        from app.services.short_positions import store

        with db_session() as s:
            print(f"{store(s, args.authority, rows, url)} position(s) nouvelle(s) enregistrée(s)")
    return 0


def cmd_exchange_list_load(args: argparse.Namespace) -> int:
    """docs/23 S3 — official share list of a venue EODHD does not carry (Milan): Euronext equities list
    downloaded from live.euronext.com (CSV or Excel), filtered on the market name, stored in
    data/exchange_lists/<code>.yaml for universe_refresh."""
    from pathlib import Path

    from app.data.providers.exchange_list import parse_list, save

    path = Path(args.file)
    try:
        shares = parse_list(path.read_bytes(), path.name, args.market)
    except Exception as exc:  # noqa: BLE001 — explained to the user
        print(f"liste refusée : {exc}")
        return 1
    if not shares:
        print(f"aucune action « {args.market} » dans {path.name} (vérifier --market)")
        return 1
    out = save(args.exchange, shares, f"{path.name} ({args.market or 'toutes places'})")
    print(f"{len(shares)} action(s) enregistrée(s) dans {out} ; exemples : " + ", ".join(s.name for s in shares[:5]))
    print("Lancer ensuite : run-job universe_refresh (historique Yahoo chargé pour les candidates).")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    """docs/27 — replay the detectors on the stored history at full cost; prints the production segments
    (admitted, no warning) and the run's known biases. Stored for the ranking prior and /apprentissage."""
    from app.db.session import db_session
    from app.services.backtest import run_backtest

    cfg = load_params(args.params)
    end = date.fromisoformat(args.to) if args.to else date.today() - timedelta(days=1)
    start = date.fromisoformat(args.from_date) if args.from_date else end - timedelta(days=365 * 8)
    dets = args.detectors.split(",") if args.detectors else None

    def progress(k: int, n: int) -> None:
        if k % 50 == 0 or k == n:
            print(f"  {k}/{n} valeurs rejouées", flush=True)

    with db_session() as s:
        run = run_backtest(s, cfg, start, end, None, dets, progress)
        seg = (run.summary or {}).get("segments", {})
        sm = run.summary or {}
        print(f"\nbacktest n° {run.id} : {start} → {end}, {run.instruments} valeurs, {sm.get('trades')} trades, "
              f"{sm.get('essais')} segments testés")  # fmt: skip
        print(f"{'segment':<38} {'n':>5} {'R net moy.':>10} {'médian':>7} {'gagnants':>8} {'t':>6} {'IC 95 %':>17}")
        for k, v in seg.items():
            if k.startswith(("production:", "periode:")):
                ci = f"[{v['ci_low']:+.2f} ; {v['ci_high']:+.2f}]" if v.get("ci_low") is not None else "—"
                t = f"{v['t_stat']:.1f}" if v.get("t_stat") is not None else "—"
                flag = "  ✓ t ≥ 3" if v.get("significant") else ""
                stats = f"{v['mean_r']:>+10.3f} {v['median_r']:>+7.2f} {v['hit_rate']:>8.0%}"
                print(f"{k:<38} {v['n']:>5} {stats} {t:>6} {ci:>17}{flag}")
        pf = sm.get("portefeuille") or {}
        if pf:
            print(f"\nPortefeuille rejoué ({pf.get('lignes_simultanees')} lignes simultanées, {pf.get('note')}) :")
            for name in ("tout", "controle"):
                v = pf.get(name)
                if not v:
                    continue
                for variant in ("sans_coupe_circuit", "avec_coupe_circuit"):
                    x = v[variant]
                    print(f"  {name:<9} {variant:<20} {x['taken']:>5} trades  {x['total_r']:+8.1f} R  "
                          f"R/an {x['r_per_year'] if x['r_per_year'] is not None else '—'}  "
                          f"recul max {x['max_drawdown_r']:+.1f} R")  # fmt: skip
                print(f"  {name:<9} garde-fou : {v.get('verdict_garde_fou') or '—'}")
        print("\nLimites :")
        for n in run.notes or []:
            print(f"  - {n}")
    return 0


def cmd_llm_costs(args: argparse.Namespace) -> int:
    """docs/25 L6 — cost of the Claude calls per purpose (same table as /sante)."""
    from app.db.session import db_session
    from app.services.llm_costs import cost_board

    with db_session() as s:
        b = cost_board(s)
        print(f"{'usage':<22} {'jour $':>8} {'7 j $':>8} {'mois $':>8} {'appels':>7} {'cache':>6} {'$/appel':>8}")
        for r in b.rows:
            cache = f"{r.cache_share:.0%}" if r.cache_share is not None else "—"
            per = f"{r.per_call:.4f}" if r.per_call is not None else "—"
            print(f"{r.purpose:<22} {r.cost_today:>8.3f} {r.cost_7d:>8.3f} {r.cost_month:>8.2f} "
                  f"{r.calls_month:>7} {cache:>6} {per:>8}")  # fmt: skip
        proj = f" · projection du mois {b.projected_month:.2f} $" if b.projected_month is not None else ""
        print(f"\ntotal : jour {b.today:.2f} $ · 7 jours {b.last_7d:.2f} $ · mois {b.month:.2f} ${proj}")
    return 0


def cmd_radar_check(args: argparse.Namespace) -> int:
    """docs/26 — Wikidata mapping and Wikipedia views of a few values, then the radar label of the day."""
    import httpx

    from app.db.session import db_session
    from app.services.weak_signals import compute_hits, refresh_articles, refresh_attention, settings

    cfg = load_params(args.params)
    isins = [x.strip() for x in args.isins.split(",") if x.strip()] if args.isins else None
    day = date.today() - timedelta(days=1)
    with httpx.Client(follow_redirects=True) as client, db_session() as s:
        n = refresh_articles(s, cfg, client, isins)
        print(f"{n} correspondance(s) Wikidata vérifiée(s)")
        from sqlalchemy import select

        from app.db.models import WikiArticle

        q = select(WikiArticle).order_by(WikiArticle.isin)
        if isins:
            q = q.where(WikiArticle.isin.in_(isins))
        arts = list(s.scalars(q))
        found = [a for a in arts if a.title]
        print(f"articles trouvés : {len(found)} / {len(arts)} (valeur × langue)")
        for a in found[:20]:
            print(f"  {a.isin} {a.lang}: {a.title}")
        if args.views:
            rows, errors = refresh_attention(s, cfg, client, day)
            failed = f", {len(errors)} en échec : {errors[:3]}" if errors else ""
            print(f"{rows} jour(s) de consultations lus{failed}")
            hits = compute_hits(s, cfg, day)
            print(f"étiquettes du {day} : {len(hits)}")
            for h in hits[:20]:
                print(f"  {h.isin} {h.label} — {h.text}")
        print(f"radar {'actif' if settings(cfg).enabled else 'désactivé'} (weak_signals.enabled)")
    return 0


def cmd_radar_measure(args: argparse.Namespace) -> int:
    """docs/26 §26.6 — return after each radar label, in excess of the universe median."""
    from app.db.session import db_session
    from app.services.weak_signals import measure

    with db_session() as s:
        res = measure(s)
    if not res:
        print("aucune étiquette mesurable (il faut au moins 5 séances après les premières étiquettes)")
    for lab, hs in res.items():
        for h, v in hs.items():
            t = f"{v['t_stat']:.1f}" if v.get("t_stat") is not None else "—"
            mean = f"{v['mean_r']:+.2%}" if v.get("mean_r") is not None else "—"
            flag = "  ✓ t ≥ 3" if v.get("significant") else ""
            print(f"{lab:<26} {h:<11} n={v['n']:<5} excès moyen {mean:>8} t={t}{flag}")
    return 0


def cmd_newsletter_link_check(args: argparse.Namespace) -> int:
    """docs/19 §19.6 — examine the latest matching letters (read-only): subject, link labels and
    hosts (never the content), then follow the configured link of each and print its status."""
    import getpass
    import os
    from urllib.parse import urlparse

    from app.data.providers.imap_gmail import GmailHistory
    from app.data.providers.linked_page import anchors, enrich_mail, find_link
    from app.scheduler.jobs.imap_poll import follow_rules

    cfg = load_params(args.params)
    rule = follow_rules(cfg.params.data.imap or {}).get(args.source)
    if rule is None:
        print(f"source {args.source} : aucun lien suivi (follow_links) ; liste des liens seulement")
    password = os.environ.get("IMAP_HISTORY_PASSWORD") or (
        os.environ.get("IMAP_PASSWORD") if args.user == os.environ.get("IMAP_USER") else None
    )
    password = (password or getpass.getpass(f"Mot de passe d'application Gmail pour {args.user} : ")).replace(" ", "")
    with GmailHistory("imap.gmail.com", args.user, password) as box:
        uids = box.search(args.query)
        if not uids:
            print("aucune lettre trouvée")
            return 1
        mails = [m for m in (box.mail(u, args.source) for u in uids[-args.count :]) if m is not None]
    ok = 0
    for mail in reversed(mails):
        print(f"\n— {mail.received_at:%d/%m/%Y %H:%M} UTC · {mail.subject[:90]}")
        labels = []
        for href, label in anchors(mail.html):
            host = urlparse(href).hostname or "?"
            if label and (label, host) not in labels:
                labels.append((label, host))
        if args.all_links:  # docs/21: locate the press-review block (labels and hosts only, never the content)
            print(f"  {len(labels)} lien(s) :")
            for i, (lab, host) in enumerate(labels, 1):
                print(f"   {i:>3}. « {lab[:80]} » ({host})")
        else:
            print("  liens : " + (" | ".join(f"« {lab[:40]} » ({host})" for lab, host in labels[:15]) or "aucun"))
        if rule is None:
            continue
        href = find_link(mail.html, list(rule.get("link_texts") or []))
        if not href:
            print(f"  bouton {rule.get('link_texts')} : absent")
            continue
        _, page = enrich_mail(mail, rule, cookie=os.environ.get("ZONEBOURSE_COOKIE") or None)
        final_host = urlparse(page.url).hostname if page.url else None
        print(
            f"  page liée : {page.status} · hôte final {final_host} · HTTP {page.http_status} · {len(page.text)} caractères"  # noqa: E501
        )
        if page.detail:
            print(f"  détail : {page.detail}")
        ok += page.status == "ok"
    return 0 if ok else 1


def cmd_newsletters_history(args: argparse.Namespace) -> int:
    """docs/19 — one-off, read-only import of past newsletters from a Gmail mailbox (app password
    asked at run time, never stored). ``--dry-run`` only counts senders and dates."""
    import getpass
    import os
    from collections import Counter

    from app.data.providers.imap_gmail import GmailHistory
    from app.db.session import db_session

    since = date.fromisoformat(args.since) if args.since else None
    password = os.environ.get("IMAP_HISTORY_PASSWORD") or getpass.getpass(
        f"Mot de passe d'application Gmail pour {args.user} (saisie masquée, jamais enregistré) : "
    )
    password = password.replace(" ", "")
    try:
        box = GmailHistory("imap.gmail.com", args.user, password).__enter__()
    except Exception as exc:  # noqa: BLE001
        print(f"connexion refusée ({exc.__class__.__name__}) : vérifier l'adresse et le mot de passe d'application")
        return 2
    try:
        uids = box.search(args.query, since)
        if args.limit:
            uids = uids[-args.limit :]
        print(f"dossier lu (lecture seule) : {box.folder} · {len(uids)} message(s) pour « {args.query} »")
        if args.dry_run:
            heads = [box.header(u) for u in uids]
            senders = Counter(h.sender for h in heads)
            dates = sorted(h.received_at for h in heads if h.received_at)
            if dates:
                print(f"du {dates[0]:%d/%m/%Y} au {dates[-1]:%d/%m/%Y}")
            print("expéditeurs :")
            for who, n in senders.most_common(10):
                print(f"  {n:5d}  {who}")
            print("exemples d'objets :")
            for h in heads[-5:]:
                print(f"  - {h.subject[:100]}")
            print("rien n'a été enregistré (--dry-run)")
            return 0

        from app.domain.calendar import load_market_calendars
        from app.llm.gateway import ClaudeGateway, settings_from
        from app.services.newsletters import ingest_mail
        from app.services.outcomes import update_outcomes

        cfg = load_params(args.params)
        cals = load_market_calendars(args.calendars)
        llm_settings = None if args.no_llm else settings_from(cfg)
        gateway = ClaudeGateway(llm_settings) if llm_settings else None
        if gateway is not None and not gateway.enabled:
            gateway = None
        import time as _time

        from app.data.providers.linked_page import enrich_mail
        from app.scheduler.jobs.imap_poll import follow_rules, subject_ok

        imap_cfg = cfg.params.data.imap or {}
        rule = follow_rules(imap_cfg).get(args.source)
        cookie = os.environ.get("ZONEBOURSE_COOKIE") or None
        totals = Counter()
        for i, uid in enumerate(uids, 1):
            with db_session() as s:
                if gateway is not None:
                    ok, why = gateway.budget_ok(s)
                    if not ok:
                        print(
                            f"arrêt : {why}. {len(uids) - i + 1} lettre(s) restante(s) : relancer demain, la reprise est automatique."  # noqa: E501
                        )  # noqa: E501
                        break
                mail = box.mail(uid, args.source)
                if mail is None:
                    totals["illisibles"] += 1
                    continue
                if not subject_ok(imap_cfg, args.source, mail.subject):
                    totals["hors_filtre_objet"] += 1
                    continue
                if rule is not None and not ingest_known(s, mail):
                    mail, page = enrich_mail(mail, rule, cookie=cookie)
                    totals[f"pages_{page.status}"] += 1
                    _time.sleep(float(rule.get("min_interval_s", 3)))
                rep = ingest_mail(s, mail, cfg, gateway, None, calendars=cals, historical=True)
            totals["nouvelles"] += rep.new
            totals["deja_presentes"] += 1 - rep.new
            totals["valeurs"] += rep.values
            totals["propositions"] += rep.signals
            totals["extraites_par_ia"] += rep.parsed_by_llm
            if i % 10 == 0 or i == len(uids):
                print(
                    f"{i}/{len(uids)} · nouvelles {totals['nouvelles']} · valeurs {totals['valeurs']} · "
                    f"propositions {totals['propositions']} · extraites par l'IA {totals['extraites_par_ia']}"
                )
        with db_session() as s:
            n = update_outcomes(s, cfg, cals)
        print(f"résultats recalculés : {n} · bilan : " + ", ".join(f"{k} {v}" for k, v in totals.items()))
        return 0
    finally:
        box.__exit__(None, None, None)


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
    for noisy in ("httpx", "httpx2", "httpcore", "anthropic", "apscheduler", "urllib3"):
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
    nh = sub.add_parser(
        "newsletters-history", help="import ponctuel, en lecture seule, des lettres passées d'une boîte Gmail (docs/19)"
    )
    nh.add_argument("--user", required=True, help="adresse Gmail à lire (lecture seule)")
    nh.add_argument("--query", required=True, help='requête Gmail, ex. "from:momentum"')
    nh.add_argument("--source", default="momentum_capital", help="nom de source (identique à data.imap.senders)")
    nh.add_argument("--since", help="AAAA-MM-JJ : lettres reçues depuis cette date")
    nh.add_argument("--limit", type=int, help="ne traiter que les N plus récentes")
    nh.add_argument("--dry-run", action="store_true", help="repérage : compte expéditeurs et dates, n'enregistre rien")
    nh.add_argument("--no-llm", action="store_true", help="extraction par règles seules (gratuite)")
    nh.set_defaults(fn=cmd_newsletters_history)
    am = sub.add_parser("archive-mentions", help="aperçu / archivage des propositions issues de simples mentions")
    am.add_argument("--apply", action="store_true", help="archiver réellement (sinon aperçu)")
    am.set_defaults(fn=cmd_archive_mentions)
    tl = sub.add_parser("ttf-list-load", help="télécharge la liste officielle des sociétés soumises à la TTF")
    tl.add_argument("--year", type=int, required=True)
    tl.add_argument("--url", default="https://bofip.impots.gouv.fr/bofip/9789-PGP.html")
    tl.add_argument("--file", help="page BOFiP enregistrée depuis le navigateur (si le téléchargement est refusé)")
    tl.add_argument("--out", default="data/ttf_list.yaml")
    tl.set_defaults(fn=cmd_ttf_list_load)
    lc = sub.add_parser("newsletter-link-check", help="suit le lien « Lire la suite » de la dernière lettre (docs/19)")
    lc.add_argument("--user", required=True)
    lc.add_argument("--query", required=True, help='ex. "from:noreply@zonebourse.com"')
    lc.add_argument("--source", default="zonebourse")
    lc.add_argument("--count", type=int, default=5, help="nombre de lettres récentes examinées")
    lc.add_argument("--all-links", action="store_true", help="liste tous les liens (libellé complet, hôte)")
    lc.set_defaults(fn=cmd_newsletter_link_check)
    sd = sub.add_parser("sources-discover", help="flux RSS annoncés par le site d'une source (docs/23)")
    sd.add_argument("url", nargs="?", default=None, help="page à examiner (défaut : sources « à renseigner »)")
    sd.add_argument("--name", default=None, help="une seule source du catalogue")
    sd.set_defaults(fn=cmd_sources_discover)
    sk = sub.add_parser("sources-check", help="lit chaque flux configuré et affiche son état (docs/23)")
    sk.add_argument("--name", default=None)
    sk.set_defaults(fn=cmd_sources_check)
    tc = sub.add_parser("tv-columns-check", help="colonnes d'analystes acceptées par le screener TradingView")
    tc.add_argument("--market", default="france")
    tc.add_argument("--columns", default=None, help="liste séparée par des virgules (défaut : candidates connues)")
    tc.set_defaults(fn=cmd_tv_columns_check)
    sc2 = sub.add_parser("shorts-check", help="positions courtes d'une autorité : lecture et contrôle (docs/23 S2)")
    sc2.add_argument("--authority", required=True, help="amf, bafin, afm, cnmv, fi_se…")
    sc2.add_argument("--url", default=None, help="adresse à tester (défaut : celle du params.yaml)")
    sc2.add_argument("--file", default=None, help="fichier CSV / Excel téléchargé à la main")
    sc2.add_argument("--store", action="store_true", help="enregistrer les positions lues")
    sc2.set_defaults(fn=cmd_shorts_check)
    el = sub.add_parser("exchange-list-load", help="liste officielle d'une place absente d'EODHD (docs/23 S3)")
    el.add_argument("--exchange", default="MI", help="code de place (MI = Milan)")
    el.add_argument("--file", required=True, help="liste Euronext téléchargée (CSV ou Excel)")
    el.add_argument("--market", default="Milan", help="ne garder que les lignes dont le marché contient ce mot")
    el.set_defaults(fn=cmd_exchange_list_load)
    bt = sub.add_parser("backtest", help="rejoue les détecteurs sur l'historique au coût complet (docs/27)")
    bt.add_argument("--from", dest="from_date", default=None, help="début (défaut : 8 ans)")
    bt.add_argument("--to", default=None)
    bt.add_argument("--detectors", default=None, help="ex. d2_breakout,d7_trend_quality (défaut : tous les actifs)")
    bt.set_defaults(fn=cmd_backtest)
    sub.add_parser("llm-costs", help="coût de l'API Claude par usage (docs/25 L6)").set_defaults(fn=cmd_llm_costs)
    rc = sub.add_parser("radar-check", help="radar des signaux faibles : Wikidata, Wikipédia, étiquettes (docs/26)")
    rc.add_argument("--isins", default=None, help="ex. FR0000120073,FR0000120271 (défaut : tout l'univers)")
    rc.add_argument("--views", action="store_true", help="lire aussi les consultations et calculer les étiquettes")
    rc.set_defaults(fn=cmd_radar_check)
    sub.add_parser("radar-measure", help="rendement en excès après chaque étiquette du radar (docs/26)").set_defaults(
        fn=cmd_radar_measure
    )
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
    pi.add_argument(
        "--reset",
        action="append",
        default=[],
        help="avec --update : remplacer ce bloc par celui de l'exemple (ex. data.macro)",
    )
    pi.set_defaults(fn=cmd_params_init)
    ev = sub.add_parser("env-set", help="régler une variable non secrète de .env (ex. BACKUP_HOST_DIR)")
    ev.add_argument("key")
    ev.add_argument("value", nargs="?", default=None)
    ev.add_argument("--unset", action="store_true", help="commenter la ligne (valeur par défaut)")
    ev.set_defaults(fn=cmd_env_set)
    es = sub.add_parser("env-setup", help="créer/compléter .env de façon interactive (saisie masquée)")
    es.add_argument(
        "group", help="database | saxo | eodhd | telegram | anthropic | imap | healthchecks | zonebourse | cloudflare"
    )
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
