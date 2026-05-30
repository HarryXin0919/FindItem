# 生成自签 HTTPS 证书
#
# 用 openssl(Git for Windows 自带:C:\Program Files\Git\usr\bin\openssl.exe)
# 用法:
#   .\scripts\gen-certs.ps1
# 结果:
#   certs\server.crt  certs\server.key

$ErrorActionPreference = "Stop"

$openssl = (Get-Command openssl -ErrorAction SilentlyContinue).Source
if (-not $openssl) {
    Write-Error "需要 openssl。最简单的办法是装一遍 Git for Windows,装好后 openssl.exe 会自动加入 PATH。"
    exit 1
}

$root  = Split-Path -Parent $PSScriptRoot
$certs = Join-Path $root "certs"
New-Item -ItemType Directory -Force -Path $certs | Out-Null

& $openssl req -x509 -newkey rsa:2048 -nodes `
    -keyout (Join-Path $certs "server.key") `
    -out    (Join-Path $certs "server.crt") `
    -days 365 `
    -subj "/CN=findit-local" `
    -addext "subjectAltName=DNS:localhost,DNS:findit-local,IP:127.0.0.1"

Write-Host "✓ 证书已生成:" -ForegroundColor Green
Write-Host "    $certs\server.crt"
Write-Host "    $certs\server.key"
Write-Host ""
Write-Host "提示:这是自签证书。手机首次打开 https://<笔记本-IP>:8443 会弹安全警告,选'继续访问'即可。" -ForegroundColor Yellow
