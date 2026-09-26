[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [switch]$SkipStorage
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Root '.venv'
$env:PYTHONPATH = Join-Path $Root 'backend'
Push-Location $Root

function Read-Env([string]$name) {
    $line = Get-Content (Join-Path $Root '.env') | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if (-not $line) { throw "Missing $name in .env" }
    return ($line -split '=', 2)[1].Trim()
}

$manifestPath = Join-Path $Path 'manifest.json'
if (-not (Test-Path $manifestPath)) { throw "No manifest.json under $Path" }
$manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
$dbUrl = Read-Env 'KNOFORGE_DATABASE_URL'
if ($dbUrl -notmatch '://([^:]+):([^@]*)@[^/]*/([^?]+)') { throw 'KNOFORGE_DATABASE_URL is not parseable' }
$dbUser, $dbPassword, $dbName = $Matches[1], $Matches[2], $Matches[3]

# A restore that races the live app or the worker writes half-loaded state, so require a stopped stack.
$listening = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($listening) { throw 'Stop the dev stack (Ctrl+C in scripts/dev.ps1) before restoring' }

if (-not $PSCmdlet.ShouldProcess("$dbName + data\storage", 'restore from backup')) { return }

Write-Host "[1/3] PostgreSQL pg_restore ($dbName, dump $($manifest.database_dump))"
$dump = Join-Path $Path $manifest.database_dump
if (-not (Test-Path $dump)) { throw "Missing dump $dump" }
docker cp $dump "bigdata-major:/tmp/$($manifest.database_dump)"
if ($LASTEXITCODE -ne 0) { throw 'docker cp of the dump failed' }
docker exec -e "PGPASSWORD=$dbPassword" bigdata-major pg_restore -h 127.0.0.1 -U $dbUser -d $dbName -c --if-exists "--no-owner" "/tmp/$($manifest.database_dump)"
if ($LASTEXITCODE -ne 0) { throw 'pg_restore failed' }
docker exec bigdata-major rm "-f" "/tmp/$($manifest.database_dump)"

Write-Host '[2/3] Object storage'
if (-not $SkipStorage) {
    $zip = Join-Path $Path $manifest.storage_archive
    if (-not (Test-Path $zip)) { throw "Missing storage archive $zip" }
    Expand-Archive -Path $zip -DestinationPath (Join-Path $Root 'data\storage') -Force
}

Write-Host '[3/3] Follow-up'
Write-Host "alembic head in backup: $($manifest.alembic_head)"
uv run alembic -c backend/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw 'alembic upgrade head failed' }
Write-Host "Qdrant was not restored: this build exposes no snapshot recovery endpoint.`nRebuild the index after the stack is up: 系统设置 → 全量重建 → 冻结评估通过 → 成对切换 (see docs/运行与恢复手册.md)."
Write-Host 'Then start scripts/dev.ps1 and check /health plus one known search.'
