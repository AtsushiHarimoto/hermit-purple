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

function Start-OllamaIfNeeded {
    $ollamaProc = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
    if ($ollamaProc) {
        Write-Host "[api-run] Ollama 已在運行，略過啟動。"
        return
    }

    $ollamaCmd = Get-Command "ollama" -ErrorAction SilentlyContinue
    if (-not $ollamaCmd) {
        throw "找不到 ollama 指令，請先安裝並加入 PATH。"
    }

    Write-Host "[api-run] 啟動 Ollama 服務..."
    Start-Process -FilePath $ollamaCmd.Source -ArgumentList "serve" -WindowStyle Minimized | Out-Null
    Start-Sleep -Seconds 2
}

function Open-TargetsInFirefox {
    if (-not (Test-Path $firefoxPath)) {
        throw "找不到 Firefox: $firefoxPath"
    }

    foreach ($url in $targets) {
        Start-Process -FilePath $firefoxPath -ArgumentList $url | Out-Null
    }
}

if (-not (Test-Path $gameApiScript)) {
    throw "找不到腳本: $gameApiScript"
}

Start-OllamaIfNeeded
Write-Host "[api-run] 開啟 Grok 與 Gemini..."
Open-TargetsInFirefox

Write-Host ""
Write-Host "[api-run] 請先確認 Grok / Gemini 已登入。"
[void](Read-Host "[api-run] 確認完成後按 Enter 繼續")

Push-Location $gatewayDir
try {
    Write-Host "[api-run] 啟動 run_game_api.ps1 ..."
    & ".\run_game_api.ps1"
} finally {
    Pop-Location
}
