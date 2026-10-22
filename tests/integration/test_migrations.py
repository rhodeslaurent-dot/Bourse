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
        "notes",
        "workflow_steps",
        "assistant_messages",
        "kpi_daily",
        "signal_outcomes",
        "learning_reviews",
        "learning_questions",
        "learning_suggestions",
        "trader_memory",
        "letter_reviews",
        "letter_opinions",
        "letter_validations",
        "macro_points",
        "macro_events",
        "portfolio_reviews",
        "daily_reports",
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


INS_SUG = (
    "INSERT INTO learning_suggestions (ts, kind, title, status) VALUES (CURRENT_TIMESTAMP, 'processus', :t, 'open')"
)
INS_Q = "INSERT INTO learning_questions (ts, question, kind, status) " + (
    "VALUES (CURRENT_TIMESTAMP, 'Pourquoi ?', 'texte', 'open')"
)


def test_migration_0008_marks_duplicate_suggestions(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, text

    url = f"sqlite+pysqlite:///{tmp_path / 'dedup.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    cfg = Config("alembic.ini")
    command.upgrade(cfg, "0007")
    eng = create_engine(url)
    with eng.begin() as c:
        for t in ("Planifier une revue", "planifier une revue !", "Autre idée"):
            c.execute(
                text(INS_SUG),
                {"t": t},
            )
        for _ in range(3):
            c.execute(text(INS_Q))
    command.upgrade(cfg, "head")
    with eng.begin() as c:
        sug = [r[0] for r in c.execute(text("SELECT status FROM learning_suggestions ORDER BY id"))]
        qs = [r[0] for r in c.execute(text("SELECT status FROM learning_questions ORDER BY id"))]
    assert sug == ["open", "duplicate", "open"] and qs == ["open", "dismissed", "dismissed"]
