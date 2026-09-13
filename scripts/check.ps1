# CI locale (docs/14 §14.4) : ruff, mypy, pytest, docker compose config. Branché en hook Stop de Claude Code.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
uv run ruff check .
uv run ruff format --check .
uv run mypy app/domain
uv run pytest -q
docker compose config | Out-Null
Write-Host "check.ps1 : OK"
