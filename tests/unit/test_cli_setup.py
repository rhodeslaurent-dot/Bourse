"""CLI helpers for the first start: DATABASE_URL composition and params.yaml initialisation."""

from __future__ import annotations

from pathlib import Path

import yaml

from app import cli
from app.cli import database_url, init_params


def test_database_url_encodes_password():
    url = database_url("pg", "bourse", "bourse", "p@ss/w:rd")
    assert url == "postgresql+psycopg://bourse:p%40ss%2Fw%3Ard@pg:5432/bourse"


def test_init_params_fills_user_and_keeps_comments(tmp_path):
    example = tmp_path / "params.example.yaml"
    example.write_text(
        'version: 1\nuser:\n  telegram_user_id: 0            # à renseigner\n  email: "..."   # destinataire\n'
        '  work_slots_local:\n    brief: "08:45"\n',
        encoding="utf-8",
    )
    target = tmp_path / "params.yaml"
    assert init_params(str(example), str(target), "123456", "laurent@example.org") is True
    text = target.read_text(encoding="utf-8")
    assert "# à renseigner" in text and "# destinataire" in text
    data = yaml.safe_load(text)
    assert data["user"]["telegram_user_id"] == 123456 and data["user"]["email"] == "laurent@example.org"
    assert data["user"]["work_slots_local"]["brief"] == "08:45"
    assert init_params(str(example), str(target), "999", "x@y.z") is False  # never overwrites
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["user"]["telegram_user_id"] == 123456
    # --update rewrites only the two user.* lines of the existing file (a typo in the e-mail, for instance)
    target.write_text(target.read_text(encoding="utf-8").replace('brief: "08:45"', 'brief: "08:50"'), encoding="utf-8")
    assert init_params(str(example), str(target), "123456", "laurent@example.net", update=True) is True
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["user"]["email"] == "laurent@example.net" and data["user"]["work_slots_local"]["brief"] == "08:50"


def test_init_params_on_real_example(tmp_path):
    target = tmp_path / "params.yaml"
    assert init_params("config/params.example.yaml", str(target), "42", "a@b.c")
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["user"]["telegram_user_id"] == 42 and data["user"]["email"] == "a@b.c"


