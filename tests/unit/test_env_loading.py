"""Placeholder values in .env (``KEY=   # comment``, ``<equipe>.…``) must not switch features on."""

from __future__ import annotations

import os

from app.api.security import cf_settings
from app.env import is_placeholder, load_env, sanitize_environ


def test_is_placeholder():
    assert is_placeholder("# Application Audience (AUD) tag")
    assert is_placeholder("<equipe>.cloudflareaccess.com") and is_placeholder("bourse.<nom>@gmail.com")
    assert is_placeholder("postgresql+psycopg://bourse:CHANGE_ME@postgres:5432/bourse")
    assert not is_placeholder("team.cloudflareaccess.com") and not is_placeholder("") and not is_placeholder(None)


def test_old_style_env_file_does_not_enable_cloudflare_access(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "CF_ACCESS_TEAM_DOMAIN=<equipe>.cloudflareaccess.com\n"
        "CF_ACCESS_AUD=                              # Application Audience (AUD) tag\n"
        "TELEGRAM_USER_ID=0                          # votre user_id\n"
        "LOG_LEVEL=INFO\n",
        encoding="utf-8",
    )
    for k in ("CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "TELEGRAM_USER_ID", "LOG_LEVEL"):
        monkeypatch.delenv(k, raising=False)
    removed = load_env(str(env))
    assert set(removed) == {"CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD"}
    assert cf_settings() is None
    assert os.environ["TELEGRAM_USER_ID"] == "0" and os.environ["LOG_LEVEL"] == "INFO"


def test_sanitize_applies_to_variables_injected_by_docker(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_AUD", "# comment injected by compose env_file")
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "real.cloudflareaccess.com")
    assert sanitize_environ(["CF_ACCESS_AUD", "CF_ACCESS_TEAM_DOMAIN"]) == ["CF_ACCESS_AUD"]
    assert os.environ["CF_ACCESS_TEAM_DOMAIN"] == "real.cloudflareaccess.com"
    assert cf_settings() is None  # AUD missing → protection disabled, /sante says so
