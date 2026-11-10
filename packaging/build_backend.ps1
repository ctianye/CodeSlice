<#
.SYNOPSIS
    Build the CodeSlice local backend into a self-contained one-dir bundle
    and smoke-test the produced executable.

.DESCRIPTION
    Idempotent pipeline:
      1. verify the backend virtualenv (uv / Python 3.11) exists,
      2. install the build-only requirements from packaging/requirements-packaging.txt,
      3. remove stale build + dist output,
      4. run PyInstaller against packaging/specs/codeslice-backend.spec,
      5. smoke-test the exe: launch with CODESLICE_PORT=0 and a temp
         CODESLICE_DATA_DIR, wait for the "CODESLICE_READY {...}" stdout line,
         call GET /api/health with the session token, then terminate it.

    Produces packaging/dist-backend/codeslice-backend/codeslice-backend.exe.
    Prints PASS / FAIL and exits 0 / 1 accordingly.

.PARAMETER SkipSmoke
    Build only; do not run the executable.

.PARAMETER SkipInstall
    Do not install packaging requirements (assumes PyInstaller is present).

.PARAMETER SmokeTimeoutSeconds
    How long to wait for the READY line + health response. Default 120.

.EXAMPLE
    pwsh -File packaging\build_backend.ps1

.NOTES
    Requires PowerShell 7 (works on 5.1 as well) on Windows.
#>
[CmdletBinding()]
param(
    [switch]$SkipSmoke,
    [switch]$SkipInstall,
    [int]$SmokeTimeoutSeconds = 120
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
$script:Failures = 0

function Write-Step([string]$Message) {
    Write-Host ''
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok([string]$Message) { Write-Host "    [ok]   $Message" -ForegroundColor Green }
function Write-Warn2([string]$Message) { Write-Host "    [warn] $Message" -ForegroundColor Yellow }
function Write-Err([string]$Message) {
    Write-Host "    [FAIL] $Message" -ForegroundColor Red
    $script:Failures++
}

function Exit-WithResult {
    Write-Host ''
    if ($script:Failures -eq 0) {
        Write-Host 'PASS: backend bundle built and verified.' -ForegroundColor Green
        exit 0
    }
    Write-Host "FAIL: $($script:Failures) problem(s) above." -ForegroundColor Red
    exit 1
}

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
$PackagingDir = $PSScriptRoot
$RepoRoot     = Split-Path -Parent $PackagingDir
$SpecPath     = Join-Path $PackagingDir 'specs\codeslice-backend.spec'
$ReqsPath     = Join-Path $PackagingDir 'requirements-packaging.txt'
$VenvPython   = Join-Path $RepoRoot 'backend\.venv\Scripts\python.exe'
$BuildDir     = Join-Path $PackagingDir 'build\backend'
$DistRoot     = Join-Path $PackagingDir 'dist-backend'
# PyInstaller always emits a *nested* one-dir layout:
#     dist-backend\codeslice-backend\codeslice-backend.exe + _internal\
# The Electron shell and desktop/electron-builder.yml expect a FLAT directory:
#     resources\backend\codeslice-backend.exe
# so step 4b moves the contents up one level. Keeping the flattening here (and
# not in generate a second copy) means there is exactly one bundle on disk.
$NestedDist = Join-Path $DistRoot 'codeslice-backend'
$ExePath    = Join-Path $DistRoot 'codeslice-backend.exe'

Write-Host 'CodeSlice backend packaging' -ForegroundColor White
Write-Host "  repo:      $RepoRoot"
Write-Host "  spec:      $SpecPath"
Write-Host "  output:    $ExePath"

# --------------------------------------------------------------------------
# 0. Preconditions
# --------------------------------------------------------------------------
Write-Step 'Checking prerequisites'

if (-not (Test-Path -LiteralPath $SpecPath)) { Write-Err "spec not found: $SpecPath"; Exit-WithResult }
if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Err "backend venv not found: $VenvPython"
    Write-Host '         Create it first:  cd backend; uv venv --python 3.11; uv sync' -ForegroundColor DarkGray
    Exit-WithResult
}

$pyVersion = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
if ($LASTEXITCODE -ne 0) { Write-Err "venv python is not usable: $pyVersion"; Exit-WithResult }
Write-Ok "backend venv python $pyVersion"

$entryPoint = Join-Path $RepoRoot 'backend\app\main.py'
if (-not (Test-Path -LiteralPath $entryPoint)) {
    Write-Warn2 "backend/app/main.py is missing - the bundle will still build,"
    Write-Warn2 "but the produced exe cannot import the application. Re-run once the backend lands."
}

$uv  = Get-Command uv -ErrorAction SilentlyContinue
# uv-created venvs usually ship without pip. Detect it by file presence: running
# a probe process here would leak a traceback into the pipeline on PS 5.1.
$hasPip = (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $VenvPython) 'pip.exe'))

