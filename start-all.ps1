param(
    [switch]$NoLlm
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

if (-not $NoLlm) {
    Write-Host "[1/3] Launching local LLM (llama-server on port 5001)..."
    Start-Process powershell -WorkingDirectory $root -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $root "start-llama.ps1")
    )
    Start-Sleep -Seconds 4
} else {
    Write-Host "[1/3] Skipping LLM (-NoLlm enabled)."
}

Write-Host "[2/3] Launching AI Description Assistant (assist.py on port 5002)..."
Start-Process powershell -WorkingDirectory $root -ArgumentList @(
    "-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $root "start-assist.ps1")
)

Start-Sleep -Seconds 2

Write-Host "[3/3] Launching AI Ad Matcher (matcher.py loop)..."
$matcherArgs = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $root "start-matcher.ps1"))
if ($NoLlm) { $matcherArgs += "-NoLlm" }
Start-Process powershell -WorkingDirectory $root -ArgumentList $matcherArgs

Write-Host ""
Write-Host "MobilRing-Server2 services started in separate windows:"
Write-Host "  - LLM API:    http://127.0.0.1:5001/health"
Write-Host "  - Assist API: http://127.0.0.1:5002/health"
Write-Host "  - Matcher:    polling Firebase Realtime Database"
