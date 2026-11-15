# start-assist.ps1 — запуск помощника по описаниям (assist.py)
#
# Сервис слушает порт из config.json (по умолчанию 5002) и ходит в KoboldCpp
# на 127.0.0.1:5001. По умолчанию слушает все интерфейсы, чтобы телефон
# в той же Wi-Fi сети мог обратиться к нему по адресу компьютера.
#
# Примеры:
#   .\start-assist.ps1
#   .\start-assist.ps1 -Port 5002 -Host 0.0.0.0

param(
    [string]$HostAddr = "",
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$script = Join-Path $root "assist.py"

if (-not (Test-Path -LiteralPath $script)) {
    Write-Host "Не найден assist.py рядом со скриптом: $script"
    exit 1
}

$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) {
    Write-Host "Python не найден в PATH — установите Python 3.10+"
    exit 1
}

$argsList = @($script)
if ($HostAddr) { $argsList += @("--host", $HostAddr) }
if ($Port -gt 0) { $argsList += @("--port", $Port) }

Write-Host "Запускаю помощника описаний. Остановить — Ctrl+C."
Write-Host "Проверка живости: http://127.0.0.1:$(if ($Port -gt 0) { $Port } else { 5002 })/health"
& $python.Source @argsList
