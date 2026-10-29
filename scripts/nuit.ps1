# Traitements de nuit du 29/10/2026, journalisés dans data/research/nuit_<horodatage>.log ; une étape en échec
# n'arrête pas les suivantes. À lancer seulement si recherche_longue.ps1 est terminé (la reconstruction l'interromprait).
#   powershell -ExecutionPolicy Bypass -File scripts\nuit.ps1              (mise à jour, nettoyage, diagnostics)
#   powershell -ExecutionPolicy Bypass -File scripts\nuit.ps1 -Recherche   (et relance de recherche_longue -Run 4,
#                                                                          seulement si le premier passage a échoué)
param([switch]$Recherche)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force -Path data/research | Out-Null
$log = "data/research/nuit_$(Get-Date -Format yyyyMMdd_HHmmss).log"
$failed = @()

function Run([string]$title, [scriptblock]$cmd) {
    $line = "=== $(Get-Date -Format 'HH:mm:ss') $title"
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
    & $cmd 2>&1 | ForEach-Object {
        Write-Host "$_"
        Add-Content -Path $log -Value "$_" -Encoding utf8
    }
    $code = $LASTEXITCODE
    Add-Content -Path $log -Value "=== fin $title (code $code)" -Encoding utf8
    if ($code -ne 0) { $script:failed += $title }
}

function App([string]$title, [string[]]$cmd) {
    Run $title { docker compose exec -T app python -m app.cli @cmd }
}

Run "1. reconstruction" { docker compose up -d --build }
Start-Sleep -Seconds 60  # migrations et démarrage de l'application
App "2. nettoyage des valeurs lues dans des mots" @("mentions-recheck", "--since", "2026-10-20", "--apply")
App "3. relecture des lettres du 29/10 (consigne actuelle)" @("letters-reread", "--date", "2026-10-29")
App "4. relecture des lettres du 28/10" @("letters-reread", "--date", "2026-10-28")
App "5. trace des lettres du 29/10 après nettoyage" @("letters-trace", "--date", "2026-10-29")
App "6. archive AMF des déclarations de dirigeants (octobre)" @("insider-archive", "--from", "2026-10-01")
App "7. couverture de l'archive AMF" @("insider-archive", "--coverage-only")
if ($Recherche) {
    Run "8. recherche longue (n° 2, résultats, hausses, n° 3)" {
        powershell -ExecutionPolicy Bypass -File scripts\recherche_longue.ps1 -Run 4
    }
}
if ($failed.Count -gt 0) {
    $msg = "terminé avec $($failed.Count) étape(s) en échec : $($failed -join ' ; ') — journal : $log"
    Write-Host $msg
    Add-Content -Path $log -Value $msg -Encoding utf8
    exit 1
}
Write-Host "terminé : journal complet dans $log"
