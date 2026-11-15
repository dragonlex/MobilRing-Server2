param(
    [string]$Model = "models\qwen2.5-7b-instruct-1m-q4_k_m.gguf",
    [int]$Port = 5001,
    [int]$Context = 8192,
    [int]$GpuLayers = 99
)

$ErrorActionPreference = "Stop"
$ai = Join-Path $PSScriptRoot "ai"
$exe = Join-Path $ai "koboldcpp.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    $exe = Join-Path $ai "koboldcpp-launcher.exe"
}
if (-not (Test-Path -LiteralPath $exe)) {
    Write-Host "Не найден koboldcpp.exe в $ai — положите сборку KoboldCpp туда или запустите её вручную."
    exit 1
}

$modelPath = if ([System.IO.Path]::IsPathRooted($Model)) { $Model } else { Join-Path $PSScriptRoot $Model }
if (-not (Test-Path -LiteralPath $modelPath)) {
    Write-Host "Не найдена модель: $modelPath"
    Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot "models") -Filter *.gguf | Select-Object -ExpandProperty Name
    exit 1
}

Write-Host "Запускаю KoboldCpp: $modelPath (порт $Port)"
Push-Location $ai
try {
    & $exe --model $modelPath --port $Port --contextsize $Context --gpulayers $GpuLayers --flashattention --quiet
} finally {
    Pop-Location
}
