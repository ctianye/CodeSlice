<#
.SYNOPSIS
    One-command development launcher for CodeSlice.

.DESCRIPTION
    Starts the Python backend with a fixed dev token in its own window, waits
    until /api/health answers, then starts the Electron shell (which in turn
    runs the Vite renderer through desktop/npm run dev).

    This mirrors the README quickstart:
        backend : cd backend; uv run fastapi dev app/main.py   (CODESLICE_TOKEN=dev-token)
        desktop : cd desktop; npm run dev

    If desktop/ has not been scaffolded yet the script still leaves the backend
    running and prints how to start the renderer in a browser instead.

.PARAMETER Port
    Port for the backend. Default 8000 (frontend/vite.config.ts proxies to it).

.PARAMETER Token
    Session token for local dev. Default "dev-token".

.PARAMETER NoDesktop
    Only start the backend (useful when working on the API alone).

.PARAMETER ReuseRunning
    Accept an already-running backend on the chosen port instead of failing.

.EXAMPLE
    pwsh -File scripts\dev.ps1

.EXAMPLE
    pwsh -File scripts\dev.ps1 -Port 8123 -Token local-test -NoDesktop
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$Token = 'dev-token',
    [switch]$NoDesktop,
    [switch]$ReuseRunning,
    [int]$StartupTimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Stop-BackendWindow {
    # `fastapi dev` runs uvicorn with reload=True, so the real server is a
    # grandchild. Killing only the console window leaves an orphan holding the
    # port, which then blocks the next run. taskkill /T walks the whole tree.
    param([System.Diagnostics.Process]$Proc)
    if (-not $Proc) { return }
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & taskkill.exe /PID $Proc.Id /T /F 2>&1 | Out-Null
    }
    catch { }
    finally { $ErrorActionPreference = $prevEap }
}

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}
function Write-Ok([string]$Message) { Write-Host "    [ok]   $Message" -ForegroundColor Green }
function Write-Warn2([string]$Message) { Write-Host "    [warn] $Message" -ForegroundColor Yellow }
function Write-Err([string]$Message) { Write-Host "    [FAIL] $Message" -ForegroundColor Red }

$RepoRoot    = Split-Path -Parent $PSScriptRoot
$BackendDir  = Join-Path $RepoRoot 'backend'
$DesktopDir  = Join-Path $RepoRoot 'desktop'
$BaseUrl     = "http://127.0.0.1:$Port"
$HealthUrl   = "$BaseUrl/api/health"

Write-Host 'CodeSlice dev launcher' -ForegroundColor White
Write-Host "  repo:    $RepoRoot"
Write-Host "  backend: $BaseUrl (token: $Token)"

# --------------------------------------------------------------------------
# 0. Already running?
# --------------------------------------------------------------------------
function Test-Health {
    param([string]$TokenValue)
    try {
        $headers = @{}
        if ($TokenValue) { $headers['X-Session-Token'] = $TokenValue }
        $resp = Invoke-RestMethod -Uri $HealthUrl -Method Get -Headers $headers -TimeoutSec 3
        return ($null -ne $resp -and $resp.status -eq 'ok')
    }
    catch { return $false }
}

if (Test-Health -TokenValue $Token) {
    if ($ReuseRunning) {
        Write-Ok "a backend is already healthy on $BaseUrl - reusing it"
        $backendStarted = $false
    }
    else {
        Write-Err "port $Port is already serving a CodeSlice backend."
        Write-Host "         Re-run with -ReuseRunning to attach to it, or -Port <other>." -ForegroundColor DarkGray
        exit 1
    }
}
else {
    $backendStarted = $true
}

# --------------------------------------------------------------------------
# 1. Start the backend in its own window
# --------------------------------------------------------------------------
$backendProc = $null

if ($backendStarted) {
    Write-Step 'Starting backend'

    if (-not (Test-Path -LiteralPath (Join-Path $BackendDir 'app\main.py'))) {
        Write-Err "backend/app/main.py not found - the backend workstream has not landed yet."
        Write-Host "         Expected: $(Join-Path $BackendDir 'app\main.py')" -ForegroundColor DarkGray
        exit 1
    }

    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) {
        Write-Err 'uv is not on PATH. Install it: https://docs.astral.sh/uv/'
        exit 1
    }
    Write-Ok "uv $(& uv --version)"

    # Ensure dependencies are installed for the checked-out backend.
    Write-Host '    uv sync (idempotent)'
    Push-Location $BackendDir
    try {
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & uv sync --quiet 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
            $syncExit = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $prevEap }
        if ($syncExit -ne 0) { Write-Warn2 "uv sync exited $syncExit (continuing with the existing venv)" }
        else { Write-Ok 'dependencies in sync' }
    }
    finally { Pop-Location }

    # Launch in a new window so its logs stay readable and Ctrl+C there kills it.
    # NOTE: `fastapi dev` does NOT read CODESLICE_PORT - fastapi-cli defines its
    # own --port option (default 8000, envvar "PORT") and runs uvicorn directly
    # against app.main:app, so app.main:main() never runs. The port must be
    # passed explicitly or the health poll below waits on the wrong port.
    $escapedBackend = $BackendDir.Replace("'", "''")
    $escapedRepo = $RepoRoot.Replace("'", "''")
    $inner = @"
