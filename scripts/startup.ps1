# Tâche planifiée Windows « au démarrage » (différée de 3 min) : relance la pile et signale le redémarrage.
Start-Sleep -Seconds 180
Set-Location (Split-Path $PSScriptRoot -Parent)
docker compose up -d
$key = (Get-Content .env | Where-Object { $_ -match '^HEALTHCHECKS_PING_KEY=' }) -replace '^HEALTHCHECKS_PING_KEY=', ''
if ($key) { Invoke-WebRequest -UseBasicParsing "https://hc-ping.com/$key/pc-startup?create=1" | Out-Null }
