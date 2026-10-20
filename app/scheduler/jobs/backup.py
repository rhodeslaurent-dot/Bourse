"""Daily backup (23:00 Réunion): ``pg_dump -Fc`` to ``BACKUP_DIR`` + rotation (docs/14 §14.2).

On SQLite (dev) it copies the file. Restoration is exercised by ``scripts/restore_test.ps1``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.db.session import database_url
from app.scheduler.runner import JobContext


def run(ctx: JobContext) -> int:
    backup_dir = Path(os.environ.get("BACKUP_DIR", "backups"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    url = database_url()
    if url.startswith("postgresql"):
        target = backup_dir / f"bourse-{stamp}.dump"
        argv, env = pg_dump_command(url, target)
        r = subprocess.run(argv, env=env, capture_output=True, timeout=600)  # noqa: S603 — fixed argv
        if r.returncode != 0:
            # never echo the command (it would carry the URL): stderr of pg_dump only
            raise RuntimeError(f"pg_dump a échoué (code {r.returncode}) : {r.stderr.decode('utf-8', 'replace')[:300]}")
    else:
        src = url.split("///", 1)[1]
        target = backup_dir / f"bourse-{stamp}.sqlite"
        shutil.copy(src, target)
    rotate(backup_dir, keep_days=int(os.environ.get("BACKUP_RETENTION_DAYS", "30")))
    return 1


def pg_dump_command(url: str, target: Path) -> tuple[list[str], dict[str, str]]:
    """``pg_dump`` argv and environment from a SQLAlchemy URL: the ``+psycopg`` driver suffix is
    dropped (libpq does not know it) and the password travels through ``PGPASSWORD``, never on the
    command line nor in any error message."""
    from urllib.parse import unquote, urlsplit

    u = urlsplit(url)
    env = {k: v for k, v in os.environ.items() if k != "PGPASSWORD"}
    if u.password:
        env["PGPASSWORD"] = unquote(u.password)
    netloc = f"{unquote(u.username)}@" if u.username else ""
    netloc += u.hostname or "localhost"
    if u.port:
        netloc += f":{u.port}"
    libpq_url = f"postgresql://{netloc}{u.path or ''}"
    return ["pg_dump", "-Fc", "-d", libpq_url, "-f", str(target)], env


def rotate(backup_dir: Path, keep_days: int) -> None:
    """Keep ``keep_days`` of daily files plus the first file of each month (12 monthlies)."""
    cutoff = datetime.now(UTC) - timedelta(days=keep_days)
    firsts: dict[str, Path] = {}
    for f in sorted(backup_dir.glob("bourse-*")):
        firsts.setdefault(f.name[7:13], f)
    for f in backup_dir.glob("bourse-*"):
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=UTC)
        if mtime < cutoff and firsts.get(f.name[7:13]) != f:
            f.unlink()
