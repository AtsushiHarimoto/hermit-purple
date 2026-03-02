[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot | Split-Path -Parent | Split-Path -Parent
$gatewayDir = Join-Path $repoRoot "projects\moyin-gateway"
$gameApiScript = Join-Path $gatewayDir "run_game_api.ps1"
$firefoxPath = "firefox"
$targets = @(
    "https://grok.com/",
    "https://gemini.google.com/"
)

$steps = @(
    @{ Key = "ollama"; Label = "Ollama"; Status = "pending"; Message = "等待啟動" },
    @{ Key = "browser"; Label = "Firefox + Grok/Gemini"; Status = "pending"; Message = "等待開啟" },
    @{ Key = "login"; Label = "登入確認"; Status = "pending"; Message = "等待確認" },
    @{ Key = "api"; Label = "run_game_api.ps1"; Status = "pending"; Message = "等待啟動" }
)

$logs = New-Object System.Collections.Generic.List[string]

function Add-Log {
    param([string]$Text)
    $ts = Get-Date -Format "HH:mm:ss"
    $logs.Add("[$ts] $Text") | Out-Null
}

function Set-StepState {
    param(
        [string]$Key,
        [string]$Status,
        [string]$Message
    )
    foreach ($s in $steps) {
        if ($s.Key -eq $Key) {
            $s.Status = $Status
            $s.Message = $Message
            return
        }
    }
}

function Status-Icon {
    param([string]$Status)
    switch ($Status) {
        "ok" { return "[OK]  " }
        "run" { return "[RUN] " }
        "err" { return "[ERR] " }
        default { return "[....]" }
    }
}

function Get-ProgressInfo {
    $done = ($steps | Where-Object { $_.Status -eq "ok" }).Count
    $total = $steps.Count
    $percent = if ($total -eq 0) { 0 } else { [Math]::Floor(($done / $total) * 100) }
    return @{
        Done = $done
        Total = $total
        Percent = $percent
    }
}

function Draw-ProgressBar {
    param(
        [int]$Percent,
        [int]$Width = 36
    )
    $filled = [Math]::Floor(($Percent / 100) * $Width)
    $empty = $Width - $filled
    return ("[" + ("#" * $filled) + ("-" * $empty) + "]")
}

function Draw-Logo {
    Write-Host "  __  __  ___ __   __ ___ _   _      _    ____ ___ " -ForegroundColor Magenta
    Write-Host " |  \/  |/ _ \\ \ / /|_ _| \ | |    / \  |  _ \_ _|" -ForegroundColor Magenta
    Write-Host " | |\/| | | | |\ V /  | ||  \| |   / _ \ | |_) | | " -ForegroundColor DarkMagenta
    Write-Host " | |  | | |_| | | |   | || |\  |  / ___ \|  __/| | " -ForegroundColor DarkMagenta
    Write-Host " |_|  |_|\___/  |_|  |___|_| \_| /_/   \_\_|  |___|" -ForegroundColor DarkMagenta
    Write-Host ""
}

function Draw-Tui {
    param([string]$Hint = "Enter=下一步   R=重試當前步驟   Q=退出")

    $progress = Get-ProgressInfo
    $bar = Draw-ProgressBar -Percent $progress.Percent

    Clear-Host
    Write-Host "╔══════════════════════════════════════════════════════════════════════════════╗" -ForegroundColor DarkMagenta
    Write-Host "║                              MOYIN API RUNNER                               ║" -ForegroundColor Magenta
    Write-Host "╚══════════════════════════════════════════════════════════════════════════════╝" -ForegroundColor DarkMagenta
    Write-Host ""
    Draw-Logo
    Write-Host "進度 $bar $($progress.Percent)%  ($($progress.Done)/$($progress.Total))" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "狀態面板" -ForegroundColor Cyan
    Write-Host "───────────────────────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    foreach ($s in $steps) {
        $icon = Status-Icon $s.Status
        $color = "Gray"
        if ($s.Status -eq "ok") { $color = "Green" }
        elseif ($s.Status -eq "run") { $color = "Yellow" }
        elseif ($s.Status -eq "err") { $color = "Red" }
        Write-Host ("{0} {1,-24} {2}" -f $icon, $s.Label, $s.Message) -ForegroundColor $color
    }
    Write-Host ""
    Write-Host "日誌（最新 10 條）" -ForegroundColor Cyan
    Write-Host "───────────────────────────────────────────────────────────────────────────────" -ForegroundColor DarkGray
    $start = [Math]::Max(0, $logs.Count - 10)
    for ($i = $start; $i -lt $logs.Count; $i++) {
        Write-Host $logs[$i] -ForegroundColor DarkGray
    }
    if ($logs.Count -eq 0) {
        Write-Host "(暫無日誌)" -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host $Hint -ForegroundColor Yellow
}

function Wait-Key {
    while ($true) {
        $k = [Console]::ReadKey($true)
        if ($k.Key -eq [ConsoleKey]::Enter) { return "enter" }
        if ($k.Key -eq [ConsoleKey]::Q) { return "quit" }
        if ($k.Key -eq [ConsoleKey]::R) { return "retry" }
    }
}

function Ensure-Ollama {
    Set-StepState -Key "ollama" -Status "run" -Message "檢查服務中"
    Draw-Tui
    $ollamaProc = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
    if ($ollamaProc) {
        Add-Log "Ollama 已在運行，略過啟動。"
        Set-StepState -Key "ollama" -Status "ok" -Message "已運行"
        Draw-Tui
        return
    }

    $ollamaCmd = Get-Command "ollama" -ErrorAction SilentlyContinue
    if (-not $ollamaCmd) {
        throw "找不到 ollama 指令，請先安裝並加入 PATH。"
    }

    Add-Log "啟動 Ollama 服務..."
    Start-Process -FilePath $ollamaCmd.Source -ArgumentList "serve" -WindowStyle Minimized | Out-Null
    Start-Sleep -Seconds 2
    Set-StepState -Key "ollama" -Status "ok" -Message "已啟動"
    Add-Log "Ollama 啟動完成。"
    Draw-Tui
}

function Open-BrowserTargets {
    Set-StepState -Key "browser" -Status "run" -Message "開啟 Grok/Gemini"
    Draw-Tui
    if (-not (Test-Path $firefoxPath)) {
        throw "找不到 Firefox: $firefoxPath"
    }
    foreach ($url in $targets) {
        Start-Process -FilePath $firefoxPath -ArgumentList $url | Out-Null
        Add-Log "已開啟: $url"
    }
    Set-StepState -Key "browser" -Status "ok" -Message "已開啟"
    Draw-Tui
}

function Wait-ForLoginConfirm {
    Set-StepState -Key "login" -Status "run" -Message "等待你確認已登入"
    Draw-Tui -Hint "請確認 Grok/Gemini 已登入。Enter=繼續   Q=退出"
    while ($true) {
        $action = Wait-Key
        if ($action -eq "quit") {
            throw "使用者取消流程。"
        }
        if ($action -eq "enter") {
            Add-Log "使用者已確認 Grok/Gemini 登入。"
            Set-StepState -Key "login" -Status "ok" -Message "已確認"
            Draw-Tui
            return
        }
    }
}

function Run-GameApi {
    if (-not (Test-Path $gameApiScript)) {
        throw "找不到腳本: $gameApiScript"
    }

    Set-StepState -Key "api" -Status "run" -Message "啟動中"
    Add-Log "開始執行 run_game_api.ps1"
    Draw-Tui -Hint "API 啟動中，請稍候..."

    Push-Location $gatewayDir
    try {
        & ".\run_game_api.ps1"
        Set-StepState -Key "api" -Status "ok" -Message "執行完成"
        Add-Log "run_game_api.ps1 執行完成。"
    } finally {
        Pop-Location
    }

    Draw-Tui -Hint "流程完成。按 Q 退出"
}

function Mark-StepError {
    param([string]$Key, [string]$Message)
    Set-StepState -Key $Key -Status "err" -Message $Message
    Add-Log $Message
    Draw-Tui -Hint "R=重試當前步驟   Q=退出"
}

$currentStep = 0
$stepKeys = @("ollama", "browser", "login", "api")

Draw-Tui

while ($currentStep -lt $stepKeys.Count) {
    $stepKey = $stepKeys[$currentStep]
    $action = Wait-Key

    if ($action -eq "quit") {
        Add-Log "流程已取消。"
        Draw-Tui -Hint "已退出。按任意鍵關閉"
        [void][Console]::ReadKey($true)
        return
    }

    if ($action -ne "enter" -and $action -ne "retry") {
        continue
    }

    try {
        switch ($stepKey) {
            "ollama" { Ensure-Ollama }
            "browser" { Open-BrowserTargets }
            "login" { Wait-ForLoginConfirm }
            "api" { Run-GameApi }
        }
        $currentStep++
    } catch {
        Mark-StepError -Key $stepKey -Message $_.Exception.Message
    }
}

while ($true) {
    $action = Wait-Key
    if ($action -eq "quit") { break }
}
