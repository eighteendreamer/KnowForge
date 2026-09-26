$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Root '.venv'
$env:PYTHONPATH = Join-Path $Root 'backend'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location $Root
uv sync --locked --python (Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts/python.exe')
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed' }
uv run python scripts/download_tokenizer.py
if ($LASTEXITCODE -ne 0) { throw 'Tokenizer download failed' }
if (Test-Path 'frontend/package.json') {
  npm --prefix frontend ci
  if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
}
if (Test-Path 'frontend-portal/package.json') {
  npm --prefix frontend-portal ci
  if ($LASTEXITCODE -ne 0) { throw 'Portal dependency installation failed' }
}
