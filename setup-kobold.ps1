# setup-kobold.ps1
# Скачивает KoboldCpp и модель GGUF, чтобы «поиск совпадений» заработал с нуля.
#
# Примеры:
#   .\setup-kobold.ps1                    # последняя версия KoboldCpp + модель по умолчанию
#   .\setup-kobold.ps1 -SkipModel         # только KoboldCpp
#   .\setup-kobold.ps1 -SkipKobold        # только модель
#   .\setup-kobold.ps1 -KoboldVersion v1.124.2
#   .\setup-kobold.ps1 -ModelRepo bartowski/Qwen2.5-3B-Instruct-GGUF -ModelFile Qwen2.5-3B-Instruct-Q4_K_M.gguf
#   .\setup-kobold.ps1 -Force             # перекачать, даже если файлы уже есть

param(
    [string]$KoboldVersion = "",
    [string]$ModelRepo = "bartowski/Qwen2.5-7B-Instruct-1M-GGUF",
    [string]$ModelFile = "Qwen2.5-7B-Instruct-1M-Q4_K_M.gguf",
    [switch]$SkipKobold,
    [switch]$SkipModel,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$root = $PSScriptRoot
$aiDir = Join-Path $root "ai"
$modelDir = Join-Path $root "models"
New-Item -ItemType Directory -Force -Path $aiDir, $modelDir | Out-Null

function Write-Step($text) {
    Write-Host ""
    Write-Host "== $text" -ForegroundColor Cyan
}

function Write-Ok($text) {
    Write-Host "   $text" -ForegroundColor Green
}

function Write-Warn($text) {
    Write-Host "   $text" -ForegroundColor Yellow
}

function Get-FreeSpaceGb {
    $drive = (Get-Item -LiteralPath $root).PSDrive
    return [math]::Round((Get-PSDrive -Name $drive.Name).Free / 1GB, 1)
}

# Скачивание с прогрессом: aria2c (если рядом лежит), иначе BITS, иначе .NET.
function Invoke-Download {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    $aria2 = Join-Path $aiDir "aria2c-win.exe"
    $part = "$Destination.part"
    if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $part -Force }

    if (Test-Path -LiteralPath $aria2) {
        Write-Host "   качаю через aria2c…"
        & $aria2 --dir (Split-Path -Parent $Destination) `
                 --out (Split-Path -Leaf $Destination) `
                 --continue=true --max-connection-per-server=4 --split=4 `
                 --allow-overwrite=true --auto-file-renaming=false $Url
        if ($LASTEXITCODE -ne 0) { throw "aria2c завершился с кодом $LASTEXITCODE" }
        return
    }

    try {
        Import-Module BitsTransfer -ErrorAction Stop
        Write-Host "   качаю через BITS (виден прогресс в системе)…"
        Start-BitsTransfer -Source $Url -Destination $Destination -Description "MobilRing setup"
        return
    } catch {
        Write-Warn "BITS недоступен, качаю обычным способом: $($_.Exception.Message)"
    }

    # Fallback: .NET WebClient с выводом процентов.
    $client = New-Object System.Net.WebClient
    $client.Headers.Add("User-Agent", "mobilring-setup")
    $lastPercent = -1
    $handler = {
        param($sender, $eventArgs)
        if ($eventArgs.TotalBytesToReceive -gt 0) {
            $percent = [int](100 * $eventArgs.BytesReceived / $eventArgs.TotalBytesToReceive)
            if ($percent -ne $script:lastPercent -and $percent % 5 -eq 0) {
                $script:lastPercent = $percent
                $have = [math]::Round($eventArgs.BytesReceived / 1MB)
                $total = [math]::Round($eventArgs.TotalBytesToReceive / 1MB)
                Write-Host ("   {0}% ({1} из {2} МБ)" -f $percent, $have, $total)
            }
        }
    }
    Register-ObjectEvent -InputObject $client -EventName DownloadProgressChanged -Action $handler | Out-Null
    try {
        $client.DownloadFile($Url, $Destination)
    } finally {
        Get-EventSubscriber | Where-Object { $_.SourceObject -eq $client } | Unregister-Event
        $client.Dispose()
    }
}

# --- 1. KoboldCpp ---------------------------------------------------------

if (-not $SkipKobold) {
    Write-Step "KoboldCpp"

    $exePath = Join-Path $aiDir "koboldcpp.exe"
    if ((Test-Path -LiteralPath $exePath) -and -not $Force) {
        Write-Ok "уже установлен: $exePath"
    } else {
        $api = if ($KoboldVersion) {
            "https://api.github.com/repos/LostRuins/koboldcpp/releases/tags/$KoboldVersion"
        } else {
            "https://api.github.com/repos/LostRuins/koboldcpp/releases/latest"
        }

        Write-Host "   узнаю версию релиза…"
        $release = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "mobilring-setup" }
        $asset = $release.assets | Where-Object { $_.name -eq "koboldcpp.exe" } | Select-Object -First 1
        if (-not $asset) { throw "в релизе $($release.tag_name) нет файла koboldcpp.exe" }

        $sizeMb = [math]::Round($asset.size / 1MB, 0)
        Write-Ok "версия $($release.tag_name), файл $sizeMb МБ"

        $free = Get-FreeSpaceGb
        if ($free -lt ($sizeMb / 1024 + 1)) { throw "мало места: свободно $free ГБ" }

        Invoke-Download -Url $asset.browser_download_url -Destination $exePath

        if (-not (Test-Path -LiteralPath $exePath)) { throw "koboldcpp.exe не появился после скачивания" }
        Write-Ok "готово: $exePath"

        $oldLauncher = Join-Path $aiDir "koboldcpp-launcher.exe"
        if (Test-Path -LiteralPath $oldLauncher) {
            Write-Warn "рядом лежит старый koboldcpp-launcher.exe — start-kobold.ps1 предпочтёт новый koboldcpp.exe"
        }
    }
}

# --- 2. Модель ------------------------------------------------------------

if (-not $SkipModel) {
    Write-Step "Модель"

    $modelPath = Join-Path $modelDir $ModelFile
    if ((Test-Path -LiteralPath $modelPath) -and -not $Force) {
        $haveGb = [math]::Round((Get-Item -LiteralPath $modelPath).Length / 1GB, 2)
        Write-Ok "уже скачана: $modelPath ($haveGb ГБ)"
    } else {
        $url = "https://huggingface.co/$ModelRepo/resolve/main/$ModelFile?download=true"
        Write-Host "   источник: $ModelRepo/$ModelFile"

        $head = $null
        try {
            $head = Invoke-WebRequest -Uri $url -Method Head -MaximumRedirection 5 -UseBasicParsing `
                -Headers @{ "User-Agent" = "mobilring-setup" }
        } catch {
            Write-Warn "не удалось узнать размер заранее: $($_.Exception.Message)"
        }

        if ($head) {
            $length = $head.Headers["Content-Length"]
            if ($length -is [array]) { $length = $length[0] }
            $needGb = [math]::Round([double]$length / 1GB, 2)
            $free = Get-FreeSpaceGb
            Write-Ok "размер $needGb ГБ, свободно $free ГБ"
            if ($free -lt ($needGb + 1)) {
                throw "мало места: нужно $needGb ГБ плюс запас, свободно $free ГБ"
            }
        }

        Invoke-Download -Url $url -Destination $modelPath

        $size = (Get-Item -LiteralPath $modelPath).Length
        if ($size -lt 100MB) { throw "файл модели подозрительно маленький: $size байт" }
        Write-Ok "готово: $modelPath ($([math]::Round($size / 1GB, 2)) ГБ)"
    }
}

