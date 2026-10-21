"""docs/17 §17.6 (A17.4): a job step runs the job and stores its result; state persists for the day;
page steps redirect; params-init --update brings the workflow block."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import JobRun, WorkflowStep
from app.db.session import db_session
from app.main import create_app
from app.services.workflow import day_view


@pytest.fixture
def client(config, calendars, db_engine, monkeypatch):
    monkeypatch.setenv("ALLOW_UNAUTHENTICATED_WRITES", "1")
    app = create_app(config, calendars, start_scheduler=False, start_telegram=False)
    with TestClient(app) as c:
        yield c


def test_steps_follow_days_and_calendar(db_engine, config):
    with db_session() as s:
        monday = day_view(s, config.params, date(2026, 10, 19), True, "reunion")
        saturday = day_view(s, config.params, date(2026, 10, 24), False, "reunion")
    by_key = {x.key: x for x in monday}
    assert by_key["c1_sync"].applicable and not by_key["c3_import"].applicable  # import on Friday only
    assert by_key["hebdo_perf"].why_not == "pas aujourd'hui"
    sat = {x.key: x for x in saturday}
    assert sat["hebdo_perf"].applicable and sat["sys_backup"].applicable and not sat["c1_sync"].applicable


def test_job_step_runs_and_persists(client, monkeypatch):
    monkeypatch.setenv(
        "BACKUP_DIR", "/tmp/claude-0/-home-user-Bourse/a937a5ef-3526-5ccd-a8bb-b03dfd7d2f97/scratchpad/bk"
    )
    r = client.post("/journee/step", data={"key": "sys_backup", "action": "do"}, follow_redirects=False)
    assert r.status_code == 303
    with db_session() as s:
        row = s.scalar(select(WorkflowStep).where(WorkflowStep.key == "sys_backup"))
        assert row is not None and row.result and row.result.startswith(("ok", "skipped", "error"))
        assert s.scalar(select(JobRun).where(JobRun.job == "backup")) is not None
    html = client.get("/journee").text
    assert "Sauvegarde du jour" in html and "fait" in html


def test_page_step_redirects_and_check_toggles(client):
    r = client.post("/journee/step", data={"key": "c3_notes", "action": "do"}, follow_redirects=False)
    assert r.headers["location"] == "/journee"
    client.post("/journee/step", data={"key": "c3_notes", "action": "undo"})
    with db_session() as s:
        assert s.scalar(select(WorkflowStep).where(WorkflowStep.key == "c3_notes")).done_at is None
    assert client.post("/journee/step", data={"key": "nope"}).status_code == 404


def test_params_init_update_adds_workflow_block(tmp_path):
    import yaml

    from app.cli import init_params

    text = open("config/params.example.yaml", encoding="utf-8").read()
    head, _, tail = text.partition("\nworkflow:")
    tail_rest = tail[tail.index("\nsubscriptions:") :]
    target = tmp_path / "params.yaml"
    target.write_text(head + tail_rest, encoding="utf-8")
    assert "workflow" not in yaml.safe_load(target.read_text(encoding="utf-8"))
    init_params("config/params.example.yaml", str(target), "42", "a@b.c", update=True)
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert any(st["key"] == "sys_backup" for st in data["workflow"]["steps"])