# --------------------------------------------------------------------------
# 1. Build requirements
# --------------------------------------------------------------------------
if (-not $SkipInstall) {
    Write-Step 'Installing packaging requirements'
    if (-not (Test-Path -LiteralPath $ReqsPath)) {
        Write-Err "requirements file not found: $ReqsPath"
    }
    elseif ($uv) {
        Write-Host "    uv pip install -r $ReqsPath"
        & uv pip install --python $VenvPython -r $ReqsPath
        if ($LASTEXITCODE -ne 0) { Write-Err 'uv pip install failed' }
        else { Write-Ok 'packaging requirements installed (uv)' }
    }
    elseif ($hasPip) {
        Write-Host "    python -m pip install -r $ReqsPath"
        & $VenvPython -m pip install --disable-pip-version-check -r $ReqsPath
        if ($LASTEXITCODE -ne 0) { Write-Err 'pip install failed' }
        else { Write-Ok 'packaging requirements installed (pip)' }
    }
    else {
        Write-Err 'Neither uv nor pip is available; install PyInstaller manually into the venv.'
    }
}

# --------------------------------------------------------------------------
# 2. Verify PyInstaller importable
# --------------------------------------------------------------------------
Write-Step 'Verifying PyInstaller'
$pyiVersion = & $VenvPython -c "import PyInstaller; print(PyInstaller.__version__)" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Err "PyInstaller is not importable in the venv: $pyiVersion"
    Write-Host '         Run without -SkipInstall, or: uv pip install -r packaging\requirements-packaging.txt' -ForegroundColor DarkGray
    Exit-WithResult
}
Write-Ok "PyInstaller $pyiVersion"

if ($script:Failures -gt 0) { Exit-WithResult }

# --------------------------------------------------------------------------
# 3. Clean
# --------------------------------------------------------------------------
Write-Step 'Cleaning previous build output'
foreach ($dir in @($BuildDir, $DistRoot)) {
    if (Test-Path -LiteralPath $dir) {
        Remove-Item -LiteralPath $dir -Recurse -Force
        Write-Ok "removed $dir"
    }
    else {
        Write-Ok "already clean: $dir"
    }
}
New-Item -ItemType Directory -Path $BuildDir -Force | Out-Null

# --------------------------------------------------------------------------
# 4. Build
# --------------------------------------------------------------------------
Write-Step 'Running PyInstaller'
$pyiArgs = @(
    '-m', 'PyInstaller'
    '--noconfirm'
    '--clean'
    '--log-level', 'WARN'
    '--distpath', $DistRoot
    '--workpath', $BuildDir
    $SpecPath
)
Write-Host "    $VenvPython $($pyiArgs -join ' ')" -ForegroundColor DarkGray

$buildStart = Get-Date
$buildLog = Join-Path $BuildDir 'pyinstaller.log'
Push-Location $RepoRoot
try {
    # PS 5.1 turns native stderr into terminating errors when
    # $ErrorActionPreference = 'Stop', so relax it just for this call.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $VenvPython @pyiArgs 2>&1 | Tee-Object -FilePath $buildLog | ForEach-Object { Write-Host "    $_" }
        $buildExit = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $prevEap }
}
finally { Pop-Location }

$buildDuration = (Get-Date) - $buildStart
if ($buildExit -ne 0) {
    Write-Err "PyInstaller failed (exit $buildExit). Full log: $buildLog"
    Exit-WithResult
}
Write-Ok ("PyInstaller finished in {0:n1}s" -f $buildDuration.TotalSeconds)

# --------------------------------------------------------------------------
# 4b. Flatten dist-backend\codeslice-backend\* -> dist-backend\*
# --------------------------------------------------------------------------
# desktop/electron-builder.yml does:
#     extraResources:
#       - from: ../packaging/dist-backend
#         to: backend
# and desktop/electron/main.ts spawns
#     <resourcesPath>/backend/codeslice-backend.exe
# i.e. it wants the exe DIRECTLY inside dist-backend/. Hoisting the nested
# PyInstaller output here keeps both consumers correct without touching
# desktop/, which belongs to another workstream.
Write-Step 'Flattening the bundle for electron-builder'

