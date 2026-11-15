param(
    [string]$HostAddr = "",
    [int]$Port = 0
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$root = $PSScriptRoot
$script = Join-Path $root "assist.py"

if (-not (Test-Path -LiteralPath $script)) {
    Write-Host "assist.py not found: $script"
    exit 1
}

$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) {
    Write-Host "Python not found in PATH - install Python 3.10+"
    exit 1
}

$argsList = @($script)
if ($HostAddr) { $argsList += @("--host", $HostAddr) }
if ($Port -gt 0) { $argsList += @("--port", $Port) }

$listenPort = if ($Port -gt 0) { $Port } else { 5002 }
Write-Host "Starting AI description assistant on port $listenPort (Ctrl+C to stop)..."
Write-Host "Health check: http://127.0.0.1:$listenPort/health"
& $python.Source @argsList
