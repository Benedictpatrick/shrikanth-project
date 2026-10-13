# One-command launcher (Windows PowerShell): installs dependencies on first run, builds the UI, starts the API.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
  python -m venv (Join-Path $root 'backend\.venv')
  & $py -m pip install -q -r (Join-Path $root 'backend\requirements.txt')
}
if (-not (Test-Path (Join-Path $root 'frontend\dist'))) {
  Push-Location (Join-Path $root 'frontend')
  npm install --no-audit --no-fund
  npm run build
  Pop-Location
}
Write-Host 'Legacy Code Automation -> http://127.0.0.1:8000' -ForegroundColor Yellow
Push-Location (Join-Path $root 'backend')
& $py -m uvicorn app.main:app --port 8000
Pop-Location