if (-not (Test-Path -LiteralPath (Join-Path $NestedDist 'codeslice-backend.exe'))) {
    Write-Err "PyInstaller output not found: $(Join-Path $NestedDist 'codeslice-backend.exe')"
    Exit-WithResult
}

Get-ChildItem -LiteralPath $NestedDist -Force | ForEach-Object {
    Move-Item -LiteralPath $_.FullName -Destination $DistRoot -Force
}
Remove-Item -LiteralPath $NestedDist -Recurse -Force -ErrorAction SilentlyContinue

if (-not (Test-Path -LiteralPath $ExePath)) {
    Write-Err "expected executable not found after flattening: $ExePath"
    Exit-WithResult
}
if (-not (Test-Path -LiteralPath (Join-Path $DistRoot '_internal'))) {
    Write-Err "expected '_internal' directory missing after flattening: $DistRoot\_internal"
    Exit-WithResult
}
Write-Ok "flat layout: $DistRoot\codeslice-backend.exe (+ _internal\)"

$exeSizeMb = [math]::Round((Get-Item -LiteralPath $ExePath).Length / 1MB, 2)
$bundleMb  = [math]::Round(((Get-ChildItem -LiteralPath $DistRoot -Recurse -File |
                             Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
Write-Ok "executable: $ExePath ($exeSizeMb MB, bundle $bundleMb MB)"

# --------------------------------------------------------------------------
# 5. Smoke test
# --------------------------------------------------------------------------
if ($SkipSmoke) {
    Write-Step 'Smoke test skipped (-SkipSmoke)'
    Exit-WithResult
}

Write-Step 'Smoke-testing the bundled executable'

$tempDataDir = Join-Path ([System.IO.Path]::GetTempPath()) ("codeslice-smoke-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $tempDataDir -Force | Out-Null
Write-Host "    CODESLICE_DATA_DIR=$tempDataDir"

# --- 5a. selftest: grammars + keyring ---------------------------------------
Write-Host '    running --selftest (imports app, parses with every grammar, checks keyring)'
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'   # native stderr must not become terminating
try {
    $selfTestOut = & $ExePath --selftest 2>&1
    $selfTestExit = $LASTEXITCODE
}
finally { $ErrorActionPreference = $prevEap }
foreach ($line in $selfTestOut) { Write-Host "    $line" -ForegroundColor DarkGray }
if ($selfTestExit -ne 0) {
    Write-Err "--selftest failed (exit $selfTestExit) - a grammar or keyring backend is missing from the bundle"
}
else {
    Write-Ok 'selftest passed (app imports, all grammars parse, keyring backend resolved)'
}

$env:CODESLICE_PORT = '0'
$env:CODESLICE_DATA_DIR = $tempDataDir
$env:CODESLICE_HOST = '127.0.0.1'
$env:CODESLICE_TOKEN = ''   # empty -> the exe generates a token and reports it
$env:CODESLICE_LOG_LEVEL = 'warning'

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $ExePath
$psi.WorkingDirectory = Split-Path -Parent $ExePath
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true
# The child inherits this process' environment, which the $env: block above just
# set. Poking $psi.EnvironmentVariables directly threw "cannot index into a null
# array" on hosts where that property is not materialised, which aborted the
# smoke test (and the whole build) right after a passing selftest.

$proc = $null
$ready = $null
$stdoutTail = New-Object System.Collections.Generic.List[string]
$stderrTail = New-Object System.Collections.Generic.List[string]

try {
    $proc = [System.Diagnostics.Process]::Start($psi)
    Write-Host "    launched pid $($proc.Id)"

    $deadline = (Get-Date).AddSeconds($SmokeTimeoutSeconds)
    $stdoutTask = $proc.StandardOutput.ReadLineAsync()
    $stderrTask = $proc.StandardError.ReadLineAsync()

    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) { break }

        if ($stdoutTask -and $stdoutTask.IsCompleted) {
            $line = $stdoutTask.Result
            if ($null -eq $line) { break }          # stdout closed -> process is done
            $stdoutTail.Add($line)
            if ($line -match '^CODESLICE_READY\s+(\{.*\})') {
                $ready = $Matches[1] | ConvertFrom-Json
                break
            }
            if ($line -match '^CODESLICE_ERROR\s+(.*)') {
                Write-Err "backend reported: $($Matches[1])"
                break
            }
            $stdoutTask = $proc.StandardOutput.ReadLineAsync()
            continue
        }

        if ($stderrTask -and $stderrTask.IsCompleted) {
            $errLine = $stderrTask.Result
            if ($null -ne $errLine) { $stderrTail.Add($errLine) }
            $stderrTask = $proc.StandardError.ReadLineAsync()
        }

        Start-Sleep -Milliseconds 50
    }
    # Drain whatever stderr arrived while we waited.
    Start-Sleep -Milliseconds 200
    while ($stderrTask -and $stderrTask.IsCompleted -and $null -ne $stderrTask.Result) {
        $stderrTail.Add($stderrTask.Result)
        $stderrTask = $proc.StandardError.ReadLineAsync()
        Start-Sleep -Milliseconds 20
    }

    if ($null -eq $ready) {
        Write-Err "did not see a CODESLICE_READY line within $SmokeTimeoutSeconds s"
        if ($stdoutTail.Count -gt 0) {
            Write-Host '    --- stdout ---' -ForegroundColor DarkGray
            $stdoutTail | Select-Object -Last 30 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        }
        if ($stderrTail.Count -gt 0) {
            Write-Host '    --- stderr ---' -ForegroundColor DarkGray
            $stderrTail | Select-Object -Last 30 | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        }
        Exit-WithResult
    }

    $readyPort  = [int]$ready.port
    $readyToken = [string]$ready.token
    Write-Ok "handshake: port=$readyPort token=$($readyToken.Substring(0, [Math]::Min(6, $readyToken.Length)))..."

    if ($readyPort -le 0) { Write-Err "handshake reported an invalid port: $readyPort" }
    if ([string]::IsNullOrWhiteSpace($readyToken)) { Write-Err 'handshake reported an empty token' }

    # --- health check -----------------------------------------------------
    $baseUrl = "http://127.0.0.1:$readyPort"
    $health = $null
    $healthError = $null
    $healthDeadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $healthDeadline) {
        try {
            $health = Invoke-RestMethod -Uri "$baseUrl/api/health" -Method Get `
                -Headers @{ 'X-Session-Token' = $readyToken } -TimeoutSec 5
            break
        }
        catch {
            $healthError = $_.Exception.Message
            Start-Sleep -Milliseconds 250
        }
    }

    if ($null -eq $health) {
        Write-Err "GET /api/health failed: $healthError"
    }
    else {
        Write-Ok ("GET /api/health -> status={0} version={1}" -f $health.status, $health.version)
        if ($health.status -ne 'ok') { Write-Err "unexpected health status: $($health.status)" }
        if (-not $health.version) { Write-Warn2 'health response has no version field' }
        if ($health.data_dir -and ($health.data_dir -ne $tempDataDir)) {
            Write-Warn2 "health reports data_dir=$($health.data_dir) (expected $tempDataDir)"
        }
    }

    # --- auth is enforced -------------------------------------------------
    try {
        Invoke-RestMethod -Uri "$baseUrl/api/projects" -Method Get -TimeoutSec 5 | Out-Null
        Write-Err 'request without X-Session-Token was accepted (auth middleware missing?)'
    }
    catch {
        $status = $null
        try { $status = [int]$_.Exception.Response.StatusCode } catch { $status = $null }
        if ($status -eq 401) { Write-Ok 'unauthenticated request correctly rejected with 401' }
        else { Write-Warn2 "unauthenticated request rejected, but status was '$status' (expected 401)" }
    }

    # --- database created in the temp data dir ----------------------------
    $dbFile = Join-Path $tempDataDir 'codeslice.db'
    if (Test-Path -LiteralPath $dbFile) {
        Write-Ok "sqlite database initialised: $dbFile"
    }
    else {
        Write-Warn2 "no codeslice.db in $tempDataDir (app may create it lazily)"
    }
}
finally {
    if ($proc -and -not $proc.HasExited) {
        Write-Host "    terminating pid $($proc.Id)"
        try { Stop-Process -Id $proc.Id -Force -ErrorAction Stop } catch { Write-Warn2 "could not stop pid $($proc.Id): $($_.Exception.Message)" }
        try { $proc.WaitForExit(10000) | Out-Null } catch { }
    }
    if ($proc) { $proc.Dispose() }

    foreach ($name in 'CODESLICE_PORT', 'CODESLICE_DATA_DIR', 'CODESLICE_HOST', 'CODESLICE_TOKEN', 'CODESLICE_LOG_LEVEL') {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $tempDataDir) {
        try { Remove-Item -LiteralPath $tempDataDir -Recurse -Force -ErrorAction Stop }
        catch { Write-Warn2 ("could not remove temp dir {0}: {1}" -f $tempDataDir, $_.Exception.Message) }
    }
}

Exit-WithResult
