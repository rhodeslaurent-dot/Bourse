"""Remote piloting (15/11/2026) — the PC runs only allow-listed commands, a refused task runs nothing, a value of
the PC is substituted there only, a secret-looking line is masked."""

from __future__ import annotations

import pytest
import yaml

from app import remote_pilot as rp


@pytest.mark.parametrize(
    "cmd",
    [
        "git log -3 --oneline",
        "git status",
        "uv run python -m app.cli params-init --update",
        'uv run python -m app.cli params-init --update --set "data.sec.user_agent=Laurent {params:user.email}"',
        "docker compose up -d --build",
        "docker compose logs --tail 200 app",
        "wait-app 600",
        "docker compose exec -T app python -m app.cli events-build --from 2016-01-01",
        "docker compose exec -T app python -m app.cli events-scoreboard --horizon 5 --context 10",
        "docker compose exec -T app python -m app.cli run-job sources_daily --date 2026-11-14",
    ],
)
def test_allowed_commands(cmd):
    assert rp.check(cmd) is None


@pytest.mark.parametrize(
    "cmd",
    [
        "cat .env",
        "git push",
        "docker compose down -v",
        "docker compose exec -T app python -m app.cli sources-health; rm -rf /",
        "docker compose exec -T app python -m app.cli sources-health && curl x",
        "docker compose exec -T app python -m app.cli env-set SAXO_TOKEN x",
        "docker compose exec -T app python -m app.cli radar-history reset",
        "docker compose exec -T app python -m app.cli saxo-auth",
        "docker compose exec -T app sh -c id",
        'uv run python -m app.cli params-init --update --set "user.email=$(whoami)"',
        "uv run python -m app.cli core-set FR0000000CR1",
        "docker compose exec -T app python -m app.cli backfill --from `id`",
        "wait-app 600\ncat .env",
    ],
)
def test_refused_commands(cmd):
    assert rp.check(cmd) is not None


def test_a_task_with_one_refused_command_runs_nothing():
    ran = []
    task = {"id": "t1", "purpose": "x", "commands": ["git status", "cat .env"]}
    rep = rp.run_task(task, {}, run=lambda a, t: ran.append(a) or (0, ""))
    assert rep.status == "refusée" and ran == [] and "cat .env" in (rep.reason or "")


def test_steps_stop_at_the_first_error_and_values_of_the_pc_are_substituted():
    ran = []

    def fake(args, timeout):
        ran.append(args)
        if "us-check" in args:
            return 1, "erreur"
        return 0, "data.sec.user_agent = Laurent moi@example.org\nok token=abc123"  # params-init prints the value

    task = {
        "id": "t2",
        "purpose": "essai",
        "commands": [
            'uv run python -m app.cli params-init --update --set "data.sec.user_agent=Laurent {params:user.email}"',
            "docker compose exec -T app python -m app.cli us-check",
            "docker compose ps",
        ],
    }
    rep = rp.run_task(task, {"user": {"email": "moi@example.org"}}, run=fake)
    assert rep.status == "échec" and len(ran) == 2  # stopped after the failing step
    assert ran[0][-1] == "data.sec.user_agent=Laurent moi@example.org"  # substituted on the PC only
    text = rep.text()
    assert "moi@example.org" not in text and "{params:user.email}" in text  # the report shows the task's text
    assert "token=***" in text and "abc123" not in text


def test_wait_app_polls_until_the_container_answers():
    answers = iter([(1, "down"), (1, "down"), (0, '{"status":"ok"}')])
    rep = rp.run_task({"id": "t3", "commands": ["wait-app 600"]}, {}, run=lambda a, t: next(answers),
                      sleep=lambda s: None)  # fmt: skip
    assert rep.status == "faite" and "application prête" in rep.steps[0].output


def test_each_task_runs_once_and_the_shipped_task_file_is_valid(tmp_path):
    done = tmp_path / ".done"
    done.write_text('{"id": "a", "status": "faite"}\nnot json\n', encoding="utf-8")
    assert rp.done_ids(done) == {"a"}
    assert rp.next_task([{"id": "a"}, {"id": "b"}], {"a"}) == {"id": "b"}
    assert rp.next_task([{"id": "a"}], {"a"}) is None
    shipped = yaml.safe_load(open("ops/pc_tasks.yaml", encoding="utf-8"))
    ids = [t["id"] for t in shipped["tasks"]]
    assert len(ids) == len(set(ids))
    for t in shipped["tasks"]:
        for c in t["commands"]:
            assert rp.check(c) is None, c


