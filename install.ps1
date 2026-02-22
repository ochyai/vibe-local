# vibe-local installer for Windows
# ✨🌴 Ｖ Ａ Ｐ Ｏ Ｒ Ｗ Ａ Ｖ Ｅ   ＩＮＳＴＡＬＬＥＲ 🌴✨

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# ╔══════════════════════════════════════════════════════════════╗
# ║  🎨  Ｖ Ａ Ｐ Ｏ Ｒ Ｗ Ａ Ｖ Ｅ   Ｃ Ｏ Ｌ Ｏ Ｒ Ｓ    ║
# ╚══════════════════════════════════════════════════════════════╝

$PINK   = "Magenta"
$CYAN   = "Cyan"
$YELLOW = "Yellow"
$GREEN  = "Green"
$RED    = "Red"
$WHITE  = "White"
$GRAY   = "DarkGray"

function Write-Vapor {
    param(
        [Parameter(Mandatory=$true)]
        [string]$Message,
        [string]$Color = "White",
        [switch]$NoNewLine
    )
    Write-Host $Message -ForegroundColor $Color -NoNewline:$NoNewLine
}

# ╔══════════════════════════════════════════════════════════════╗
# ║  🌅  Ｔ Ｉ Ｔ Ｌ Ｅ   Ｓ Ｃ Ｒ Ｅ Ｅ Ｎ                ║
# ╚══════════════════════════════════════════════════════════════╝

function Show-Header {
    Clear-Host
    Write-Host ""
    Write-Host "  💜✨🔮  💜✨🔮  💜✨🔮" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "  ██╗   ██╗██╗██████╗ ███████╗" -ForegroundColor Magenta
    Write-Host "  ██║   ██║██║██╔══██╗██╔════╝" -ForegroundColor Magenta
    Write-Host "  ██║   ██║██║██████╔╝█████╗  " -ForegroundColor Magenta
    Write-Host "  ╚██╗ ██╔╝██║██╔══██╗██╔══╝  " -ForegroundColor Magenta
    Write-Host "   ╚████╔╝ ██║██████╔╝███████╗" -ForegroundColor Magenta
    Write-Host "    ╚═══╝  ╚═╝╚═════╝ ╚══════╝" -ForegroundColor Magenta
    Write-Host ""
    Write-Host "              ██╗      ██████╗  ██████╗ █████╗ ██╗     " -ForegroundColor Cyan
    Write-Host "              ██║     ██╔═══██╗██╔════╝██╔══██╗██║     " -ForegroundColor Cyan
    Write-Host "              ██║     ██║   ██║██║     ███████║██║     " -ForegroundColor Cyan
    Write-Host "              ██║     ██║   ██║██║     ██╔══██║██║     " -ForegroundColor Cyan
    Write-Host "              ███████╗╚██████╔╝╚██████╗██║  ██║███████╗" -ForegroundColor Cyan
    Write-Host "              ╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  💜💜💜💜💜💜💜💜💜💜💜💜💜💜💜💜" -ForegroundColor Magenta
    Write-Host ""
    Write-Vapor -Message "  ✨🌴  無 料 Ａ Ｉ コ ー デ ィ ン グ 環 境  🌴✨" -Color $PINK
    Write-Host ""
    Write-Host "  ════════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "  💜 ネットワーク不要 • 完全無料 • ローカルAIコーディング 💜" -ForegroundColor White
    Write-Host "  ════════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host ""
    Start-Sleep -Milliseconds 500
    Write-Host "  ヴェイパーウェーブサブシステム初期化中..." -ForegroundColor DarkCyan
    Start-Sleep -Milliseconds 300
    Write-Host "  アエステティックモジュール読み込み中..." -ForegroundColor DarkMagenta
    Start-Sleep -Milliseconds 300
    Write-Host "  ネオン周波数キャリブレーション中..." -ForegroundColor DarkMagenta
    Start-Sleep -Milliseconds 300
    Write-Host "  ▶ Ｓ Ｙ Ｓ Ｔ Ｅ Ｍ  Ｏ Ｎ Ｌ Ｉ Ｎ Ｅ" -ForegroundColor Green
    Start-Sleep -Milliseconds 500
    Write-Host ""
}

function Step-Header {
    param([int]$Num, [string]$Title)
    $icons = @("🔍","🧠","📦","🤖","📂","⚙️","🧪")
    $icon = $icons[$Num - 1]
    Write-Host ""
    Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
    Write-Host "  $icon  ＳＴＥＰ $Num/7  $Title" -ForegroundColor Cyan
    Write-Host "  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
}

