"""17/11/2026 — saxo-check run in the container (no .env file) printed « (simulation : …) » on the LIVE account."""

from __future__ import annotations

import argparse

from app import cli
from app.data.providers import saxo


class _Client:
    calls = [("GET", "/port/v1/clients/me")]

    def __init__(self, **kw) -> None:
        pass

    def me(self) -> dict:
        return {"ClientKey": "abc123XYZ==", "Name": "Client"}


class _Portfolio:
    isin_errors: dict = {}

    def __init__(self, client, key) -> None:
        pass

    def cash(self) -> float:
        return 0.0

    def positions(self) -> list:
        return []

    def orders(self) -> list:
        return []


def _run(monkeypatch, tmp_path, capsys, env_key: str | None) -> str:
    monkeypatch.chdir(tmp_path)  # no .env here, as in the container
    monkeypatch.setenv("SAXO_TOKEN_KEY", "x")
    monkeypatch.setattr(saxo, "SaxoClient", _Client)
    monkeypatch.setattr(saxo, "SaxoPortfolio", _Portfolio)
    monkeypatch.setattr(saxo, "TokenStore", lambda live: argparse.Namespace(path=str(tmp_path / "t")))
    if env_key is None:
        monkeypatch.delenv("SAXO_CLIENT_KEY", raising=False)
    else:
        monkeypatch.setenv("SAXO_CLIENT_KEY", env_key)
    assert cli.cmd_saxo_check(argparse.Namespace(sim=False)) == 0
    return capsys.readouterr().out


def test_live_in_the_container_never_says_simulation(monkeypatch, tmp_path, capsys):
    out = _run(monkeypatch, tmp_path, capsys, "abc123XYZ==")
    assert "LIVE" in out and "simulation" not in out and "déjà fourni à l'application" in out
    assert "abc123XYZ==" not in out  # the key is never printed whole
    out = _run(monkeypatch, tmp_path, capsys, None)
    assert "absent de l'environnement" in out
