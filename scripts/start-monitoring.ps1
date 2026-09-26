$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Root '.venv'
$env:PYTHONPATH = Join-Path $Root 'backend'
$env:PYTHONIOENCODING = 'utf-8'
Push-Location $Root
try {
  uv run python scripts/setup_monitoring.py
  if ($LASTEXITCODE -ne 0) { throw 'Monitoring credential setup failed' }
  docker compose -p knowforge-monitoring -f deploy/docker-compose.monitoring.yaml up -d
  if ($LASTEXITCODE -ne 0) { throw 'Monitoring startup failed' }
  Write-Host 'Grafana: http://127.0.0.1:3000 | Prometheus: http://127.0.0.1:9090'
  Write-Host 'Grafana user: admin. Password: data/monitoring/grafana-password.txt (not printed).'
  Write-Host 'Run dev.ps1 after initial credential setup so the API loads the metrics token.'
} finally {
  Pop-Location
}
