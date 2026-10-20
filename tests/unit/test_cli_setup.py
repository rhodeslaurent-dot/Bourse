"""CLI helpers for the first start: DATABASE_URL composition and params.yaml initialisation."""

from __future__ import annotations

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
