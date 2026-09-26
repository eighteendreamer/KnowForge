param([int]$ApiPort = 8000, [int]$FrontendPort = 5173, [int]$PortalPort = 5174)
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Root '.venv'
$env:PYTHONPATH = Join-Path $Root 'backend'
$env:PYTHONIOENCODING = 'utf-8'
$Python = Join-Path $Root '.venv/Scripts/python.exe'
$Vite = Join-Path $Root 'frontend/node_modules/vite/bin/vite.js'
$PortalVite = Join-Path $Root 'frontend-portal/node_modules/vite/bin/vite.js'
foreach ($Port in @($ApiPort, $FrontendPort, $PortalPort)) {
  if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use; stop its owner or choose another port."
  }
}
if (!(Test-Path $Python) -or !(Test-Path $Vite) -or !(Test-Path $PortalVite)) { throw 'Run scripts/setup.ps1 first.' }
$RunId = Get-Date -Format 'yyyyMMdd-HHmmss-ffff'
$RunDir = Join-Path $Root "data/runtime/$RunId"
$MetricDir = Join-Path $RunDir 'metrics'
New-Item -ItemType Directory -Path $MetricDir -Force | Out-Null
$env:PROMETHEUS_MULTIPROC_DIR = $MetricDir
$Processes = @()
try {
  $Processes += Start-Process $Python -ArgumentList @('-m', 'uvicorn', 'app.main:create_app', '--factory', '--host', '127.0.0.1', '--port', $ApiPort) -WorkingDirectory $Root -RedirectStandardOutput "$RunDir/api.log" -RedirectStandardError "$RunDir/api-error.log" -PassThru -NoNewWindow
  $Processes += Start-Process $Python -ArgumentList @('-m', 'celery', '-A', 'app.worker.celery_app:celery_app', 'worker', '--pool=solo', '--concurrency=1', '--loglevel=INFO') -WorkingDirectory $Root -RedirectStandardOutput "$RunDir/worker.log" -RedirectStandardError "$RunDir/worker-error.log" -PassThru -NoNewWindow
  $Processes += Start-Process $Python -ArgumentList @('-m', 'celery', '-A', 'app.worker.celery_app:celery_app', 'beat', '--loglevel=INFO', '--schedule', "`"$RunDir/celerybeat`"") -WorkingDirectory $Root -RedirectStandardOutput "$RunDir/beat.log" -RedirectStandardError "$RunDir/beat-error.log" -PassThru -NoNewWindow
  $env:VITE_API_TARGET = "http://127.0.0.1:$ApiPort"
  $Processes += Start-Process (Get-Command node).Source -ArgumentList @("`"$Vite`"", '--host', '127.0.0.1', '--port', $FrontendPort, '--strictPort') -WorkingDirectory (Join-Path $Root 'frontend') -RedirectStandardOutput "$RunDir/frontend.log" -RedirectStandardError "$RunDir/frontend-error.log" -PassThru -NoNewWindow
  $Processes += Start-Process (Get-Command node).Source -ArgumentList @("`"$PortalVite`"", '--host', '127.0.0.1', '--port', $PortalPort, '--strictPort') -WorkingDirectory (Join-Path $Root 'frontend-portal') -RedirectStandardOutput "$RunDir/portal.log" -RedirectStandardError "$RunDir/portal-error.log" -PassThru -NoNewWindow
  Write-Host "Admin: http://127.0.0.1:$FrontendPort | Portal: http://127.0.0.1:$PortalPort | API: http://127.0.0.1:$ApiPort"
  Write-Host "Logs and shared metrics: $RunDir. Ctrl+C stops only these child processes."
  while ($true) {
    foreach ($Process in $Processes) {
      if ($Process.HasExited) { throw "Process $($Process.Id) exited; inspect $RunDir." }
    }
    Start-Sleep -Seconds 1
  }
} finally {
  foreach ($Process in $Processes) {
    if (!$Process.HasExited) { Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue }
  }
}
