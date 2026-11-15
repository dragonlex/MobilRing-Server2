param(
    [string]$ServerExe = "",
    [string]$Model = "",
    [string]$HostAddr = "127.0.0.1",
    [int]$Port = 5001,
    [int]$Context = 0,
    [int]$GpuLayers = -1
)

$ErrorActionPreference = "Stop"
$cfgPath = Join-Path $PSScriptRoot "config.json"

if (Test-Path -LiteralPath $cfgPath) {
    $cfg = Get-Content -LiteralPath $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $ServerExe -and $cfg.llama -and $cfg.llama.server_exe) {
        $ServerExe = $cfg.llama.server_exe
    }
    if (-not $Model -and $cfg.llama -and $cfg.llama.model_path) {
        $Model = $cfg.llama.model_path
    }
    if ($Context -le 0 -and $cfg.llama -and $cfg.llama.context) {
        $Context = [int]$cfg.llama.context
    }
    if ($GpuLayers -lt 0 -and $cfg.llama -and $null -ne $cfg.llama.gpu_layers) {
        $GpuLayers = [int]$cfg.llama.gpu_layers
    }
}

if ($Context -le 0) { $Context = 2048 }
if ($GpuLayers -lt 0) { $GpuLayers = 99 }

if (-not (Test-Path -LiteralPath $ServerExe)) {
    Write-Error "llama-server.exe not found: $ServerExe (check config.json -> llama.server_exe)"
    exit 1
}
if (-not (Test-Path -LiteralPath $Model)) {
    Write-Error "GGUF model not found: $Model (check config.json -> llama.model_path)"
    exit 1
}

Write-Host "Starting llama-server on http://${HostAddr}:${Port} ..."
Write-Host "Model: $Model (ctx=$Context, ngl=$GpuLayers)"

$exeDir = Split-Path -Parent $ServerExe
Push-Location $exeDir
try {
    & $ServerExe -m $Model --host $HostAddr --port $Port -c $Context -ngl $GpuLayers
} finally {
    Pop-Location
}
