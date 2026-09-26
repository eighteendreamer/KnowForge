$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
docker compose -p knowforge-monitoring -f "$Root/deploy/docker-compose.monitoring.yaml" stop
if ($LASTEXITCODE -ne 0) { throw 'Failed to stop monitoring containers' }
docker compose -f "$Root/deploy/docker-compose.dev.yaml" stop qdrant
if ($LASTEXITCODE -ne 0) { throw 'Failed to stop KnowForge Qdrant' }
Write-Host 'KnowForge containers stopped; data volumes and bigdata-major were not changed. Use Ctrl+C in the dev.ps1 window to stop local app processes.'
