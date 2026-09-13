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
        subprocess.run(["pg_dump", "-Fc", "-d", url, "-f", str(target)], check=True, timeout=600)
    else:
        src = url.split("///", 1)[1]
        target = backup_dir / f"bourse-{stamp}.sqlite"
        shutil.copy(src, target)
    rotate(backup_dir, keep_days=int(os.environ.get("BACKUP_RETENTION_DAYS", "30")))
    return 1


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
