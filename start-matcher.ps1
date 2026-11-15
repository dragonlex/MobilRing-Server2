param(
    [switch]$Once,
    [switch]$NoLlm,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
Push-Location $PSScriptRoot
try {
    $args = @("matcher.py")
    if ($Once) { $args += "--once" }
    if ($NoLlm) { $args += "--no-llm" }
    if ($DryRun) { $args += "--dry-run" }
    python @args
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
