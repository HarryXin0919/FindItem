# 创建 Mosquitto 密码文件
#
# 用 mosquitto 自带的 mosquitto_passwd 工具,把密码哈希后写进 mosquitto\passwords
# 用法:
#   .\scripts\init-mqtt-passwd.ps1
# 默认账号: findit_backend / findit123
# 改密请同步改 ESP32 .ino 和后端环境变量。

$ErrorActionPreference = "Stop"

$tool = (Get-Command mosquitto_passwd -ErrorAction SilentlyContinue).Source
if (-not $tool) {
    Write-Error "找不到 mosquitto_passwd。请先安装 Mosquitto: https://mosquitto.org/download/"
    exit 1
}

$root   = Split-Path -Parent $PSScriptRoot
$pwfile = Join-Path $root "mosquitto\passwords"
New-Item -ItemType Directory -Force -Path (Split-Path $pwfile) | Out-Null

# -c 创建新文件(覆盖); -b 把密码当参数(非交互)
& $tool -c -b $pwfile findit_backend findit123

Write-Host "✓ MQTT 密码文件已生成: $pwfile" -ForegroundColor Green
Write-Host "  默认账号 findit_backend / findit123(哈希后写入)"