`$env:CODESLICE_TOKEN = '$Token'
`$env:CODESLICE_PORT = '$Port'
`$env:CODESLICE_HOST = '127.0.0.1'
`$env:CODESLICE_DATA_DIR = Join-Path '$escapedRepo' '.devdata'
Set-Location -LiteralPath '$escapedBackend'
Write-Host 'CodeSlice backend (dev)' -ForegroundColor Cyan
uv run fastapi dev app/main.py --host 127.0.0.1 --port $Port
"@

    $backendProc = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList @('-NoExit', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $inner) `
        -WorkingDirectory $BackendDir -PassThru
    Write-Ok "backend window started (pid $($backendProc.Id))"
}

# --------------------------------------------------------------------------
# 2. Wait for /api/health
# --------------------------------------------------------------------------
Write-Step 'Waiting for the backend to become healthy'

$deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
$healthy = $false
$lastError = $null

while ((Get-Date) -lt $deadline) {
    if (Test-Health -TokenValue $Token) { $healthy = $true; break }

    if ($backendProc -and $backendProc.HasExited) {
        Write-Err "the backend window exited early (code $($backendProc.ExitCode))."
        Write-Host '         Check the backend window output for the traceback.' -ForegroundColor DarkGray
        Stop-BackendWindow -Proc $backendProc
        exit 1
    }
    try {
        Invoke-WebRequest -Uri $HealthUrl -Method Get -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop | Out-Null
        # Reachable but unhealthy/unexpected payload - keep polling briefly.
    }
    catch {
        $lastError = $_.Exception.Message
    }
    Start-Sleep -Milliseconds 500
}

if (-not $healthy) {
    Write-Err "backend did not become healthy within $StartupTimeoutSeconds s."
    if ($lastError) { Write-Host "         last error: $lastError" -ForegroundColor DarkGray }
    Stop-BackendWindow -Proc $backendProc
    Write-Host '         Stopped the backend window so the port is free for the next run.' -ForegroundColor DarkGray
    exit 1
}

$health = Invoke-RestMethod -Uri $HealthUrl -Method Get -Headers @{ 'X-Session-Token' = $Token } -TimeoutSec 5
Write-Ok ("healthy: version={0} data_dir={1}" -f $health.version, $health.data_dir)
Write-Host "    token for the UI / API: $Token" -ForegroundColor DarkGray
Write-Host "    API docs:               $BaseUrl/api/docs" -ForegroundColor DarkGray

# --------------------------------------------------------------------------
# 3. Desktop shell (or browser fallback)
# --------------------------------------------------------------------------
if ($NoDesktop) {
    Write-Step 'Backend only (-NoDesktop)'
    Write-Host '    The backend keeps running in its own window. Close it to stop.' -ForegroundColor DarkGray
    exit 0
}

Write-Step 'Starting the desktop shell'

$desktopPkg = Join-Path $DesktopDir 'package.json'
if (-not (Test-Path -LiteralPath $desktopPkg)) {
    Write-Warn2 "desktop/package.json not found - the Electron workstream has not landed yet."
    Write-Host '    The backend is still running; you can use the renderer in a browser instead:' -ForegroundColor DarkGray
    $frontendPkg = Join-Path $RepoRoot 'frontend\package.json'
    if (Test-Path -LiteralPath $frontendPkg) {
        Write-Host '      cd frontend; npm install; npm run dev' -ForegroundColor DarkGray
        Write-Host '      then open http://localhost:3000 and paste the token above' -ForegroundColor DarkGray
    }
    Write-Host '    Or drive the API directly:' -ForegroundColor DarkGray
    Write-Host "      curl $HealthUrl -H `"X-Session-Token: $Token`"" -ForegroundColor DarkGray
    exit 0
}

$nodeModules = Join-Path $DesktopDir 'node_modules'
if (-not (Test-Path -LiteralPath $nodeModules)) {
    Write-Host '    npm install (desktop)'
    Push-Location $DesktopDir
    try {
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & npm install --no-audit --no-fund 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
            $npmExit = $LASTEXITCODE
        }
        finally { $ErrorActionPreference = $prevEap }
        if ($npmExit -ne 0) { Write-Err 'npm install failed in desktop/'; exit 1 }
    }
    finally { Pop-Location }
}

# The shell reads these to reach the running backend and to inject the token.
# desktop/electron/main.ts reads CODESLICE_SESSION_TOKEN (not CODESLICE_TOKEN);
# the backend itself reads CODESLICE_TOKEN.
$env:CODESLICE_TOKEN = $Token
$env:CODESLICE_SESSION_TOKEN = $Token
$env:CODESLICE_PORT = "$Port"
$env:CODESLICE_DEV_URL = 'http://127.0.0.1:3000'
$env:CODESLICE_API_BASE = $BaseUrl

Push-Location $DesktopDir
try {
    Write-Host '    npm run dev (desktop) - close this window or press Ctrl+C to stop' -ForegroundColor DarkGray
    & npm run dev
    $desktopExit = $LASTEXITCODE
}
finally { Pop-Location }

if ($desktopExit -ne 0) {
    Write-Err "desktop dev exited with code $desktopExit"
    exit $desktopExit
}

Write-Ok 'desktop dev session ended'
Write-Host '    The backend may still be running in its own window.' -ForegroundColor DarkGray
exit 0
