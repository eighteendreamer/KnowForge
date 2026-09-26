$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Root '.venv'
$env:PYTHONPATH = Join-Path $Root 'backend'
$env:PYTHONIOENCODING = 'utf-8'
Set-Location $Root
uv run ruff check backend scripts sdk/python
if ($LASTEXITCODE -ne 0) { throw 'Ruff checks failed' }
uv run ruff format --check backend scripts sdk/python
if ($LASTEXITCODE -ne 0) { throw 'Formatting checks failed' }
uv run mypy backend/app sdk/python/knowforge_sdk
if ($LASTEXITCODE -ne 0) { throw 'Python type checks failed' }
npm --prefix sdk/javascript test
if ($LASTEXITCODE -ne 0) { throw 'JavaScript SDK tests failed' }
uv run python -m pytest backend/tests --cov=backend/app --cov-report=term-missing --cov-fail-under=70
if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed' }
uv run alembic -c backend/alembic.ini check
if ($LASTEXITCODE -ne 0) { throw 'Database schema drift detected' }
if (Test-Path 'frontend/package.json') {
  npm --prefix frontend run lint
  if ($LASTEXITCODE -ne 0) { throw 'Frontend lint failed' }
  npm --prefix frontend run test
  if ($LASTEXITCODE -ne 0) { throw 'Frontend tests failed' }
  npm --prefix frontend run build
  if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
}
if (Test-Path 'frontend-portal/package.json') {
  npm --prefix frontend-portal run lint
  if ($LASTEXITCODE -ne 0) { throw 'Portal lint failed' }
  npm --prefix frontend-portal run test
  if ($LASTEXITCODE -ne 0) { throw 'Portal tests failed' }
  npm --prefix frontend-portal run build
  if ($LASTEXITCODE -ne 0) { throw 'Portal build failed' }
}
