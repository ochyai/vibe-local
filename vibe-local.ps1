# vibe-local.ps1
# PowerShell Wrapper for vibe-local
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$stateDir = "$HOME\.local\state\vibe-local"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

$configFile = "$HOME\.config\vibe-local\config"
$proxyScript = "$HOME\.local\lib\vibe-local\anthropic-ollama-proxy.py"
$proxyLog = "$stateDir\proxy.log"
$proxyPidFile = "$stateDir\proxy.pid"

$model = ""
$sidecarModel = ""
$ollamaHost = "http://localhost:11434"
$proxyPort = 8082
$debugMode = 0
$autoMode = $false
$yesFlag = $false
$extraArgs = @()

# Parse config
if (Test-Path $configFile) {
    foreach ($line in (Get-Content $configFile)) {
        if ($line -match "^MODEL=`"?(.*?)`"?$") { $model = $matches[1] }
        if ($line -match "^SIDECAR_MODEL=`"?(.*?)`"?$") { $sidecarModel = $matches[1] }
        if ($line -match "^OLLAMA_HOST=`"?(.*?)`"?$") { $ollamaHost = $matches[1] }
        if ($line -match "^PROXY_PORT=`"?(.*?)`"?$") { $proxyPort = $matches[1] }
        if ($line -match "^VIBE_LOCAL_DEBUG=`"?(.*?)`"?$") { $debugMode = $matches[1] }
    }
}

# RAM Detection
$mem = Get-CimInstance Win32_ComputerSystem
$ramGB = [math]::Round($mem.TotalPhysicalMemory / 1GB)

if ($model -eq "") {
    if ($ramGB -ge 32) { $model = "qwen3-coder:30b" }
    elseif ($ramGB -ge 16) { $model = "qwen3:8b" }
    elseif ($ramGB -ge 8) { $model = "qwen3:1.7b" }
    else {
        Write-Host "Error: Insufficient memory ($ramGB GB)" -ForegroundColor Red
        exit 1
    }
}
if ($sidecarModel -eq "") {
    if ($ramGB -ge 32) { $sidecarModel = "qwen3:8b" }
    elseif ($ramGB -ge 16) { $sidecarModel = "qwen3:1.7b" }
}

# Parse Args
for ($i = 0; $i -lt $args.Length; $i++) {
    switch ($args[$i]) {
        "--auto" { $autoMode = $true }
        "-y" { $yesFlag = $true }
        "--yes" { $yesFlag = $true }
        "--model" {
            if ($i + 1 -lt $args.Length) {
                $i++
                $model = $args[$i]
            }
        }
        default { $extraArgs += $args[$i] }
    }
}

if ($autoMode) {
    try {
        $response = Invoke-WebRequest -Uri "https://api.anthropic.com/" -TimeoutSec 3 -UseBasicParsing -ErrorAction Ignore
        Write-Host "🌐 Network available -> Launching Claude Code directly." -ForegroundColor Cyan
        & claude $extraArgs
        exit $LASTEXITCODE
    }
    catch {
        Write-Host "📡 No network connection -> Local mode ($model)" -ForegroundColor Yellow
    }
}

# Ollama起動確認 (TCPポートチェック - HTTP APIより確実)
function Test-PortOpen {
    param([int]$Port)
    $result = Test-NetConnection -ComputerName "127.0.0.1" -Port $Port `
        -InformationLevel Quiet -WarningAction SilentlyContinue 2>$null
    return $result
}

$ollamaPort = 11434
try {
    $uri = [System.Uri]$ollamaHost
    if ($uri.Port -gt 0) { $ollamaPort = $uri.Port }
}
catch {}

if (-not (Test-PortOpen $ollamaPort)) {
    Write-Host "🦙 Starting Ollama..." -ForegroundColor Yellow
    Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
    $started = $false
    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 2
        if (Test-PortOpen $ollamaPort) {
            $started = $true
            break
        }
    }
    if (-not $started) {
        Write-Host "❌ Failed to start Ollama." -ForegroundColor Red
        exit 1
    }
}
else {
    Write-Host "✅ Ollama is already running." -ForegroundColor Green
}

# Proxy起動確認
$proxyUrl = "http://127.0.0.1:$proxyPort"
$proxyRunning = Test-PortOpen $proxyPort

if (-not $proxyRunning) {
    if (Test-Path $proxyPidFile) {
        $oldPid = Get-Content $proxyPidFile
        try { Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue } catch {}
    }
    Write-Host "⚡ Starting Anthropic -> Ollama proxy (port $proxyPort)..." -ForegroundColor Yellow
    $env:OLLAMA_HOST = $ollamaHost
    $env:VIBE_LOCAL_MODEL = $model
    $env:VIBE_LOCAL_SIDECAR_MODEL = if ($sidecarModel) { $sidecarModel } else { $model }
    $env:VIBE_LOCAL_DEBUG = $debugMode

    $proxyLogErr = $proxyLog -replace '\.log$', '.err.log'
    $proxyProc = Start-Process -FilePath "python" -ArgumentList "`"$proxyScript`" $proxyPort" -WindowStyle Hidden -PassThru -RedirectStandardOutput $proxyLog -RedirectStandardError $proxyLogErr
    $proxyProc.Id | Out-File -FilePath $proxyPidFile

    for ($i = 0; $i -lt 15; $i++) {
        Start-Sleep -Seconds 1
        if (Test-PortOpen $proxyPort) {
            $proxyRunning = $true
            break
        }
    }
    if (-not $proxyRunning) {
        Write-Host "❌ Proxy failed to start or respond." -ForegroundColor Red
        Get-Content $proxyLog -Tail 10 | Write-Host
        exit 1
    }
}
else {
    Write-Host "✅ Proxy is already running." -ForegroundColor Green
}

# Permissions
$permArgs = @()
if (-not $yesFlag) {
    Write-Host ""
    Write-Host "============================================" -ForegroundColor Yellow
    Write-Host " 🔐 Permission Check" -ForegroundColor Yellow
    Write-Host "============================================" -ForegroundColor Yellow
    Write-Host "`n vibe-local is about to start. Local LLMs might perform completely unexpected actions."
    Write-Host "`n [y] Auto-approve all tools (Danger)"
    Write-Host " [N] Ask before each tool use (Recommended)"
    $reply = Read-Host "`n Continue? [y/N]"
    if ($reply -match "^[yY](es)?$") {
        $yesFlag = $true
        Write-Host " -> Auto-approve mode enabled." -ForegroundColor Red
    }
    else {
        Write-Host " -> Normal mode (ask each time)." -ForegroundColor Green
    }
}

if ($yesFlag) {
    $permArgs += "--dangerously-skip-permissions"
}

$sidecarLabel = if ($sidecarModel) { $sidecarModel } else { "none" }
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " 🤖 vibe-local"
Write-Host " Model:       $model"
Write-Host " Sidecar:     $sidecarLabel"
Write-Host " Proxy:       $proxyUrl -> $ollamaHost"
Write-Host " Permissions: $(if ($yesFlag) { 'auto-approve' } else { 'ask each time' })"
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$env:ANTHROPIC_BASE_URL = $proxyUrl
$env:ANTHROPIC_API_KEY = "local"
$env:VIBE_LOCAL_DEBUG = $debugMode

# Run Claude Code CLI natively
try {
    & claude --model $model @permArgs @extraArgs
}
finally {
    # Proxy keeps running for faster subsequent launches
}