def test_a_missing_value_of_the_pc_fails_the_step_without_running_it():
    ran = []
    task = {"id": "t4", "commands": ['uv run python -m app.cli params-init --update --set "a.b={params:user.nope}"']}
    rep = rp.run_task(task, {"user": {}}, run=lambda a, t: ran.append(a) or (0, ""))
    assert rep.status == "échec" and ran == [] and "user.nope" in rep.steps[0].output


def test_params_init_update_keeps_the_user_values_without_prompting(tmp_path):
    from app.cli import existing_user

    p = tmp_path / "params.yaml"
    p.write_text('user:\n  telegram_user_id: 42\n  email: "x@example.org"\n', encoding="utf-8")
    assert existing_user(str(p)) == {"telegram_user_id": 42, "email": "x@example.org"}
    assert existing_user(str(tmp_path / "absent.yaml")) == {}


def _paths(tmp_path, monkeypatch):
    for name, rel in (("TASKS", "pc_tasks.yaml"), ("DONE", ".done"), ("OUT", "out"), ("SENT", ".sent"),
                      ("LOCK", ".running")):  # fmt: skip
        monkeypatch.setattr(rp, name, tmp_path / rel)
    (tmp_path / "pc_tasks.yaml").write_text(
        yaml.safe_dump({"tasks": [{"id": "t1", "purpose": "essai", "commands": ["git status"]}]}), encoding="utf-8"
    )


def test_a_tick_starts_the_task_in_the_background_and_never_twice(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    spawned = []
    out = rp.tick(spawn=lambda: spawned.append(1), now=1000.0)
    assert "tâche lancée en arrière-plan : t1" in out and spawned == [1]
    again = rp.tick(spawn=lambda: spawned.append(1), now=1060.0)
    assert "tâche en cours : t1 depuis 1 min" in again and spawned == [1]  # the running task is never doubled


def test_the_worker_writes_the_report_and_the_next_tick_sends_it_once(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    rp.tick(spawn=lambda: None, now=1000.0)
    done = rp.Report("t1", "essai", "faite", [rp.Step("git status", 0, 1.0, "ok")])
    monkeypatch.setattr(rp, "run_task", lambda task, params: done)
    assert rp.work() == 0 and not (tmp_path / ".running").exists()
    first = rp.tick(spawn=lambda: None, now=2000.0)
    assert "=== COMPTE RENDU À ENVOYER ===" in first and "tâche t1 : faite" in first
    assert "aucune tâche nouvelle" in first
    second = rp.tick(spawn=lambda: None, now=2100.0)
    assert "COMPTE RENDU" not in second  # sent once


def test_a_stale_lock_is_removed(tmp_path, monkeypatch):
    _paths(tmp_path, monkeypatch)
    (tmp_path / ".running").write_text('{"id": "old", "started": 0, "timeout_s": 10}', encoding="utf-8")
    out = rp.tick(spawn=lambda: None, now=100000.0)
    assert "verrou périmé de la tâche old retiré" in out and "tâche lancée en arrière-plan : t1" in out


def test_host_cli_commands_run_with_the_runner_s_own_interpreter():
    import sys

    assert rp.argv_of("uv run python -m app.cli params-init --update")[:3] == [sys.executable, "-m", "app.cli"]
    assert rp.argv_of("docker compose ps") == ["docker", "compose", "ps"]


def test_the_cli_writes_utf8_even_to_a_cp1252_pipe(monkeypatch):
    """PC report of 15/11/2026: « ✅ » made check-config fail on a Windows pipe (cp1252)."""
    import io
    import sys

    from app import cli

    raw = io.BytesIO()
    fake = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", fake)
    cli.utf8_output()
    print("✅ ⛔ →")
    fake.flush()
    assert raw.getvalue().decode("utf-8").strip() == "✅ ⛔ →"


def test_a_report_stays_bounded_so_the_pc_relays_it_verbatim():
    huge = "\n".join(f"ligne de journal {k}" for k in range(20000))
    rep = rp.run_task({"id": "t5", "commands": ["docker compose logs --tail 400 app", "docker compose ps"]}, {},
                      run=lambda a, t: (0, huge))  # fmt: skip
    text = rep.text()
    assert len(text) <= rp.REPORT_MAX + 200 and "caractères coupés" in text
    assert "ligne de journal 0" in text and "ligne de journal 19999" in text  # the start and the end are kept


def test_commands_started_by_the_runner_write_utf8():
    import sys

    code, out = rp._run([sys.executable, "-c", "import sys; print(sys.stdout.encoding)"], 30)
    assert code == 0 and out.strip().lower().replace("-", "") == "utf8"
