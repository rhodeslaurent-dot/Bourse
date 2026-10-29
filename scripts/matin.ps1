# Mesures du 30/10/2026 sur l'historique du PC, journalisées dans data/research/matin_<horodatage>.log ; une étape en
# échec n'arrête pas les suivantes. Avant : git pull, puis (fenêtre séparée) params-init --update.
#   powershell -ExecutionPolicy Bypass -File scripts\matin.ps1                 (mesures rapides puis longues)
#   powershell -ExecutionPolicy Bypass -File scripts\matin.ps1 -Dirigeants     (et l'historique AMF depuis 11/2021 : long)
#   powershell -ExecutionPolicy Bypass -File scripts\matin.ps1 -SansReconstruction
# Ordre : rapides d'abord (verrous, lettres, propositions), puis longues (rejeu 2017-2026, plan n° 4). Les plans n° 4 et
# n° 5 sont gelés (docs/34 §34.11, §34.12) avant toute lecture de ces sorties.
param([switch]$Dirigeants, [switch]$SansReconstruction, [int]$Run = 4)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force -Path data/research | Out-Null
$log = "data/research/matin_$(Get-Date -Format yyyyMMdd_HHmmss).log"
$failed = @()

function Head([string]$title) {
    $line = "=== $(Get-Date -Format 'HH:mm:ss') $title"
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

function Done([string]$title, [int]$code) {
    Add-Content -Path $log -Value "=== fin $title (code $code)" -Encoding utf8
    if ($code -ne 0) { $script:failed += $title }
}

function App([string]$title, [string[]]$cliArgs) {
    Head $title
    & docker compose exec -T app python -m app.cli @cliArgs 2>&1 | ForEach-Object {
        Write-Host "$_"
        Add-Content -Path $log -Value "$_" -Encoding utf8
    }
    Done $title $LASTEXITCODE
}

if (-not $SansReconstruction) {
    Head "1. reconstruction"
    & docker compose up -d --build 2>&1 | ForEach-Object {
        Write-Host "$_"
        Add-Content -Path $log -Value "$_" -Encoding utf8
    }
    Done "1. reconstruction" $LASTEXITCODE
    Start-Sleep -Seconds 60  # migrations et démarrage de l'application
}
$reg4 = "research/prereg/2026-10-30_reg4.yaml"
App "2. historique des annonces macro (BCE, Fed, emploi et prix US)" @("macro-history-load", "--from", "2017-01-01")
App "3. couverture des verrous en octobre" @("gates-coverage", "--from", "2026-10-01", "--to", "2026-10-29")
App "4. étude historique des recommandations des lettres" @("letters-study", "--from", "2017-01-02")
App "5. rejeu des propositions réellement faites" @("entry-replay", "--mode", "proposals")
App "6. rejeu des politiques d'entrée 2017-2026 (environ 1 h)" @("entry-replay", "--from", "2017-01-02")
App "7a. calibration du plan n° 4" @("research", "calibrate", "--prereg", $reg4, "--run", "$Run")
App "7b. exécution du plan n° 4" @("research", "run", "--prereg", $reg4, "--universe", "all", "--run", "$Run")
if ($Dirigeants) {
    App "8. historique AMF des déclarations de dirigeants depuis 11/2021 (long)" @("insider-archive", "--from", "2021-11-01")
    App "9. couverture de l'archive AMF" @("insider-archive", "--coverage-only")
}
if ($failed.Count -gt 0) {
    $msg = "terminé avec $($failed.Count) étape(s) en échec : $($failed -join ' ; ') — journal : $log"
    Write-Host $msg
    Add-Content -Path $log -Value $msg -Encoding utf8
    exit 1
}
Write-Host "terminé : journal complet dans $log"
