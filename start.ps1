# Установка и запуск для разработки (PowerShell 7). Повторный запуск безопасен.
#   .\start.ps1            — окружение, зависимости, сборка интерфейса, сервер на http://127.0.0.1:8710
#   .\start.ps1 -Desktop   — то же, но в окне лаунчера
#   .\start.ps1 -Dev       — сервер + Vite с горячей перезагрузкой на http://127.0.0.1:5173
param([switch]$Desktop, [switch]$Dev)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$py = Join-Path $root 'server\.venv\Scripts\python.exe'

if (-not (Test-Path $py)) {
    Write-Host '→ создаю окружение Python 3.13' -ForegroundColor DarkGray
    py -3.13 -m venv (Join-Path $root 'server\.venv')
}
Write-Host '→ зависимости сервера' -ForegroundColor DarkGray
& $py -m pip install -q -e "$root\server[dev,desktop]"

Push-Location (Join-Path $root 'web')
if (-not (Test-Path node_modules)) { Write-Host '→ зависимости интерфейса' -ForegroundColor DarkGray; npm install --no-audit --no-fund }
if (-not $Dev) { Write-Host '→ сборка интерфейса' -ForegroundColor DarkGray; npm run build | Out-Null }
Pop-Location

if ($Desktop) {
    & (Join-Path $root 'server\.venv\Scripts\pythonw.exe') (Join-Path $root 'launcher\orchestra_launcher.py')
    return
}
if ($Dev) {
    Start-Process -NoNewWindow -FilePath npm -ArgumentList 'run', 'dev' -WorkingDirectory (Join-Path $root 'web')
}
Push-Location (Join-Path $root 'server')
& $py -m orchestra serve
Pop-Location
