"""18/11/2026 — « the Cloudflare link takes several minutes to load »: slow requests kept, the home page timed part
by part, the running jobs and long SQL statements listed (`sources-health --pages`)."""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta

from app import cli
from app.db.models import JobRun
from app.db.session import db_session
from app.services import page_timing


def test_slow_requests_are_kept_and_cut_back(monkeypatch, tmp_path):
    monkeypatch.setenv("SLOW_REQUESTS_PATH", str(tmp_path / "d" / "slow.jsonl"))
    monkeypatch.setattr(page_timing, "KEEP", 3)
    for k in range(7):
        page_timing.record_slow("GET", f"/p{k}", 6.0 + k, 200)
    kept = page_timing.recent_slow(10)
    assert [r["path"] for r in kept] == ["/p4", "/p5", "/p6"] and kept[-1]["s"] == 12.0
    monkeypatch.setenv("SLOW_REQUESTS_PATH", str(tmp_path / "none.jsonl"))
    assert page_timing.recent_slow() == []


def test_the_home_page_is_timed_part_by_part_and_nothing_stays_patched(db_engine, config):
    from app.services import daily_review

    before = daily_review._ideas
    with db_session() as s:
        total, parts = page_timing.time_home(s, config, date(2026, 11, 18))
    names = {n for n, _, _ in parts}
    assert total >= 0 and {"_ideas", "_portfolio", "_sources", "build_risk_view"} <= names
    assert daily_review._ideas is before  # the wrappers are removed afterwards


def test_sources_health_pages_prints_the_four_sections(db_engine, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("PARAMS_PATH", "config/params.example.yaml")
    monkeypatch.setenv("SLOW_REQUESTS_PATH", str(tmp_path / "slow.jsonl"))
    page_timing.record_slow("GET", "/", 95.0, 200, datetime(2026, 11, 18, 5, 0, tzinfo=UTC))
    with db_session() as s:
        s.add(JobRun(job="sources_daily", started_at=datetime.now(UTC) - timedelta(minutes=12), status="running"))
    assert cli.cmd_sources_health(argparse.Namespace(date="2026-11-18", no_jobs=False, pages=True)) == 0
    out = capsys.readouterr().out
    assert "18/11 09:00:00     95.0 s  GET /" in out  # Réunion time
    assert "sources_daily" in out and "(12 min)" in out
    assert "Requêtes SQL en cours depuis plus de 5 s" in out and "Page d'accueil (Revue du jour) construite en" in out
    assert "Page Sources construite en" in out and "scoreboard" in out


def test_sources_health_job_prints_only_the_named_jobs_whole(db_engine, capsys):
    """18/11/2026: the consensus line fell in the part of the relay report that is cut."""
    with db_session() as s:
        s.add(JobRun(job="consensus_daily", started_at=datetime.now(UTC) - timedelta(hours=2), status="ok",
                     rows=1819, note="couverture par place : " + "x" * 900))  # fmt: skip
        s.add(JobRun(job="news_scan", started_at=datetime.now(UTC), status="ok", note="autre"))
    args = argparse.Namespace(date=None, no_jobs=False, pages=False, job=["consensus_daily"])
    assert cli.cmd_sources_health(args) == 0
    out = capsys.readouterr().out
    assert "consensus_daily" in out and "x" * 900 in out and "news_scan" not in out and "Cours de clôture" not in out


def test_the_social_note_says_why_reddit_and_bluesky_gave_nothing(db_engine, config, monkeypatch):
    """18/11/2026: « Reddit 0 (1 échec(s)) ; Bluesky 0 (1 échec(s)) » every day, the cause unsaid."""
    import httpx

    from app.services import social

    for k in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(social, "collect_forum", lambda *a: (0, []))
    with db_session() as s:
        notes = social.collect_all(s, config, httpx.Client(), date(2026, 11, 18))
    assert "Reddit 0 (1 échec(s) : identifiants Reddit absents (env-setup reddit))" in notes
    assert "Bluesky 0 (1 échec(s) : identifiants Bluesky absents (env-setup bluesky))" in notes
