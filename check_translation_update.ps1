param(
    [string]$Root = "",
    [switch]$SkipLaunch,
    [switch]$AutoUpdate,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$GameArgs
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$OutputEncoding = [System.Text.Encoding]::UTF8

$GithubRepo = "Devisione/the-guild-1410-russifier"
$SteamAppId = "2977260"
$VersionFileName = "RussianLocalization.version"
$ModPakFiles = @(
    "RussianLocalization_P.pak",
    "RussianLocalization_P.ucas",
    "RussianLocalization_P.utoc"
)
$PakNames = $ModPakFiles + @($VersionFileName)
$LauncherNames = @(
    "CheckTranslationUpdate.cmd",
    "check_translation_update.ps1"
)
$DisableHours = 24
$Title = "Europa 1410 — русский перевод"

if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$Root = [System.IO.Path]::GetFullPath($Root.TrimEnd("\", "/"))
$PaksDir = Join-Path $Root "Europa1410\Content\Paks"
$VersionPath = Join-Path $PaksDir $VersionFileName
$CacheDir = Join-Path $env:LOCALAPPDATA "Europa1410-RussianLocalization"
$CachePath = Join-Path $CacheDir "update_check.json"
$DisabledPath = Join-Path $CacheDir "disabled.json"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

function Show-Popup {
    param(
        [string]$Text,
        [int]$Buttons = 0,
        [int]$Icon = 64
    )
    return [System.Windows.Forms.MessageBox]::Show(
        $Text,
        $Title,
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    )
}

function Normalize-Version([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return "0.0.0"
    }
    $trimmed = $Value.Trim()
    if ($trimmed.StartsWith("v") -or $trimmed.StartsWith("V")) {
        $trimmed = $trimmed.Substring(1)
    }
    $trimmed = $trimmed.Split("-")[0]
    try {
        return ([version]$trimmed).ToString()
    } catch {
        return "0.0.0"
    }
}

function Convert-Version([string]$Value) {
    return [version](Normalize-Version $Value)
}

function Get-LocalVersion {
    foreach ($candidate in @($VersionPath, (Join-Path $PaksDir ($VersionFileName + ".off")))) {
        if (Test-Path -LiteralPath $candidate) {
            return (Get-Content -LiteralPath $candidate -Raw -Encoding UTF8).Trim()
        }
    }
    return ""
}

function Test-GameRunning {
    return [bool](Get-Process -Name "Europa1410","Europa1410-Win64-Shipping" -ErrorAction SilentlyContinue)
}

function Test-TranslationEnabled {
    return (Test-Path -LiteralPath (Join-Path $PaksDir "RussianLocalization_P.pak"))
}

function Read-DisabledState {
    if (-not (Test-Path -LiteralPath $DisabledPath)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $DisabledPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Get-DisabledUntil {
    $state = Read-DisabledState
    if ($null -eq $state -or [string]::IsNullOrWhiteSpace($state.disabledUntil)) {
        return $null
    }
    try {
        return [datetime]::Parse($state.disabledUntil)
    } catch {
        return $null
    }
}

function Write-DisabledState([datetime]$Until) {
    New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
    $payload = @{ disabledUntil = $Until.ToUniversalTime().ToString("o") } | ConvertTo-Json
    Set-Content -LiteralPath $DisabledPath -Value $payload -Encoding UTF8
}

function Clear-DisabledState {
    if (Test-Path -LiteralPath $DisabledPath) {
        Remove-Item -LiteralPath $DisabledPath -Force
    }
}

function Enable-Translation {
    if (Test-GameRunning) {
        throw "Закройте игру, чтобы включить перевод."
    }
    New-Item -ItemType Directory -Force -Path $PaksDir | Out-Null
    foreach ($name in $ModPakFiles) {
        $off = Join-Path $PaksDir ($name + ".off")
        $on = Join-Path $PaksDir $name
        if (Test-Path -LiteralPath $off) {
            if (Test-Path -LiteralPath $on) {
                Remove-Item -LiteralPath $on -Force
            }
            Move-Item -LiteralPath $off -Destination $on -Force
        }
    }
    $versionOff = Join-Path $PaksDir ($VersionFileName + ".off")
    if (Test-Path -LiteralPath $versionOff) {
        if (Test-Path -LiteralPath $VersionPath) {
            Remove-Item -LiteralPath $VersionPath -Force
        }
        Move-Item -LiteralPath $versionOff -Destination $VersionPath -Force
    }
    Clear-DisabledState
}

function Disable-Translation {
    if (Test-GameRunning) {
        throw "Закройте игру, чтобы отключить перевод."
    }
    if (-not (Test-TranslationEnabled)) {
        return
    }
    New-Item -ItemType Directory -Force -Path $PaksDir | Out-Null
    foreach ($name in ($ModPakFiles + @($VersionFileName))) {
        $on = Join-Path $PaksDir $name
        $off = Join-Path $PaksDir ($name + ".off")
        if (Test-Path -LiteralPath $on) {
            if (Test-Path -LiteralPath $off) {
                Remove-Item -LiteralPath $off -Force
            }
            Move-Item -LiteralPath $on -Destination $off -Force
        }
    }
    Write-DisabledState -Until ([datetime]::Now.AddHours($DisableHours))
}

function Restore-TranslationIfExpired {
    if (Test-TranslationEnabled) {
        Clear-DisabledState
        return
    }
    $until = Get-DisabledUntil
    if ($null -eq $until) {
        return
    }
    if ([datetime]::Now -ge $until.ToLocalTime()) {
        Enable-Translation
    }
}

function Write-Cache {
    param($LatestTag, $DownloadUrl, $AssetName)
    New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
    $payload = @{
        checkedAt = [datetime]::UtcNow.ToString("o")
        latestTag = $LatestTag
        downloadUrl = $DownloadUrl
        assetName = $AssetName
    } | ConvertTo-Json
    Set-Content -LiteralPath $CachePath -Value $payload -Encoding UTF8
}

function Get-LatestRelease {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $headers = @{
        "User-Agent" = "Europa1410-RussianLocalization"
        "Accept" = "application/vnd.github+json"
    }
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$GithubRepo/releases/latest" -Headers $headers -TimeoutSec 6
    $asset = @($release.assets) | Where-Object { $_.name -like "*.zip" } | Select-Object -First 1
    if ($null -eq $asset) {
        throw "В релизе нет zip-архива"
    }
    return @{
        tag = [string]$release.tag_name
        url = [string]$asset.browser_download_url
        name = [string]$asset.name
    }
}

function Get-ReleaseInfo {
    try {
        $latest = Get-LatestRelease
        Write-Cache -LatestTag $latest.tag -DownloadUrl $latest.url -AssetName $latest.name
        return $latest
    } catch {
        return $null
    }
}

function Install-Zip {
    param([string]$Url, [string]$AssetName)

    if (Test-GameRunning) {
        throw "Закройте игру, чтобы обновить перевод."
    }

    $tmpZip = Join-Path $env:TEMP "Europa1410-RussianLocalization-update.zip"
    $tmpDir = Join-Path $env:TEMP "Europa1410-RussianLocalization-update"
    if (Test-Path -LiteralPath $tmpZip) { Remove-Item -LiteralPath $tmpZip -Force }
    if (Test-Path -LiteralPath $tmpDir) { Remove-Item -LiteralPath $tmpDir -Recurse -Force }

    Invoke-WebRequest -Uri $Url -OutFile $tmpZip -UserAgent "Europa1410-RussianLocalization" -TimeoutSec 120 -UseBasicParsing
    New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null
    Expand-Archive -LiteralPath $tmpZip -DestinationPath $tmpDir -Force

    $pakFile = Get-ChildItem -LiteralPath $tmpDir -Recurse -Filter "RussianLocalization_P.pak" | Select-Object -First 1
    if ($null -eq $pakFile) {
        throw "В архиве $AssetName нет RussianLocalization_P.pak"
    }

    foreach ($name in $ModPakFiles) {
        $off = Join-Path $PaksDir ($name + ".off")
        if (Test-Path -LiteralPath $off) {
            Remove-Item -LiteralPath $off -Force
        }
    }
    $versionOff = Join-Path $PaksDir ($VersionFileName + ".off")
    if (Test-Path -LiteralPath $versionOff) {
        Remove-Item -LiteralPath $versionOff -Force
    }

    New-Item -ItemType Directory -Force -Path $PaksDir | Out-Null
    $srcPaks = $pakFile.Directory.FullName
    foreach ($name in $PakNames) {
        $src = Join-Path $srcPaks $name
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination (Join-Path $PaksDir $name) -Force
        }
    }

    foreach ($name in $LauncherNames) {
        $src = Get-ChildItem -LiteralPath $tmpDir -Recurse -Filter $name | Select-Object -First 1
        if ($null -ne $src) {
            Copy-Item -LiteralPath $src.FullName -Destination (Join-Path $Root $name) -Force
        }
    }

    Clear-DisabledState
    return $true
}

function Start-Game {
    if ($SkipLaunch) {
        return
    }
    $cleanArgs = @()
    if ($GameArgs) {
        foreach ($item in $GameArgs) {
            if (-not [string]::IsNullOrWhiteSpace($item)) {
                $cleanArgs += $item.Trim()
            }
        }
    }
    if ($cleanArgs.Count -gt 0) {
        $exe = $cleanArgs[0].Trim('"')
        if ($cleanArgs.Count -gt 1) {
            Start-Process -FilePath $exe -ArgumentList $cleanArgs[1..($cleanArgs.Count - 1)]
        } else {
            Start-Process -FilePath $exe
        }
        return
    }

    Start-Process "steam://rungameid/$SteamAppId"
}

function Test-UpdateAvailable($Latest) {
    if ($null -eq $Latest) {
        return $false
    }
    $localRaw = Get-LocalVersion
    if ([string]::IsNullOrWhiteSpace($localRaw)) {
        return $true
    }
    return ((Convert-Version $localRaw) -lt (Convert-Version $Latest.tag))
}

function Set-LauncherBusy {
    param([bool]$Busy, [string]$StatusText)
    $ui = $script:LauncherUi
    if ($null -eq $ui) {
        return
    }
    $hasUpdate = Test-UpdateAvailable $script:LauncherLatest
    $ui.PlayButton.Enabled = -not $Busy
    $ui.ToggleButton.Enabled = -not $Busy
    $ui.DownloadButton.Enabled = (-not $Busy) -and $hasUpdate
    if ($StatusText) {
        $ui.StatusLabel.Text = $StatusText
    }
    [System.Windows.Forms.Application]::DoEvents()
}

function Update-LauncherUi {
    $ui = $script:LauncherUi
    if ($null -eq $ui) {
        return
    }
    $latest = $script:LauncherLatest
    $localRaw = Get-LocalVersion
    $localLabel = if ([string]::IsNullOrWhiteSpace($localRaw)) { "не установлена" } else { $localRaw }
    $enabled = Test-TranslationEnabled
    $until = Get-DisabledUntil
    $hasUpdate = Test-UpdateAvailable $latest

    $ui.VersionLabel.Text = "Установлено: $localLabel"
    $ui.DownloadButton.Enabled = $hasUpdate
    if ($hasUpdate) {
        $ui.DownloadButton.Text = "Скачать обновление $($latest.tag)"
        $ui.PlayButton.Text = "Играть без обновления"
    } else {
        $ui.DownloadButton.Text = "Обновлений нет"
        $ui.PlayButton.Text = "Играть"
    }

    if (-not $enabled) {
        $untilText = "вручную"
        if ($null -ne $until) {
            $untilText = $until.ToLocalTime().ToString("dd.MM.yyyy HH:mm")
        }
        $ui.StatusLabel.Text = "Перевод сейчас выключен (до $untilText). Игра пойдёт на языке из настроек."
        $ui.ToggleButton.Text = "Включить перевод"
    } elseif ($hasUpdate) {
        $ui.StatusLabel.Text = "Доступна новая версия: $($latest.tag). Можно скачать или играть как есть."
        $ui.ToggleButton.Text = "Отключить перевод на сутки"
    } else {
        $ui.StatusLabel.Text = "Стоит последняя версия. Можно играть или временно выключить перевод."
        $ui.ToggleButton.Text = "Отключить перевод на сутки"
    }
}

function Show-Launcher {
    Restore-TranslationIfExpired
    $script:LauncherLatest = $null

    $form = New-Object System.Windows.Forms.Form
    $form.Text = $Title
    $form.Size = New-Object System.Drawing.Size(470, 340)
    $form.StartPosition = "CenterScreen"
    $form.FormBorderStyle = "FixedDialog"
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.TopMost = $true
    $form.Font = New-Object System.Drawing.Font("Segoe UI", 10)

    $titleLabel = New-Object System.Windows.Forms.Label
    $titleLabel.Location = New-Object System.Drawing.Point(20, 16)
    $titleLabel.Size = New-Object System.Drawing.Size(420, 24)
    $titleLabel.Font = New-Object System.Drawing.Font("Segoe UI", 12, [System.Drawing.FontStyle]::Bold)
    $titleLabel.Text = "Русский перевод"
    $form.Controls.Add($titleLabel)

    $versionLabel = New-Object System.Windows.Forms.Label
    $versionLabel.Location = New-Object System.Drawing.Point(20, 48)
    $versionLabel.Size = New-Object System.Drawing.Size(420, 22)
    $form.Controls.Add($versionLabel)

    $statusLabel = New-Object System.Windows.Forms.Label
    $statusLabel.Location = New-Object System.Drawing.Point(20, 72)
    $statusLabel.Size = New-Object System.Drawing.Size(420, 44)
    $form.Controls.Add($statusLabel)

    $hintLabel = New-Object System.Windows.Forms.Label
    $hintLabel.Location = New-Object System.Drawing.Point(20, 118)
    $hintLabel.Size = New-Object System.Drawing.Size(420, 36)
    $hintLabel.ForeColor = [System.Drawing.Color]::DimGray
    $hintLabel.Text = "Если перевод мешает игре — отключите его на сутки. Файлы не удаляются, только прячутся."
    $form.Controls.Add($hintLabel)

    $playButton = New-Object System.Windows.Forms.Button
    $playButton.Location = New-Object System.Drawing.Point(20, 164)
    $playButton.Size = New-Object System.Drawing.Size(420, 40)
    $playButton.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Bold)
    $playButton.Text = "Играть"
    $form.Controls.Add($playButton)
    $form.AcceptButton = $playButton

    $downloadButton = New-Object System.Windows.Forms.Button
    $downloadButton.Location = New-Object System.Drawing.Point(20, 212)
    $downloadButton.Size = New-Object System.Drawing.Size(420, 32)
    $downloadButton.Text = "Скачать обновление"
    $downloadButton.Enabled = $false
    $form.Controls.Add($downloadButton)

    $toggleButton = New-Object System.Windows.Forms.Button
    $toggleButton.Location = New-Object System.Drawing.Point(20, 252)
    $toggleButton.Size = New-Object System.Drawing.Size(420, 32)
    $toggleButton.Text = "Отключить перевод на сутки"
    $form.Controls.Add($toggleButton)

    $script:LauncherUi = @{
        Form = $form
        VersionLabel = $versionLabel
        StatusLabel = $statusLabel
        PlayButton = $playButton
        DownloadButton = $downloadButton
        ToggleButton = $toggleButton
    }

    $form.Add_Shown({
        $script:LauncherUi.StatusLabel.Text = "Проверка обновлений на GitHub..."
        [System.Windows.Forms.Application]::DoEvents()
        $script:LauncherLatest = Get-ReleaseInfo
        Update-LauncherUi
    })

    $playButton.Add_Click({
        Start-Game
        $script:LauncherUi.Form.Close()
    })

    $downloadButton.Add_Click({
        $latest = $script:LauncherLatest
        if ($null -eq $latest) {
            return
        }
        Set-LauncherBusy -Busy $true -StatusText "Скачивание $($latest.tag)..."
        try {
            [void](Install-Zip -Url $latest.url -AssetName $latest.name)
            [void][System.Windows.Forms.MessageBox]::Show(
                "Перевод обновлён до $($latest.tag).",
                $Title,
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Information
            )
        } catch {
            [void][System.Windows.Forms.MessageBox]::Show(
                "Не удалось скачать обновление:`r`n$($_.Exception.Message)",
                $Title,
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Error
            )
        }
        Set-LauncherBusy -Busy $false -StatusText ""
        Update-LauncherUi
    })

    $toggleButton.Add_Click({
        try {
            if (Test-TranslationEnabled) {
                $answer = [System.Windows.Forms.MessageBox]::Show(
                    "Перевод будет скрыт на $DisableHours ч. Игра запустится на языке из настроек (английский или немецкий). Вернуть можно здесь же или автоматически через сутки.`r`n`r`nОтключить перевод?",
                    $Title,
                    [System.Windows.Forms.MessageBoxButtons]::YesNo,
                    [System.Windows.Forms.MessageBoxIcon]::Question
                )
                if ($answer -ne [System.Windows.Forms.DialogResult]::Yes) {
                    return
                }
                Disable-Translation
            } else {
                Enable-Translation
            }
        } catch {
            [void][System.Windows.Forms.MessageBox]::Show(
                $_.Exception.Message,
                $Title,
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Error
            )
        }
        Update-LauncherUi
    })

    [void]$form.ShowDialog()
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if ($AutoUpdate) {
    try {
        Restore-TranslationIfExpired
        $latest = Get-ReleaseInfo
        if (Test-UpdateAvailable $latest) {
            [void](Install-Zip -Url $latest.url -AssetName $latest.name)
        }
    } catch {
    }
    if (-not $SkipLaunch) {
        Start-Game
    }
    exit 0
}

Show-Launcher
exit 0
