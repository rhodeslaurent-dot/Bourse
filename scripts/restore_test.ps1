# Test de restauration mensuel (docs/14 §14.2) : restaure le dernier dump dans une base `bourse_restore_test`.
$ErrorActionPreference = "Stop"
param([string]$Container = "postgres", [string]$User = "bourse")
$latest = Get-ChildItem backups\bourse-*.dump | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $latest) { throw "aucun dump trouvé dans backups/" }
docker cp $latest.FullName "${Container}:/tmp/restore.dump"
docker exec $Container psql -U $User -c "DROP DATABASE IF EXISTS bourse_restore_test;"
docker exec $Container psql -U $User -c "CREATE DATABASE bourse_restore_test;"
docker exec $Container pg_restore -U $User -d bourse_restore_test /tmp/restore.dump
docker exec $Container psql -U $User -d bourse_restore_test -c "SELECT count(*) AS jobs_runs FROM jobs_runs;"
Write-Host "Restauration testée depuis $($latest.Name)"
