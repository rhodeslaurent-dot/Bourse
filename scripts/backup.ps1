# Sauvegarde manuelle (le job `backup` tourne aussi à 23:00 Réunion dans le conteneur).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
docker compose exec app python -m app.cli run-job backup
Get-ChildItem backups | Sort-Object LastWriteTime -Descending | Select-Object -First 3
