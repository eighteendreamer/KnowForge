param(
    [string]$OutputRoot = '',
    [switch]$SkipQdrant
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

# The database and Redis run inside bigdata-major; the app reaches them through published ports on 127.0.0.1.
$dbUrl = Read-Env 'KNOFORGE_DATABASE_URL'
if ($dbUrl -notmatch '://([^:]+):([^@]*)@[^/]*/([^?]+)') { throw 'KNOFORGE_DATABASE_URL is not parseable' }
$dbUser, $dbPassword, $dbName = $Matches[1], $Matches[2], $Matches[3]
# qdrant_collection is not editable at runtime, so the app's own settings are authoritative.
$collection = (uv run python -c 'from app.core.config import Settings; print(Settings().qdrant_collection)') -join ''
if (-not $collection) { throw 'Unable to read the Qdrant collection name' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
if (-not $OutputRoot) { $OutputRoot = Join-Path $Root "data\backup\$stamp" }
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null

Write-Host "[1/4] PostgreSQL pg_dump ($dbName)"
$dumpName = "$dbName-$stamp.dump"
docker exec -e "PGPASSWORD=$dbPassword" bigdata-major pg_dump -h 127.0.0.1 -U $dbUser -d $dbName -Fc -f "/tmp/$dumpName"
if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed' }
docker cp "bigdata-major:/tmp/$dumpName" (Join-Path $OutputRoot $dumpName)
if ($LASTEXITCODE -ne 0) { throw 'docker cp of the dump failed' }
docker exec bigdata-major rm "-f" "/tmp/$dumpName"

Write-Host "[2/4] Object storage (data\storage)"
$storageZip = Join-Path $OutputRoot "storage-$stamp.zip"
$stored = Get-ChildItem (Join-Path $Root 'data\storage') -Recurse -File | Where-Object { $_.Name -ne '.gitkeep' }
Compress-Archive -Path (Join-Path $Root 'data\storage\*') -DestinationPath $storageZip -Force

Write-Host "[3/4] Qdrant collection snapshot ($collection)"
$snapshotName = ''
if (-not $SkipQdrant) {
    $created = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:6333/collections/$collection/snapshots" -TimeoutSec 600
    $snapshotName = $created.result.name
    if (-not $snapshotName) { throw 'Qdrant did not report a snapshot name' }
    docker cp "knowforge-qdrant:/qdrant/snapshots/$collection/$snapshotName" (Join-Path $OutputRoot $snapshotName)
    if ($LASTEXITCODE -ne 0) { throw 'docker cp of the Qdrant snapshot failed' }
    docker exec knowforge-qdrant rm "-f" "/qdrant/snapshots/$collection/$snapshotName"
}

Write-Host '[4/4] Manifest'
$revision = (uv run alembic -c backend/alembic.ini heads | Select-Object -First 1) -replace '\s.*', ''
$stats = Invoke-RestMethod -Uri "http://127.0.0.1:6333/collections/$collection" -TimeoutSec 30
$manifest = [ordered]@{
    created_at = (Get-Date).ToUniversalTime().ToString('o')
    alembic_head = $revision
    database = $dbName
    storage_files = $stored.Count
    collection = $collection
    points_count = $stats.result.points_count
    database_dump = $dumpName
    storage_archive = (Split-Path $storageZip -Leaf)
    qdrant_snapshot = $snapshotName
    qdrant_snapshot_restore = 'not supported by this Qdrant build; rebuild the index through the admin rebuild flow'
}
$manifest | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $OutputRoot 'manifest.json')
Write-Host "Backup written to $OutputRoot"
$manifest | ConvertTo-Json
