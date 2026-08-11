# One-shot local start: PostgreSQL (Docker) + FastAPI backend + Vite frontend.
#
#   .\scripts\start-all.ps1
#
# Prerequisites (first run only):
#   1. Docker Desktop running
#   2. cd backend; python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
#   3. cd backend; .\.venv\Scripts\python.exe -m app.seed
#   4. cd frontend; npm install
#
# device_mode defaults to "simulator", so the five controllers run inside the
# backend process and locate commands are acknowledged with no broker and no
# ESP32 attached. Set DEVICE_MODE=broker only once real hardware is in the loop.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# ---- 1. PostgreSQL --------------------------------------------------------
Write-Host "-> PostgreSQL (docker compose)" -ForegroundColor Cyan
docker compose -f (Join-Path $root "docker-compose.postgres.yml") up -d
if ($LASTEXITCODE -ne 0) { Write-Warning "docker compose failed - is Docker Desktop running?"; exit 1 }

# ---- 2. Backend -----------------------------------------------------------
$py = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Warning "No venv at $py. Run: cd backend; python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt"
    exit 1
}

$runtime = Join-Path $root "runtime"
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

Write-Host "-> Backend: http://127.0.0.1:8000" -ForegroundColor Cyan
$backend = Start-Process -FilePath $py `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--port", "8000") `
    -WorkingDirectory (Join-Path $root "backend") `
    -RedirectStandardOutput (Join-Path $runtime "backend.log") `
    -RedirectStandardError  (Join-Path $runtime "backend.err.log") `
    -PassThru -WindowStyle Hidden
Write-Host "   PID=$($backend.Id)  (stop: Stop-Process -Id $($backend.Id))" -ForegroundColor DarkGray

# ---- 3. Frontend ----------------------------------------------------------
if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) {
    Write-Warning "frontend/node_modules missing. Run: cd frontend; npm install"
    exit 1
}

Write-Host "-> Frontend: http://localhost:5173" -ForegroundColor Cyan
$frontend = Start-Process -FilePath "cmd.exe" `
    -ArgumentList @("/c", "npm run dev") `
    -WorkingDirectory (Join-Path $root "frontend") `
    -PassThru -WindowStyle Hidden
Write-Host "   PID=$($frontend.Id)  (stop: Stop-Process -Id $($frontend.Id))" -ForegroundColor DarkGray

Write-Host ""
Write-Host "All started. Logs in runtime\. Stop everything with:" -ForegroundColor Green
Write-Host "  Stop-Process -Id $($backend.Id),$($frontend.Id) -Force" -ForegroundColor Green
Write-Host "  docker compose -f docker-compose.postgres.yml down" -ForegroundColor Green