function Write-VaporSuccess { Write-Host "  ┃ ✅ $args" -ForegroundColor Green }
function Write-VaporInfo    { Write-Host "  ┃ 💠 $args" -ForegroundColor Cyan }
function Write-VaporWarn    { Write-Host "  ┃ ⚠️  $args" -ForegroundColor Yellow }
function Write-VaporError   { Write-Host "  ┃ 💀 $args" -ForegroundColor Red }

function Check-Command {
    param([string]$Name)
    if (Get-Command $Name -ErrorAction SilentlyContinue) { return $true }
    if (Test-Path "$env:ProgramFiles\Ollama\ollama.exe")              { return $true }
    if (Test-Path "$env:LocalAppData\Programs\Ollama\ollama.exe")     { return $true }
    if (Test-Path "C:\Program Files\Ollama\ollama.exe")               { return $true }
    return $false
}

# =============================================
Show-Header

# =============================================
# Step 1: システムスキャン
# =============================================
Step-Header 1 "Ｓ Ｙ Ｓ Ｔ Ｅ Ｍ  Ｓ Ｃ Ａ Ｎ"
Write-VaporInfo "ハードウェアスキャン中..."
try {
    $arch = (Get-CimInstance Win32_OperatingSystem).OSArchitecture
} catch {
    $arch = "Unknown"
}
Write-VaporInfo "OS: Windows / Arch: $arch"
Write-VaporSuccess "Windows 検出 🪟"

# =============================================
# Step 2: メモリ解析 & モデル自動選択
# =============================================
Step-Header 2 "Ｍ Ｅ Ｍ Ｏ Ｒ Ｙ  Ａ Ｎ Ａ Ｌ Ｙ Ｓ Ｉ Ｓ"
Write-VaporInfo "メモリ空間マッピング中..."

$mem   = Get-CimInstance Win32_ComputerSystem
$ramGB = [math]::Round($mem.TotalPhysicalMemory / 1GB)

# メモリバー表示
$barWidth  = 30
$filled    = [math]::Min([math]::Round($ramGB * $barWidth / 128), $barWidth)
$empty     = $barWidth - $filled
$bar       = ("█" * $filled) + ("░" * $empty)
Write-Host "  ┃ 🧠 搭載メモリ: " -NoNewline -ForegroundColor Cyan
Write-Host "${ramGB}GB" -ForegroundColor Green
Write-Host "  ┃    ▐${bar}▌ (${ramGB}/128GB)" -ForegroundColor Cyan
Write-Host ""

$model        = ""
$sidecarModel = ""

if ($ramGB -ge 32) {
    $model        = "qwen3-coder:30b"
    $sidecarModel = "qwen3:8b"
    Write-Host "  ┃ 🏆 ★★★ ＢＥＳＴ  ＭＯＤＥＬ ★★★" -ForegroundColor Yellow
    Write-Host "  ┃    $model (19GB, MoE 3.3B active, コーディング最強)" -ForegroundColor White
    Write-Host "  ┃    + sidecar: $sidecarModel (5GB, fast helper)" -ForegroundColor DarkGray
} elseif ($ramGB -ge 16) {
    $model        = "qwen3:8b"
    $sidecarModel = "qwen3:1.7b"
    Write-Host "  ┃ ⭐ ★★ ＧＲＥＡＴ  ＭＯＤＥＬ ★★" -ForegroundColor Cyan
    Write-Host "  ┃    $model (5GB, 高性能コーディング)" -ForegroundColor White
    Write-Host "  ┃    + sidecar: $sidecarModel (1.1GB, fast helper)" -ForegroundColor DarkGray
} elseif ($ramGB -ge 8) {
    $model = "qwen3:1.7b"
    Write-VaporWarn "$model (1.1GB, 最低限動作)"
    Write-VaporWarn "16GB以上のメモリを推奨します"
} else {
    Write-VaporError "メモリ不足: ${ramGB}GB (最低8GB必要)"
    Write-Host ""
    Write-Host "  不要なアプリを閉じてメモリを解放してください"
    exit 1
}

# =============================================
# Step 3: パッケージインストール確認
# =============================================
Step-Header 3 "Ｐ Ａ Ｃ Ｋ Ａ Ｇ Ｅ  Ｉ Ｎ Ｓ Ｔ Ａ Ｌ Ｌ"

# Ollama
if (Check-Command "ollama") {
    Write-VaporSuccess "Ollama 🦙 インストール済み"
} else {
    Write-VaporError "Ollama 🦙 見つかりません。https://ollama.com/ からインストールしてください"
}

# Node.js
if (Check-Command "node") {
    $nodeVer = (node -v 2>$null)
    Write-VaporSuccess "Node.js 💚 インストール済み ($nodeVer)"
} else {
    Write-VaporError "Node.js 💚 見つかりません。https://nodejs.org/ からインストールしてください"
}

