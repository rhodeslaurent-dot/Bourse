"""CLI helpers for the first start: DATABASE_URL composition and params.yaml initialisation."""

from __future__ import annotations

import yaml

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


def test_init_params_on_real_example(tmp_path):
    target = tmp_path / "params.yaml"
    assert init_params("config/params.example.yaml", str(target), "42", "a@b.c")
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert data["user"]["telegram_user_id"] == 42 and data["user"]["email"] == "a@b.c"
