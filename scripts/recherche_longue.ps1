# Enchaîne les traitements longs de la recherche (docs/33, docs/34) en une seule fois, chaque étape journalisée dans
# data/research/longue_<horodatage>.log. Une étape en échec n'arrête pas les suivantes qui n'en dépendent pas.
# Lancement (plusieurs heures, de préférence la nuit) :
#   powershell -ExecutionPolicy Bypass -File scripts\recherche_longue.ps1
# Ordre :
#  1. backtest européen de référence (New York, Londres et Zurich jamais lus : données de test du n° 2) ;
#  2. calibration du plan n° 2 sur ce backtest, puis son exécution (refusée par le banc si la calibration est INVALIDE) ;
#  3. historique des publications de résultats depuis 2016 (dates et BPA, aucun cours lu) et sa couverture ;
#  4. étude des hausses manquées (exploratoire, places européennes, depuis 2017) ;
#  5. calibration du plan n° 3 sur le même backtest, puis son exécution.
# Lecture des rapports dans cet ordre : n° 2 (placebo X1 d'abord), étude des hausses, n° 3 (placebo X1 d'abord).
$ErrorActionPreference = "Continue"
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
    return $code
}

$reg2 = "research/prereg/2026-10-28_reg2.yaml"
$reg3 = "research/prereg/2026-10-28_reg3.yaml"

$bt = Step "1. backtest européen" @("backtest", "--from", "2017-01-02")
if ($bt -eq 0) {
    if ((Step "2a. calibration n° 2" @("research", "calibrate", "--prereg", $reg2)) -eq 0) {
        Step "2b. exécution n° 2" @("research", "run", "--universe", "all", "--prereg", $reg2) | Out-Null
    }
} else {
    Write-Host "backtest en échec : calibrations et exécutions n° 2 et n° 3 non lancées"
}
Step "3. historique des publications" @("earnings-history", "--from", "2016-01-01") | Out-Null
Step "4. étude des hausses" @("moves-study", "--from", "2017-01-02") | Out-Null
if ($bt -eq 0) {
    if ((Step "5a. calibration n° 3" @("research", "calibrate", "--prereg", $reg3)) -eq 0) {
        Step "5b. exécution n° 3" @("research", "run", "--universe", "all", "--prereg", $reg3) | Out-Null
    }
}
Write-Host "terminé : journal complet dans $log"
