"""Rule 6: no secret in logs or job errors. The backup job once echoed DATABASE_URL (with the
password) in its error message."""

from __future__ import annotations

from pathlib import Path

from app.logging_setup import redact
from app.scheduler.jobs.backup import pg_dump_command


def test_redact_masks_url_credentials_and_tokens():
    s = "Command ['pg_dump', '-d', 'postgresql+psycopg://bourse:S3cr3t%40x@pg:5432/bourse'] failed"
    out = redact(s)
    assert "S3cr3t" not in out and "postgresql+psycopg://bourse:[redacted]@pg:5432/bourse" in out
    assert redact("api_token=abcdef12345&x=1") == "api_token=[redacted]&x=1"
    assert redact("rien de secret ici") == "rien de secret ici"


def test_pg_dump_command_keeps_password_off_argv(monkeypatch):
    monkeypatch.setenv("PGPASSWORD", "stale")
    argv, env = pg_dump_command(
        "postgresql+psycopg://bourse:p%40ss@invest-n8n-postgres-1:5432/bourse", Path("/b/x.dump")
    )
    assert argv == ["pg_dump", "-Fc", "-d", "postgresql://bourse@invest-n8n-postgres-1:5432/bourse", "-f", "/b/x.dump"]
    assert env["PGPASSWORD"] == "p@ss" and "p@ss" not in " ".join(argv)


def test_backup_error_never_contains_the_url(monkeypatch, tmp_path):
    import subprocess

    from app.scheduler.jobs import backup

    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(backup, "database_url", lambda: "postgresql+psycopg://bourse:S3cr3t@pg:5432/bourse")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, b"", b"pg_dump: error: connection refused"),
    )
    import pytest

    with pytest.raises(RuntimeError) as exc:
        backup.run(None)  # type: ignore[arg-type]
    assert "S3cr3t" not in str(exc.value) and "connection refused" in str(exc.value)
