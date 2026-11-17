"""17/11/2026 — `run-job` from the command line ran the AI jobs without the AI (rules fallback, silently)."""

from __future__ import annotations

import argparse

from app import cli


def test_run_job_gives_the_job_the_same_ai_as_the_scheduler(monkeypatch):
    import app.llm as llm
    import app.scheduler.runner as runner

    seen = {}

    class Run:
        status, rows, note, error = "ok", 0, "", None

    def fake_execute(name, config, calendars, **kw):
        seen.update(kw)
        return Run()

    monkeypatch.setattr(runner, "execute_job", fake_execute)
    monkeypatch.setattr(llm, "settings_from", lambda cfg: "settings")
    monkeypatch.setattr(llm, "ClaudeGateway", lambda st: ("gateway", st))
    args = argparse.Namespace(params="config/params.example.yaml", calendars="config/market_calendars.yaml",
                              date=None, variant="normal", mics=None, job="position_ai_review")  # fmt: skip
    assert cli.cmd_run_job(args) == 0
    assert seen["llm"] == ("gateway", "settings")
    monkeypatch.setattr(llm, "settings_from", lambda cfg: None)  # no key: the rules fallback, as in the app
    cli.cmd_run_job(args)
    assert seen["llm"] is None