def test_docker_helpers_resolve_network_name_and_superuser(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake(*args: str) -> str | None:
        calls.append(args)
        if args[0] == "network":
            return "invest-n8n_default\n"
        if args[0] == "inspect":
            return "PATH=/usr/bin\nPOSTGRES_USER=n8n\nPOSTGRES_PASSWORD=x\n"
        return None

    monkeypatch.setattr(cli, "_docker", fake)
    assert cli.docker_network_name("c8927f481f45") == "invest-n8n_default"
    assert cli.docker_postgres_superuser("invest-n8n-postgres-1") == "n8n"
    monkeypatch.setattr(cli, "_docker", lambda *a: None)  # docker absent or failing → values kept / default
    assert cli.docker_network_name("my_net") == "my_net"
    assert cli.docker_postgres_superuser("whatever") == "postgres"


def test_create_database_uses_detected_superuser_and_keeps_password_off_argv(monkeypatch, capsys):
    import subprocess

    seen: dict = {}

    def fake_run(argv, input=None, capture_output=False):  # noqa: A002
        seen["argv"], seen["stdin"] = argv, input.decode()
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(cli, "docker_postgres_superuser", lambda c: "n8n")
    monkeypatch.setattr(subprocess, "run", fake_run)
    import shutil

    monkeypatch.setattr(shutil, "which", lambda x: "/usr/bin/docker")
    assert cli.create_database("pg", "bourse", "bourse", "s3cr'et") == 0
    assert seen["argv"][:6] == ["docker", "exec", "-i", "pg", "psql", "-U"] and seen["argv"][6] == "n8n"
    assert "s3cr" not in " ".join(seen["argv"]) and "PASSWORD 's3cr''et'" in seen["stdin"]
    assert "superutilisateur n8n" in capsys.readouterr().out


def test_set_env_value_replaces_commented_or_missing_lines():
    text = "A=1\n# BACKUP_HOST_DIR=C:/x   # commentaire\nB=2\n"
    out = cli.set_env_value(text, "BACKUP_HOST_DIR", "D:/sauvegardes")
    assert out == "A=1\nBACKUP_HOST_DIR=D:/sauvegardes\nB=2\n"
    assert cli.set_env_value(out, "BACKUP_HOST_DIR", None) == "A=1\n# BACKUP_HOST_DIR=\nB=2\n"
    assert cli.set_env_value("A=1", "NEW_VAR", "x") == "A=1\nNEW_VAR=x\n"
    win = "C:\\Users\\Laurent RHODES\\Documents\\x"  # backslashes must survive the substitution
    assert cli.set_env_value("# X_DIR=\n", "X_DIR", win) == f"X_DIR={win}\n"


def test_env_set_refuses_secrets(tmp_path, monkeypatch, capsys):
    import argparse

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("A=1\n", encoding="utf-8")
    assert cli.cmd_env_set(argparse.Namespace(key="TELEGRAM_BOT_TOKEN", value="x", unset=False)) == 2
    assert "secret" in capsys.readouterr().out
    assert cli.cmd_env_set(argparse.Namespace(key="BACKUP_HOST_DIR", value="D:/s", unset=False)) == 0
    assert "BACKUP_HOST_DIR=D:/s" in (tmp_path / ".env").read_text(encoding="utf-8")
    ns = argparse.Namespace(key="BACKUP_HOST_DIR", value="C:\\Users\\Laurent RHODES\\sauvegardes\\", unset=False)
    assert cli.cmd_env_set(ns) == 0
    assert "BACKUP_HOST_DIR=C:/Users/Laurent RHODES/sauvegardes\n" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert cli.cmd_env_set(argparse.Namespace(key="BACKUP_HOST_DIR", value="--C:/x", unset=False)) == 2


def test_merge_missing_jobs_appends_new_jobs_only():
    example = (
        "jobs:\n"
        '  test_job: {cron: "5 9 * * 1-5"}\n'
        "  news_scan:\n"
        '    premarket: {cron: "*/5 7-8 * * 1-5"}\n'
        '    day: {cron: "*/15 9-18 * * 1-5"}\n'
        '  backup: {cron: "0 23 * * *"}\n'
        '  saxo_token_keepalive: {every_minutes: 20, days: "1-7", market_days_only: false}   # ADR-007\n'
        "\n"
        "subscriptions: []\n"
    )
    target = (
        "jobs:   # heure de Paris\n"
        '  test_job: {cron: "5 9 * * 1-5", misfire_grace_min: 10}\n'
        '  backup: {cron: "0 23 * * *", tz: "Indian/Reunion"}\n'
        "\n"
        "subscriptions: []\n"
    )
    out, added = cli.merge_missing_jobs(example, target)
    assert added == ["news_scan", "saxo_token_keepalive"]
    assert "misfire_grace_min: 10" in out and 'tz: "Indian/Reunion"' in out  # existing lines untouched
    assert out.index("saxo_token_keepalive") < out.index("subscriptions") and "    day:" in out
    assert cli.merge_missing_jobs(example, out) == (out, [])  # idempotent


def test_init_params_update_merges_jobs_from_real_example(tmp_path):
    import yaml

    target = tmp_path / "params.yaml"
    text = Path("config/params.example.yaml").read_text(encoding="utf-8")  # noqa: F821
    target.write_text(text.replace("  saxo_token_keepalive:", "  # removed:"), encoding="utf-8")
    assert init_params("config/params.example.yaml", str(target), "42", "a@b.c", update=True)
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["jobs"]["saxo_token_keepalive"]["every_minutes"] == 20


def test_init_params_update_adds_learning_block_and_new_workflow_steps(tmp_path):
    import re

    import yaml

    target = tmp_path / "params.yaml"
    text = Path("config/params.example.yaml").read_text(encoding="utf-8")  # noqa: F821
    text = re.sub(r"^learning:.*?(?=^\S)", "", text, flags=re.M | re.S)  # an older params.yaml
    text = re.sub(r"^    - \{key: hebdo_apprentissage.*\n", "", text, flags=re.M)
    text = text.replace('label: "Lire les trois décisions du jour"', 'label: "Mes décisions"')
    target.write_text(text, encoding="utf-8")
    assert init_params("config/params.example.yaml", str(target), "42", "a@b.c", update=True)
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["learning"]["horizon_sessions"] == 20
    keys = [st["key"] for st in data["workflow"]["steps"]]
    assert "hebdo_apprentissage" in keys and len(keys) == len(set(keys))
    assert data["workflow"]["steps"][0]["label"] == "Mes décisions"  # user edits kept
    out = target.read_text(encoding="utf-8")
    assert cli.merge_missing_workflow_steps(out, out) == (out, [])


def test_merge_missing_children_nested_and_idempotent():
    example = (
        "risk:\n  a: 1\n  stop:\n    x: 1\n    wick: 0.005   # tampon\n  override:\n    min: 85\n    max: 1.5\n"
        "data:\n  q: true\n"
    )
    target = "risk:\n  a: 2\n  stop:\n    x: 3\n\ndata:\n  q: false\n"
    out, added = cli.merge_missing_children(example, target, "risk")
    assert added == ["override"] and "  a: 2\n" in out
    out, added = cli.merge_missing_children(example, out, "risk.stop")
    assert added == ["wick"] and "    x: 3\n    wick: 0.005   # tampon\n" in out
    import yaml

    d = yaml.safe_load(out)
    assert d["risk"]["override"] == {"min": 85, "max": 1.5} and d["risk"]["stop"] == {"x": 3, "wick": 0.005}
    assert d["data"] == {"q": False}
    assert cli.merge_missing_children(example, out, "risk") == (out, [])


def test_params_update_adds_new_rules_to_an_old_params(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    example = (root / "config" / "params.example.yaml").read_text(encoding="utf-8")
    import yaml

    old = yaml.safe_load(example)
    for k in ("admission", "market_gate", "conjuncture"):
        old.pop(k)
    old["risk"].pop("override")
    old["risk"]["stop"].pop("wick_buffer_pct")
    target = tmp_path / "params.yaml"
    target.write_text(yaml.safe_dump(old, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert cli.init_params(str(root / "config" / "params.example.yaml"), str(target), "1", "a@b.c", update=True)
    d = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert d["admission"]["min_reward_risk"] == 2.0 and d["risk"]["override"]["min_score"] == 85
    assert d["risk"]["stop"]["wick_buffer_pct"] == 0.005 and d["market_gate"]["mode"] == "double"


def test_reset_block_replaces_only_the_named_block():
    example = "data:\n  a: 1\n  macro:\n    series:\n      x: new\n  b: 2\njobs:\n  j: 1\n"
    target = "data:\n  a: 5\n  macro:\n    series:\n      x: old\n      y: old\n  b: 7\njobs:\n  j: 9\n"
    out, done = cli.reset_block(example, target, "data.macro")
    assert done and out == "data:\n  a: 5\n  macro:\n    series:\n      x: new\n  b: 7\njobs:\n  j: 9\n"
    assert cli.reset_block(example, target, "data.absent") == (target, False)


def test_archive_mentions_dry_run_then_apply(db_engine, capsys):
    from datetime import UTC, datetime

    from app.db.models import NewsletterValue, Proposal, Signal
    from app.db.session import db_session

    with db_session() as s:
        sig = Signal(isin="FR0000120271", detector="external", source="momentum_capital", ts=datetime.now(UTC),
                     timeframe="eod", data_status="eod", params_version="t")  # fmt: skip
        s.add(sig)
        s.flush()
        s.add(NewsletterValue(item_id=1, isin="FR0000120271", direction="neutral", signal_id=sig.id))
        s.add(Proposal(signal_id=sig.id, isin="FR0000120271", action="WATCH", status="expired"))
    assert cli.main(["archive-mentions"]) == 0
    assert "aperçu seulement" in capsys.readouterr().out
    with db_session() as s:
        assert s.query(Proposal).one().deleted_at is None
    assert cli.main(["archive-mentions", "--apply"]) == 0
    with db_session() as s:
        assert s.query(Proposal).one().deleted_at is not None


def test_params_update_completes_every_section_of_the_example(tmp_path):
    """26/10/2026: a fixed list of sections missed ``regime.indices_by_mic``, ``market_gate.letters_markets`` and
    ``weak_signals.social.history`` — every block mapping of the example is now completed, the user's values kept."""
    example = Path("config/params.example.yaml").read_text(encoding="utf-8")
    full = yaml.safe_load(example)
    lines = example.splitlines(keepends=True)
    for path in ("regime.indices_by_mic", "market_gate.letters_markets", "weak_signals.social.history"):
        s, e, _ = cli._block_range(lines, path)
        if e == s + 1 or not lines[s].rstrip().endswith(":"):  # a one-line key
            e = s + 1
        del lines[s:e]
    old = "".join(lines).replace("  min_reward_risk: 2.0", "  min_reward_risk: 2.5")
    assert "indices_by_mic" not in old and "2.5" in old
    target = tmp_path / "params.yaml"
    target.write_text(old, encoding="utf-8")
    assert init_params("config/params.example.yaml", str(target), "42", "a@b.c", update=True)
    got = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert got["regime"]["indices_by_mic"] == full["regime"]["indices_by_mic"]
    assert got["market_gate"]["letters_markets"] == full["market_gate"]["letters_markets"]
    assert got["weak_signals"]["social"]["history"] == full["weak_signals"]["social"]["history"]
    assert got["admission"]["min_reward_risk"] == 2.5  # the user's value is kept
    again = target.read_text(encoding="utf-8")
    init_params("config/params.example.yaml", str(target), "42", "a@b.c", update=True)
    assert target.read_text(encoding="utf-8") == again  # idempotent


def test_merge_never_appends_under_an_inline_value():
    example = "a:\n  b:\n    x: 1\n    y: 2\n"
    target = "a:\n  b: {x: 5}\n"
    assert cli.merge_missing_children(example, target, "a.b") == (target, [])


def test_fx_history_loads_the_ecb_history_from_a_date(db_engine, monkeypatch, capsys):
    """26/10/2026: the base only held ECB rates from 23/07/2026; the research ran 2016–2026 on static rates."""
    import argparse
    from datetime import date

    from app.data.providers.ecb_fx import FxPoint
    from app.db.models import FxRate
    from app.db.session import db_session

    pts = [FxPoint(date(2014, 12, 31), "SEK", 9.39), FxPoint(date(2016, 1, 4), "SEK", 9.25),
           FxPoint(date(2016, 1, 4), "USD", 1.09), FxPoint(date(2016, 1, 5), "USD", 1.07)]  # fmt: skip
    monkeypatch.setattr("app.data.providers.ecb_fx.fetch", lambda period="daily", client=None: pts)
    assert cli.cmd_fx_history(argparse.Namespace(from_date="2015-01-01")) == 0
    with db_session() as s:
        rows = {(r.pair, r.day): r.rate for r in s.query(FxRate)}
    assert rows == {("EURSEK", date(2016, 1, 4)): 9.25, ("EURUSD", date(2016, 1, 4)): 1.09,
                    ("EURUSD", date(2016, 1, 5)): 1.07}  # fmt: skip
    assert "3 taux BCE enregistrés : 2 devises, 2 jours du 2016-01-04 au 2016-01-05" in capsys.readouterr().out


def test_delisted_check_reports_counts_isin_and_history(monkeypatch, capsys):
    """docs/33 §33.3: registration n° 2 needs the delisted shares of each venue, with their history."""
    import argparse
    from datetime import date as d

    from app.data.base import ProviderError

    class Bar:
        def __init__(self, day):
            self.day = day

    class Fake:
        def delisted_symbols(self, ex):
            if ex == "SW":
                raise ProviderError("EODHD 403 on /exchange-symbol-list/SW: plan")
            return [{"Code": "OLD", "Isin": "US0000000001"}, {"Code": "NOISIN", "Isin": None}]

        def fetch_eod(self, symbols, start, end):
            return [Bar(d(2017, 1, 3)), Bar(d(2019, 5, 2))]

    monkeypatch.setenv("EODHD_API_TOKEN", "t")
    monkeypatch.setattr("app.data.providers.eodhd.eodhd_from", lambda cfg: Fake())
    assert cli.cmd_delisted_check(argparse.Namespace(params="config/params.example.yaml", exchanges="US,SW")) == 0
    out = capsys.readouterr().out
    assert "US     2 action(s) radiée(s), dont 1 avec ISIN · historique de OLD.US : 2 séance(s)" in out
    assert "SW     liste des radiées : ÉCHEC" in out
    assert "Sans historique des radiées (SW)" in out


def test_a_read_only_params_file_says_where_to_run_the_command(tmp_path, monkeypatch, capsys):
    """28/10/2026: params-init run inside the container hit the read-only mount with a traceback."""
    import errno
    from pathlib import Path

    import pytest

    from app.cli import init_params

    target = tmp_path / "params.yaml"
    init_params("config/params.example.yaml", str(target), "42", "a@b.c")

    def read_only(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ARG001
        raise OSError(errno.EROFS, "Read-only file system")

    monkeypatch.setattr(Path, "write_text", read_only)
    with pytest.raises(SystemExit) as exc:
        init_params("config/params.example.yaml", str(target), "42", "a@b.c", update=True)
    assert exc.value.code == 2 and "uv run python -m app.cli params-init" in capsys.readouterr().out


def test_the_routine_lines_of_the_15_second_job_are_filtered():
    import logging

    from app.logging_setup import RoutineJobFilter

    f = RoutineJobFilter()

    def rec(name: str, level: int, msg: str) -> logging.LogRecord:
        return logging.LogRecord(name, level, __file__, 1, msg, None, None)

    assert not f.filter(
        rec("apscheduler.executors.default", logging.INFO, 'Running job "intraday_reeval (trigger: …)"')
    )
    assert not f.filter(
        rec("apscheduler.executors.default", logging.INFO, 'Job "intraday_reeval (trigger: …)" executed')
    )
    assert f.filter(rec("apscheduler.executors.default", logging.INFO, 'Running job "position_monitor (trigger: …)"'))
    assert f.filter(rec("apscheduler.executors.default", logging.ERROR, 'Job "intraday_reeval (trigger)" raised'))
    assert f.filter(rec("bourse.jobs", logging.INFO, "intraday_reeval : 2 changements"))
