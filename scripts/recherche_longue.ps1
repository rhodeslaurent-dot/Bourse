# Enchaîne les traitements longs de la recherche (docs/33, docs/34) en une seule fois, chaque étape journalisée dans
# data/research/longue_<horodatage>.log. Une étape en échec n'arrête pas les suivantes qui n'en dépendent pas.
# Lancement (plusieurs heures, de préférence la nuit) :
#   powershell -ExecutionPolicy Bypass -File scripts\recherche_longue.ps1            (refait le backtest)
#   powershell -ExecutionPolicy Bypass -File scripts\recherche_longue.ps1 -Run 4     (réutilise le backtest n° 4)
# Ordre :
#  1. backtest européen de référence (New York, Londres et Zurich jamais lus : données de test du n° 2) ;
#  2. calibration du plan n° 2 sur ce backtest, puis son exécution (refusée par le banc si la calibration est INVALIDE) ;
#  3. historique des publications de résultats depuis 2016 (dates et BPA, aucun cours lu) et sa couverture ;
#  4. étude des hausses manquées (exploratoire, places européennes, depuis 2017) ;
#  5. calibration du plan n° 3 sur le même backtest, puis son exécution.
# Lecture des rapports dans cet ordre : n° 2 (placebo X1 d'abord), étude des hausses, n° 3 (placebo X1 d'abord).
param([int]$Run = 0)  # -Run 4 : backtest de référence déjà fait (n° 4) — on le réutilise au lieu d'en refaire un
$ErrorActionPreference = "Continue"
# docker writes UTF-8: decoded with the console code page the log showed « Ã© » instead of « é »
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$failed = @()
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force -Path data/research | Out-Null
$log = "data/research/longue_$(Get-Date -Format yyyyMMdd_HHmmss).log"

function Step([string]$title, [string[]]$cmd) {
    $line = "=== $(Get-Date -Format 'HH:mm:ss') $title : $($cmd -join ' ')"
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
    # every line goes to the screen and to the log; nothing goes to the pipeline, so the function returns the code only
    & docker compose exec -T app python -m app.cli @cmd 2>&1 | ForEach-Object {
        Write-Host "$_"
        Add-Content -Path $log -Value "$_" -Encoding utf8
    }
    $code = $LASTEXITCODE
    Add-Content -Path $log -Value "=== fin $title (code $code)" -Encoding utf8
    if ($code -ne 0) { $script:failed += $title }
    return $code
}

$reg2 = "research/prereg/2026-10-28_reg2.yaml"
$reg3 = "research/prereg/2026-10-28_reg3.yaml"

$runArgs = @()
if ($Run -gt 0) {
    $bt = 0
    $runArgs = @("--run", "$Run")
    Write-Host "backtest de référence n° $Run réutilisé"
} else {
    $bt = Step "1. backtest européen" @("backtest", "--from", "2017-01-02")
}
# the calibration is bound to the data end: it is redone right before each run (new bars arrive every evening)
if ($bt -eq 0) {
    if ((Step "2a. calibration n° 2" (@("research", "calibrate", "--prereg", $reg2) + $runArgs)) -eq 0) {
        Step "2b. exécution n° 2" (@("research", "run", "--universe", "all", "--prereg", $reg2) + $runArgs) | Out-Null
    }
} else {
    Write-Host "backtest en échec : calibrations et exécutions n° 2 et n° 3 non lancées"
}
Step "3. historique des publications" @("earnings-history", "--from", "2016-01-01") | Out-Null
Step "4. étude des hausses" @("moves-study", "--from", "2017-01-02") | Out-Null
if ($bt -eq 0) {
    if ((Step "5a. calibration n° 3" (@("research", "calibrate", "--prereg", $reg3) + $runArgs)) -eq 0) {
        Step "5b. exécution n° 3" (@("research", "run", "--universe", "all", "--prereg", $reg3) + $runArgs) | Out-Null
    }
}
if ($failed.Count -gt 0) {
    $msg = "terminé avec $($failed.Count) étape(s) en échec : $($failed -join ' ; ') — journal complet dans $log"
    Write-Host $msg
    Add-Content -Path $log -Value $msg -Encoding utf8
    exit 1
}
Write-Host "terminé : journal complet dans $log"