# --- 3. Итог --------------------------------------------------------------

Write-Step "Проверка"
$exeOk = (Test-Path -LiteralPath (Join-Path $aiDir "koboldcpp.exe")) -or
         (Test-Path -LiteralPath (Join-Path $aiDir "koboldcpp-launcher.exe"))
$models = @(Get-ChildItem -LiteralPath $modelDir -Filter *.gguf -ErrorAction SilentlyContinue)

Write-Host ("   KoboldCpp:  {0}" -f $(if ($exeOk) { "есть" } else { "НЕТ" }))
Write-Host ("   модели:     {0}" -f $(if ($models.Count -gt 0) { ($models | ForEach-Object { $_.Name }) -join ", " } else { "нет" }))

if ($exeOk -and $models.Count -gt 0) {
    Write-Host ""
    Write-Host "Готово. Дальше:" -ForegroundColor Green
    Write-Host "   .\start-kobold.ps1     # отдельное окно с моделью"
    Write-Host "   .\start-matcher.ps1    # поиск совпадений"
    if ($models.Count -gt 0) {
        Write-Host ""
        Write-Host ("Если start-kobold.ps1 не нашёл модель, укажите её явно: " +
                    ".\start-kobold.ps1 -Model models\$(($models | Select-Object -First 1).Name)")
    }
} else {
    Write-Warn "чего-то не хватает — смотрите сообщения выше"
    exit 1
}