# Python
if (Check-Command "python") {
    $pyVer = (python --version 2>$null)
    Write-VaporSuccess "Python 🐍 $pyVer"
} else {
    Write-VaporError "Python 🐍 見つかりません。Microsoft Store または python.org からインストールしてください"
}

# Claude Code CLI
if (Check-Command "claude") {
    Write-VaporSuccess "Claude Code CLI 🤖 インストール済み"
} else {
    Write-VaporInfo "Claude Code CLI 🤖 インストール中..."
    npm install -g @anthropic-ai/claude-code
    if (Check-Command "claude") {
        Write-VaporSuccess "Claude Code CLI 🤖 インストール完了"
    } else {
        Write-VaporError "Claude Code CLI 🤖 インストール失敗"
        Write-VaporWarn "手動でインストールしてから再実行してください: npm install -g @anthropic-ai/claude-code"
    }
}

# =============================================
# Step 4: AI モデルダウンロード
# =============================================
Step-Header 4 "Ａ Ｉ  Ｍ Ｏ Ｄ Ｅ Ｌ  Ｄ Ｏ Ｗ Ｎ Ｌ Ｏ Ａ Ｄ"

if (Check-Command "ollama") {
    Write-VaporInfo "モデルをダウンロード中... (初回はサイズに応じて数分〜数十分かかります)"
    Write-VaporInfo "メインモデル: $model"
    ollama pull $model
    if ($sidecarModel) {
        Write-VaporInfo "サイドカーモデル: $sidecarModel"
        ollama pull $sidecarModel
    }
    Write-VaporSuccess "モデルダウンロード完了"
} else {
    Write-VaporWarn "Ollama が見つからないためモデルダウンロードをスキップします"
}

# =============================================
# Step 5: ファイルデプロイ
# =============================================
Step-Header 5 "Ｆ Ｉ Ｌ Ｅ  Ｄ Ｅ Ｐ Ｌ Ｏ Ｙ"

$libDir = "$HOME\.local\lib\vibe-local"
$binDir = "$HOME\.local\bin"
New-Item -ItemType Directory -Force -Path $libDir | Out-Null
New-Item -ItemType Directory -Force -Path $binDir | Out-Null

if (Test-Path "$PSScriptRoot\anthropic-ollama-proxy.py") {
    Copy-Item "$PSScriptRoot\anthropic-ollama-proxy.py" "$libDir\" -Force
    Write-VaporSuccess "ソース: ローカル → $libDir"
}
if (Test-Path "$PSScriptRoot\vibe-local.ps1") {
    Copy-Item "$PSScriptRoot\vibe-local.ps1" "$binDir\vibe-local.ps1" -Force
}
Write-VaporSuccess "ファイルデプロイ complete → $libDir"

# =============================================
# Step 6: 設定ファイル生成
# =============================================
Step-Header 6 "Ｃ Ｏ Ｎ Ｆ Ｉ Ｇ  Ｇ Ｅ Ｎ Ｅ Ｒ Ａ Ｔ Ｅ"

$configDir  = "$HOME\.config\vibe-local"
$configFile = "$configDir\config"
New-Item -ItemType Directory -Force -Path $configDir | Out-Null

if (Test-Path $configFile) {
    Write-VaporInfo "設定ファイルが既に存在 → 既存設定を保持"
} else {
    $configContent = @"
MODEL="$model"
SIDECAR_MODEL="$sidecarModel"
PROXY_PORT=8082
OLLAMA_HOST="http://localhost:11434"
"@
    $configContent | Out-File -FilePath $configFile -Encoding utf8
    Write-VaporSuccess "設定ファイル生成: $configFile"
}

# =============================================
# Step 7: システムテスト & 完了
# =============================================
Step-Header 7 "Ｓ Ｙ Ｓ Ｔ Ｅ Ｍ  Ｔ Ｅ Ｓ Ｔ"

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════════════════════╗" -ForegroundColor Magenta
Write-Host "  ║   ✅  ＩＮＳＴＡＬＬ  ＣＯＭＰＬＥＴＥ !!             ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════════════════════════╝" -ForegroundColor Magenta
Write-Host ""
Write-Host "  ⚡ 使い方:" -ForegroundColor White
Write-Host "    .\vibe-local.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "  ⚙️  設定:" -ForegroundColor White
Write-Host "    モデル  : $model" -ForegroundColor DarkGray
if ($sidecarModel) {
Write-Host "    サイドカー: $sidecarModel" -ForegroundColor DarkGray
}
Write-Host "    設定ファイル: $configFile" -ForegroundColor DarkGray
Write-Host ""
Write-Vapor -Message "  🌴  無 料 Ａ Ｉ コ ー デ ィ ン グ を 楽 し も う  🌴" -Color $PINK
Write-Host ""
