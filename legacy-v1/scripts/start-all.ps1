# 一键启动:Mosquitto + FastAPI(HTTPS)
#
# 用法:
#   .\scripts\start-all.ps1
#
# 前置:
#   1. 已 pip install -r requirements.txt 进 .venv
#   2. 已跑过 .\scripts\init-mqtt-passwd.ps1
#   3. 已跑过 .\scripts\gen-certs.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# ---- 1. Mosquitto ----
$brokerLog = Join-Path $root "runtime\mosquitto.log"
New-Item -ItemType Directory -Force -Path (Split-Path $brokerLog) | Out-Null
Write-Host "→ 启动 Mosquitto(日志: $brokerLog)" -ForegroundColor Cyan
$broker = Start-Process mosquitto `
    -ArgumentList @("-c","mosquitto\mosquitto.conf","-v") `
    -WorkingDirectory $root `
    -RedirectStandardOutput $brokerLog `
    -RedirectStandardError  ([System.IO.Path]::ChangeExtension($brokerLog, ".err.log")) `
    -PassThru -WindowStyle Hidden
Write-Host "   PID=$($broker.Id) (停止: Stop-Process -Id $($broker.Id))" -ForegroundColor DarkGray
Start-Sleep -Seconds 1

# ---- 2. FastAPI(HTTPS) ----
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Warning ".venv 不在 $py。先运行: python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt"
    exit 1
}
$cert = Join-Path $root "certs\server.crt"
$key  = Join-Path $root "certs\server.key"
if (-not (Test-Path $cert) -or -not (Test-Path $key)) {
    Write-Warning "证书缺失。先运行 .\scripts\gen-certs.ps1"
    exit 1
}

Write-Host "→ 启动 FastAPI: https://0.0.0.0:8443" -ForegroundColor Cyan
Push-Location $root
try {
    & $py -m uvicorn backend.app.main:app `
        --host 0.0.0.0 --port 8443 `
        --ssl-keyfile  $key `
        --ssl-certfile $cert
} finally {
    Pop-Location
    Write-Host "→ 关闭 Mosquitto..." -ForegroundColor Cyan
    Stop-Process -Id $broker.Id -ErrorAction SilentlyContinue
}
