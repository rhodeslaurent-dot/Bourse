"""Alembic migration applies on an empty database and matches the ORM models."""

import os

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command


def test_alembic_upgrade_head(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'mig.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    tables = set(inspect(create_engine(url)).get_table_names())
    for t in [
        "jobs_runs",
        "market_calendar",
        "srd_calendar",
        "availability",
        "alerts",
        "telegram_events",
        "params_versions",
    ]:
        assert t in tables
    assert os.environ["DATABASE_URL"] == url


def test_seed_calendar_cli(tmp_path, monkeypatch):
    url = f"sqlite+pysqlite:///{tmp_path / 'seed.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    from sqlalchemy import create_engine as ce

    from app.db.session import configure_engine

    configure_engine(ce(url))
    from app.cli import main

    assert main(["--params", "config/params.example.yaml", "seed-calendar", "--years", "2026"]) == 0
    from sqlalchemy import text

    with ce(url).connect() as c:
        n = c.execute(
            text("select count(*) from market_calendar where mic='XETR' and status='closed' and day='2026-12-24'")
        ).scalar()
        assert n == 1
        assert c.execute(text("select count(*) from srd_calendar")).scalar() == 24
