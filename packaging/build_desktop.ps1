<#
.SYNOPSIS
    Build the CodeSlice Windows installer (electron-builder NSIS) from the
    frontend + Electron shell, bundling the PyInstaller backend.

.DESCRIPTION
    Step order matters because electron-builder snapshots extraResources when
    the NSIS stage runs:

      1. verify the backend bundle exists and matches the extraResources
         `from:` directory declared in desktop/electron-builder.yml,
      2. frontend: npm ci (fallback npm install) + npm run build,
      3. desktop:  electron-builder --win --dir, then rcedit (icon + version
         info), then electron-builder --win nsis --prepackaged (three stages,
         see the comment in section 4 for why),
      4. verify the produced installer under desktop/release/ and confirm the
         backend actually landed in win-unpacked/resources/backend/.

    Nothing is copied into the desktop/ tree: electron-builder reads the
    backend straight from packaging/dist-backend (the extraResources `from:`),
    so that directory IS the source of truth for <install>\resources\backend.

    The desktop/electron-builder config is owned by another workstream; this
    script only guarantees the expected on-disk layout and fails loudly when
    something is missing instead of silently producing a broken installer.
    In particular it never edits electron-builder.yml: the icon/version fix is
    applied by calling the cached rcedit-x64.exe on the unpacked exe, because
    electron-builder's own `signAndEditExecutable` path needs the winCodeSign
    archive, which cannot be extracted without administrator privileges.

.PARAMETER SkipFrontend
    Reuse an existing frontend/dist (skips the frontend build).

.PARAMETER Clean
    Remove desktop/release and force a fresh `npm ci` in frontend/ and desktop/.

.EXAMPLE
    pwsh -File packaging\build_desktop.ps1

.NOTES
    Requires Node 24+, npm, and the backend bundle from build_backend.ps1.
#>
[CmdletBinding()]
param(
    [switch]$SkipFrontend,
    [switch]$Clean,
    # CI has no electron-builder cache. Pass a downloaded rcedit-x64.exe here
    # (or let the script fetch it) instead of requiring a warm local cache.
    [string]$RceditPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

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
        Write-Host 'PASS: desktop installer built.' -ForegroundColor Green
        exit 0
    }
    Write-Host "FAIL: $($script:Failures) problem(s) above." -ForegroundColor Red
    exit 1
}

<#
    PS 5.1 turns native-command stderr into terminating errors when
    $ErrorActionPreference = 'Stop'. npm/tsc/vite write progress to stderr, so
    every native call must run with the preference relaxed. Returns the exit
    code; -Quiet suppresses per-line echo.
#>
function Invoke-Native {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [string[]]$Arguments = @(),
        [switch]$Quiet
    )
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($Quiet) {
            & $FilePath @Arguments *>&1 | Out-Null
        }
        else {
            & $FilePath @Arguments *>&1 | ForEach-Object { Write-Host "        $_" }
        }
        return $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $prevEap }
}

<#
    Hash of the icon Windows actually shows for a file (first 16 hex chars of
    the SHA-256 of the rendered bitmap). Two files with the same value display
    the same icon, which is how we prove our .ico made it into the exe.
#>
function Get-IconHash([string]$Path) {
    Add-Type -AssemblyName System.Drawing -ErrorAction SilentlyContinue
    $icon = [System.Drawing.Icon]::ExtractAssociatedIcon($Path)
    if (-not $icon) { return '' }
    $bytes = $null
    try {
        $bmp = $icon.ToBitmap()
        try {
            $ms = New-Object System.IO.MemoryStream
            try {
                $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
                $bytes = $ms.ToArray()
            }
            finally { $ms.Dispose() }
        }
        finally { $bmp.Dispose() }
    }
    finally { $icon.Dispose() }
    if (-not $bytes) { return '' }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $digest = $sha.ComputeHash($bytes) }
    finally { $sha.Dispose() }
    $hex = ($digest | ForEach-Object { $_.ToString('x2') }) -join ''
    return $hex.Substring(0, 16)
}

<#
    Locate a working rcedit.exe inside electron-builder's winCodeSign cache.
    We cannot let electron-builder run rcedit itself (signAndEditExecutable is
    false in desktop/electron-builder.yml, and turning it on requires extracting
    winCodeSign, which fails without SeCreateSymbolicLinkPrivilege), but the
    cache already holds a standalone rcedit-x64.exe that works fine.
#>
function Find-Rcedit {
    $root = Join-Path $env:LOCALAPPDATA 'electron-builder\Cache\winCodeSign'
    if (-not (Test-Path -LiteralPath $root)) { return $null }
    $candidates = @(Get-ChildItem -LiteralPath $root -Filter 'rcedit-x64.exe' -File -Recurse -ErrorAction SilentlyContinue |
        Sort-Object Length -Descending)
    foreach ($candidate in $candidates) {
        if ($candidate.Length -gt 100000) { return $candidate.FullName }
    }
    return $null
}

<#
    Resolve rcedit for both local runs and clean CI runners.

    Local builds usually already have rcedit from a previous electron-builder
    run. GitHub-hosted runners do not, and unpacking electron-builder's
    winCodeSign archive can fail without the symlink privilege, so fetch the
    standalone rcedit binary from the official electron/rcedit release instead.
#>
function Resolve-Rcedit([string]$Explicit) {
    if ($Explicit) {
        if (Test-Path -LiteralPath $Explicit) { return (Resolve-Path -LiteralPath $Explicit).Path }
        Write-Err "rcedit not found at $Explicit"
        return $null
    }

    $cached = Find-Rcedit
    if ($cached) { return $cached }

    $tempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { $env:TEMP }
    $destDir = Join-Path $tempRoot 'codeslice-build'
    $dest = Join-Path $destDir 'rcedit-x64.exe'
    if (-not (Test-Path -LiteralPath $dest)) {
        New-Item -ItemType Directory -Force -Path $destDir | Out-Null
        Write-Host '    downloading rcedit (official electron/rcedit release)...' -ForegroundColor DarkGray
        $previous = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'
        try {
            Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe' -OutFile $dest
        }
        catch {
            Write-Warn2 "rcedit download failed: $($_.Exception.Message)"
        }
        finally {
            $ProgressPreference = $previous
        }
    }
    if ((Test-Path -LiteralPath $dest) -and ((Get-Item -LiteralPath $dest).Length -gt 100000)) {
        return $dest
    }
    return $null
}

<#
    A running packaged app keeps release/win-unpacked/CodeSlice.exe open and
    electron-builder then dies with "remove ...: Access is denied." Detect it
    up front and say how to fix it instead of surfacing that cryptic error.
#>
function Test-FileLocked([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        $stream = [System.IO.File]::Open($Path, 'Open', 'ReadWrite', 'None')
        $stream.Close()
        return $false
    }
    catch { return $true }
}

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
$PackagingDir  = $PSScriptRoot
$RepoRoot      = Split-Path -Parent $PackagingDir
$FrontendDir   = Join-Path $RepoRoot 'frontend'
$DesktopDir    = Join-Path $RepoRoot 'desktop'
$BackendSrc    = Join-Path $PackagingDir 'dist-backend'
$BackendExe    = Join-Path $BackendSrc 'codeslice-backend.exe'
$ReleaseDir    = Join-Path $DesktopDir 'release'

Write-Host 'CodeSlice desktop packaging' -ForegroundColor White
Write-Host "  repo:        $RepoRoot"
Write-Host "  backend src: $BackendSrc"
Write-Host "  release:     $ReleaseDir"

# --------------------------------------------------------------------------
# 0. Prerequisites
# --------------------------------------------------------------------------
Write-Step 'Checking prerequisites'

$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCmd) {
    Write-Err 'node is not on PATH. Node 24+ is required.'
}
else {
    $nodeVersion = (& node --version) -replace '^v', ''
    $nodeMajor = [int]($nodeVersion.Split('.')[0])
    if ($nodeMajor -lt 24) { Write-Warn2 "Node $nodeVersion detected; Node 24 LTS is recommended." }
    else { Write-Ok "node $nodeVersion" }
}

$npmCmd = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npmCmd) { Write-Err 'npm is not on PATH.' }
else {
    $npmVersion = & npm --version
    Write-Ok "npm $npmVersion"
}

foreach ($dir in @($FrontendDir, $DesktopDir)) {
    if (-not (Test-Path -LiteralPath $dir)) { Write-Err "missing directory: $dir" }
    elseif (-not (Test-Path -LiteralPath (Join-Path $dir 'package.json'))) {
        Write-Err "missing package.json in $dir (frontend/desktop workstream not landed yet?)"
    }
    else { Write-Ok "found $(Split-Path -Leaf $dir)/package.json" }
}

if ($script:Failures -gt 0) {
    Write-Host ''
    Write-Host 'The frontend/desktop workstream has not produced its package.json files yet.' -ForegroundColor Yellow
    Write-Host 'Nothing was built. Re-run once frontend/ and desktop/ exist.' -ForegroundColor Yellow
    Exit-WithResult
}

# --------------------------------------------------------------------------
# 0b. electron-builder config <-> packaged layout
# --------------------------------------------------------------------------
# desktop/electron/main.ts spawns <resourcesPath>/backend/codeslice-backend.exe
# (a FLAT layout). If electron-builder.yml uses `from: ../packaging/dist-backend
# to: backend`, the whole directory is nested one level too deep and the
# packaged app cannot find the exe. Detect that here, before a 5-minute build.
Write-Step 'Checking electron-builder extraResources layout'

$builderConfigs = @(Get-ChildItem -LiteralPath $DesktopDir -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match '^electron-builder\.(yml|yaml|json)$' })
if ($builderConfigs.Count -eq 0) {
    Write-Warn2 'no electron-builder.yml/json found in desktop/ - cannot pre-verify the layout'
}
else {
    foreach ($cfg in $builderConfigs) {
        $text = Get-Content -LiteralPath $cfg.FullName -Raw
        # Inspect the backend mapping only; icon.png and frontend/ are also
        # extraResources entries but do not contain the Python executable.
        if ($text -match '(?ms)^\s*-\s*from:\s*(?<from>[^\r\n#]+)[\r\n]+(?:\s*#.*[\r\n]+)*\s*to:\s*backend\s*(?:\r?\n|$)') {
            $fromVal = $Matches['from'].Trim()
            Write-Host "    $($cfg.Name): backend from=$fromVal" -ForegroundColor DarkGray

            # Flat layout requires `from` to point at the directory that DIRECTLY
            # contains codeslice-backend.exe.
            $resolvedFrom = Join-Path $DesktopDir $fromVal
            $flatOk = Test-Path -LiteralPath (Join-Path $resolvedFrom 'codeslice-backend.exe')
            if (-not $flatOk) {
                Write-Err ("extraResources 'from: $fromVal' mapped to backend does not directly contain codeslice-backend.exe - " +
                    "the packaged app would look for resources\backend\codeslice-backend.exe and fail.")
                Write-Host '         Fix the backend mapping in desktop/electron-builder.yml so `from:` points at the flat bundle.' -ForegroundColor DarkGray
                Write-Host '         (desktop/ is owned by another workstream - this script does not edit it.)' -ForegroundColor DarkGray
            }
            else {
                Write-Ok "extraResources source is flat and contains codeslice-backend.exe"
            }
        }
        else {
            Write-Warn2 "could not locate the from/to pair in $($cfg.Name)"
        }
    }
}

if ($script:Failures -gt 0) { Exit-WithResult }

# --------------------------------------------------------------------------
# 1. Backend bundle
# --------------------------------------------------------------------------
Write-Step 'Locating the backend bundle'

if (-not (Test-Path -LiteralPath $BackendExe)) {
    Write-Err "backend executable not found: $BackendExe"
    Write-Host '         Run packaging\build_backend.ps1 first.' -ForegroundColor DarkGray
    Write-Host '         Note the bundle must be FLAT: dist-backend\codeslice-backend.exe,' -ForegroundColor DarkGray
    Write-Host '         not dist-backend\codeslice-backend\codeslice-backend.exe.' -ForegroundColor DarkGray
    Exit-WithResult
}
$bundleMb = [math]::Round(((Get-ChildItem -LiteralPath $BackendSrc -Recurse -File |
                            Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
Write-Ok "codeslice-backend.exe found ($bundleMb MB bundle)"

if ($script:Failures -gt 0) { Exit-WithResult }

# --------------------------------------------------------------------------
# 2. Prepare output directories
# --------------------------------------------------------------------------
Write-Step 'Preparing output directories'

if ($Clean) {
    if (Test-Path -LiteralPath $ReleaseDir) {
        Remove-Item -LiteralPath $ReleaseDir -Recurse -Force
        Write-Ok "removed $ReleaseDir"
    }
}

# electron-builder copies extraResources straight out of packaging/dist-backend,
# so nothing is staged into the desktop/ tree - the source directory IS what
# ends up in <install>\resources\backend. Verifying it here keeps the failure
# local (before npm/electron-builder spend a minute) and leaves the desktop/
# workstream untouched.
$layoutOk = Test-Path -LiteralPath $BackendExe
if ($layoutOk) {
    Write-Ok "extraResources source is ready: $BackendSrc"
}
else {
    Write-Err "extraResources source is missing codeslice-backend.exe: $BackendSrc"
}

# --------------------------------------------------------------------------
# 3. Frontend build
# --------------------------------------------------------------------------
Write-Step 'Building frontend'

# npm install is the slowest and most fragile step (it re-downloads Electron's
# ~115 MB zip through electron/install.js). Two guards keep it idempotent:
#   * skip it entirely when node_modules is already populated, unless -Clean;
#   * fall back to a mirror when the GitHub release download is flaky.
# `npm ci` deletes the whole tree first, so it is only used with -Clean or when
# node_modules is missing/incomplete - running it blind can leave the project
# with no dependencies at all if the network blips mid-postinstall.
if (-not $env:ELECTRON_MIRROR -and -not $env:CI) {
    $env:ELECTRON_MIRROR = 'https://npmmirror.com/mirrors/electron/'
    Write-Host "    using ELECTRON_MIRROR=$env:ELECTRON_MIRROR" -ForegroundColor DarkGray
}

function Test-NodeModulesReady([string]$Directory) {
    $modules = Join-Path $Directory 'node_modules'
    if (-not (Test-Path -LiteralPath $modules)) { return $false }
    $probe = @(Get-ChildItem -LiteralPath $modules -Force -ErrorAction SilentlyContinue)
    return ($probe.Count -gt 5)
}

function Invoke-NpmInstall([string]$Directory, [string]$Label) {
    if ((Test-NodeModulesReady -Directory $Directory) -and (-not $Clean)) {
        Write-Ok "[$Label] node_modules already present, skipping install (use -Clean to force)"
        return $true
    }

    Push-Location $Directory
    try {
        if ((Test-Path -LiteralPath (Join-Path $Directory 'package-lock.json')) -and $Clean) {
            Write-Host "    [$Label] npm ci"
            $code = Invoke-Native -FilePath 'npm' -Arguments @('ci', '--no-audit', '--no-fund')
            if ($code -eq 0) { return $true }
            Write-Warn2 "[$Label] npm ci exited $code; falling back to npm install"
        }

        Write-Host "    [$Label] npm install"
        $code = Invoke-Native -FilePath 'npm' -Arguments @('install', '--no-audit', '--no-fund')
        if ($code -ne 0) { Write-Err "[$Label] npm install failed (exit $code)"; return $false }
    }
    finally { Pop-Location }
    return $true
}

if (-not $SkipFrontend) {
    if (-not (Invoke-NpmInstall -Directory $FrontendDir -Label 'frontend')) { Exit-WithResult }

    Push-Location $FrontendDir
    try {
        Write-Host '    [frontend] npm run build'
        $code = Invoke-Native -FilePath 'npm' -Arguments @('run', 'build')
        if ($code -ne 0) { Write-Err "frontend build failed (exit $code)" }
        else { Write-Ok 'frontend build finished' }
    }
    finally { Pop-Location }

    $frontendDist = Join-Path $FrontendDir 'dist'
    if (Test-Path -LiteralPath (Join-Path $frontendDist 'index.html')) {
        Write-Ok "frontend dist: $frontendDist"
    }
    else {
        Write-Err "frontend/dist/index.html not found - the renderer bundle is missing"
    }
}
else {
    Write-Step 'Skipping frontend build (-SkipFrontend)'
}

<#
    electron-builder packages `dist/**` from desktop/ (files:) and ships a second
    copy as extraResources `from: dist -> frontend`. That desktop/dist tree is
    produced by `npm run build:renderer` (scripts/copy-renderer.mjs, a plain
    node script with no dependencies). It used to be an implicit part of
    `npm run dist`, so invoking the script directly could package whatever
    desktop/dist happened to contain - typically a renderer from an older
    checkout. Always refresh it here, in both modes, so the installer can never
    ship UI that does not match frontend/dist.
#>
Write-Step 'Syncing renderer into desktop/dist'
$desktopDistIndex = Join-Path (Join-Path $DesktopDir 'dist') 'index.html'
$copyCode = Invoke-Native -FilePath 'node' -Arguments @(
    (Join-Path $DesktopDir 'scripts\copy-renderer.mjs')
)
if ($copyCode -ne 0) {
    Write-Err "copying frontend/dist -> desktop/dist failed (exit $copyCode)"
}
elseif (Test-Path -LiteralPath $desktopDistIndex) {
    Write-Ok "desktop/dist refreshed from frontend/dist"
}
else {
    Write-Err "desktop/dist/index.html is missing after the copy"
}

if ($script:Failures -gt 0) { Exit-WithResult }

# --------------------------------------------------------------------------
# 4. Desktop installer (three stages)
# --------------------------------------------------------------------------
Write-Step 'Building desktop installer'

if (-not (Invoke-NpmInstall -Directory $DesktopDir -Label 'desktop')) { Exit-WithResult }

# electron-builder packages desktop/electron/**, so the TypeScript main/preload
# must be compiled first. A developer checkout usually already has
# electron/build/ from `npm run dev`; a clean CI runner does not.
Push-Location $DesktopDir
try {
    Write-Host '    [desktop] npm run build'
    $desktopBuildCode = Invoke-Native -FilePath 'npm' -Arguments @('run', 'build')
    if ($desktopBuildCode -ne 0) {
        Write-Err "desktop TypeScript build failed (exit $desktopBuildCode)"
    }
    else {
        Write-Ok 'electron main/preload compiled'
    }
}
finally { Pop-Location }

if (-not (Test-Path -LiteralPath (Join-Path $DesktopDir 'electron\build\main.js'))) {
    Write-Err 'electron/build/main.js is missing after npm run build'
}

if ($script:Failures -gt 0) { Exit-WithResult }

<#
    Why three stages instead of a single `npm run dist`:

    desktop/electron-builder.yml sets `win.signAndEditExecutable: false`, and
    turning it on is not an option on this machine: electron-builder would have
    to extract winCodeSign-2.6.0.7z, which contains macOS symlinks and therefore
    needs SeCreateSymbolicLinkPrivilege (7-Zip: "Cannot create symbolic link :
    the client does not hold the required privilege", exit 2). Without rcedit the
    packaged CodeSlice.exe keeps Electron's atom icon and "Electron" version
    info, which looks broken in Explorer and the taskbar.

    So we build in three explicit stages:
      1. electron-builder --win --dir      -> release/win-unpacked (also adds the
                                              asar INTEGRITY resource itself)
      2. rcedit-x64.exe on CodeSlice.exe   -> our icon + our VERSIONINFO
      3. electron-builder --win nsis
         --prepackaged release/win-unpacked -> repackages the patched tree into
                                              the NSIS installer, leaving the exe
                                              untouched.

    rcedit itself is fine and already cached; only the archive extraction is
    blocked, which is why we call the cached rcedit-x64.exe directly.
#>
$DesktopExe   = Join-Path $ReleaseDir 'win-unpacked\CodeSlice.exe'
$IconPath     = Join-Path $DesktopDir 'build-assets\icon.ico'
$RceditPath   = Resolve-Rcedit -Explicit $RceditPath

if (-not $RceditPath) {
    Write-Err 'no usable rcedit-x64.exe found and the automatic download failed'
    Write-Host '         Pass -RceditPath <path> or download it manually:' -ForegroundColor DarkGray
    Write-Host '           https://github.com/electron/rcedit/releases/download/v2.0.0/rcedit-x64.exe' -ForegroundColor DarkGray
}
else {
    Write-Ok "rcedit: $RceditPath"
}

if (-not (Test-Path -LiteralPath $IconPath)) {
    Write-Err "app icon not found: $IconPath (needed by rcedit and by electron-builder)"
}

# An app instance left running keeps CodeSlice.exe open and makes stage 3 fail
# with "remove ...: Access is denied."
if (Test-FileLocked $DesktopExe) {
    Write-Err "$DesktopExe is locked by a running CodeSlice instance"
    Write-Host '         Close the app (or: taskkill /IM CodeSlice.exe /T /F) and re-run.' -ForegroundColor DarkGray
}

if ($script:Failures -gt 0) { Exit-WithResult }

# Version strings for VERSIONINFO, taken from desktop/package.json so the
# metadata can never drift from the artifact name.
$appVersion = '0.2.4'
$pkgJson = Join-Path $DesktopDir 'package.json'
if (Test-Path -LiteralPath $pkgJson) {
    try {
        $appVersion = (Get-Content -LiteralPath $pkgJson -Raw | ConvertFrom-Json).version
    }
    catch {
        Write-Warn2 "could not read version from package.json, defaulting to $appVersion"
    }
}
$fileVersion = $appVersion
if ($fileVersion -notmatch '^\d+\.\d+\.\d+\.\d+$') { $fileVersion = "$fileVersion.0" }

<#
    Call the locally installed electron-builder directly instead of `npx`:
    npx may consult the registry (slow, and it fails outright when the network
    is flaky on this box), while node_modules/.bin/electron-builder.cmd is
    exactly the version pinned by package-lock.json.
#>
$ElectronBuilder = Join-Path $DesktopDir 'node_modules\.bin\electron-builder.cmd'
if (-not (Test-Path -LiteralPath $ElectronBuilder)) {
    $ElectronBuilder = 'npx'
    Write-Warn2 'node_modules/.bin/electron-builder.cmd not found, falling back to npx'
}

Push-Location $DesktopDir
try {
    # --- stage 1: unpacked app -------------------------------------------
    Write-Host '    [desktop] stage 1/3: electron-builder --win --dir'
    $dirCode = Invoke-Native -FilePath $ElectronBuilder -Arguments @('--win', '--dir')
    if ($dirCode -ne 0) {
        Write-Err "electron-builder --dir failed (exit $dirCode)"
    }
    elseif (-not (Test-Path -LiteralPath $DesktopExe)) {
        Write-Err "stage 1 did not produce $DesktopExe"
    }
    else {
        Write-Ok "unpacked app: $DesktopExe"
    }

    # --- stage 2: patch icon + version info ------------------------------
    if ($script:Failures -eq 0) {
        Write-Host '    [desktop] stage 2/3: rcedit (icon + VERSIONINFO)'
        $rceditArgs = @(
            $DesktopExe,
            '--set-icon', $IconPath,
            '--set-version-string', 'FileDescription', 'CodeSlice',
            '--set-version-string', 'ProductName', 'CodeSlice',
            '--set-version-string', 'CompanyName', 'CodeSlice',
            '--set-version-string', 'LegalCopyright', 'Copyright (C) 2026 CodeSlice',
            '--set-version-string', 'OriginalFilename', 'CodeSlice.exe',
            '--set-file-version', $fileVersion,
            '--set-product-version', $fileVersion
        )
        $rceditCode = Invoke-Native -FilePath $RceditPath -Arguments $rceditArgs -Quiet
        if ($rceditCode -ne 0) {
            Write-Err "rcedit failed (exit $rceditCode)"
        }
        else {
            Write-Ok "rcedit applied icon and version $fileVersion"
            $exeIcon = Get-IconHash $DesktopExe
            $srcIcon = Get-IconHash $IconPath
            if ($exeIcon -and $srcIcon -and $exeIcon -eq $srcIcon) {
                Write-Ok "icon verified: $exeIcon"
            }
            else {
                Write-Err "icon mismatch after rcedit (exe=$exeIcon source=$srcIcon)"
            }
            $vi = (Get-Item -LiteralPath $DesktopExe).VersionInfo
            if ($vi.ProductName -eq 'CodeSlice') {
                Write-Ok "version info: $($vi.ProductName) $($vi.FileVersion)"
            }
            else {
                Write-Err "version info was not applied (ProductName=$($vi.ProductName))"
            }
        }
    }

    # --- stage 3: NSIS installer -----------------------------------------
    if ($script:Failures -eq 0) {
        Write-Host '    [desktop] stage 3/3: electron-builder --win nsis --prepackaged'
        $nsisCode = Invoke-Native -FilePath $ElectronBuilder -Arguments @(
            '--win', 'nsis', '--prepackaged', 'release\win-unpacked'
        )
        if ($nsisCode -ne 0) {
            Write-Err "electron-builder --prepackaged failed (exit $nsisCode)"
        }
        else {
            Write-Ok 'electron-builder finished'
        }
    }
}
finally { Pop-Location }

# The packaged tree must not still carry the patched exe's pre-rcedit icon.
if (Test-Path -LiteralPath $DesktopExe) {
    $finalIcon = Get-IconHash $DesktopExe
    $sourceIcon = Get-IconHash $IconPath
    if ($finalIcon -and $sourceIcon -and $finalIcon -eq $sourceIcon) {
        Write-Ok "packaged exe keeps the CodeSlice icon after repackaging"
    }
    else {
        Write-Err "packaged exe icon drifted during stage 3 (exe=$finalIcon source=$sourceIcon)"
    }
}

# --------------------------------------------------------------------------
# 5. Verify artifacts
# --------------------------------------------------------------------------
Write-Step 'Verifying installer artifacts'

if (Test-Path -LiteralPath $ReleaseDir) {
    # Only top-level *.exe are installers; win-unpacked\CodeSlice.exe is the app.
    $installers = @(Get-ChildItem -LiteralPath $ReleaseDir -Filter '*.exe' -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notmatch 'uninstaller|Uninstall' })
    if ($installers.Count -gt 0) {
        foreach ($installer in $installers) {
            $sizeMb = [math]::Round($installer.Length / 1MB, 1)
            $hash = (Get-FileHash -LiteralPath $installer.FullName -Algorithm SHA256).Hash
            Write-Ok "installer: $($installer.FullName) ($sizeMb MB)"
            Write-Host "           sha256: $hash" -ForegroundColor DarkGray
        }
    }
    else {
        Write-Err "no installer (*.exe) found under $ReleaseDir"
        Write-Host '         Check desktop/package.json "build" -> nsis target and output dir.' -ForegroundColor DarkGray
    }

    $unpacked = @(Get-ChildItem -LiteralPath $ReleaseDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'win-unpacked' })
    foreach ($dir in $unpacked) {
        $packedBackend = Join-Path $dir.FullName 'resources\backend\codeslice-backend.exe'
        if (Test-Path -LiteralPath $packedBackend) {
            $backendMb = [math]::Round((Get-Item -LiteralPath $packedBackend).Length / 1MB, 1)
            Write-Ok "packaged backend present: $packedBackend ($backendMb MB)"
        }
        else {
            Write-Err "packaged app is missing resources\backend\codeslice-backend.exe in $($dir.FullName)"
            Write-Host '         The electron-builder extraResources rule probably points somewhere else.' -ForegroundColor DarkGray
        }

        <#
            electron-builder does not delete extraResources leftovers, so a stale
            renderer can survive an incremental rebuild: the packaged index.html
            then references an asset hash that no longer matches frontend/dist.
            Compare them explicitly, otherwise the installer silently ships old UI.
        #>
        $packedIndex = Join-Path $dir.FullName 'resources\frontend\index.html'
        $builtIndex = Join-Path $DesktopDir 'dist\index.html'
        if ((Test-Path -LiteralPath $packedIndex) -and (Test-Path -LiteralPath $builtIndex)) {
            $pattern = 'assets/index-[A-Za-z0-9_-]+\.js'
            $packedMatch = Select-String -LiteralPath $packedIndex -Pattern $pattern -AllMatches |
                ForEach-Object { $_.Matches } | Select-Object -First 1
            $builtMatch = Select-String -LiteralPath $builtIndex -Pattern $pattern -AllMatches |
                ForEach-Object { $_.Matches } | Select-Object -First 1
            $packedJs = if ($packedMatch) { $packedMatch.Value } else { '' }
            $builtJs = if ($builtMatch) { $builtMatch.Value } else { '' }
            if ($packedJs -and $packedJs -eq $builtJs) {
                Write-Ok "packaged renderer is current: $packedJs"
            }
            elseif (-not $packedJs -or -not $builtJs) {
                Write-Warn2 "could not compare renderer asset names (packaged='$packedJs' built='$builtJs')"
            }
            else {
                Write-Err "packaged renderer is STALE (installer has $packedJs, desktop/dist has $builtJs)"
                Write-Host '         Remove desktop/release and re-run with -Clean to drop the old extraResources copy.' -ForegroundColor DarkGray
            }
        }
    }
}
else {
    Write-Err "release directory not produced: $ReleaseDir"
}

Exit-WithResult
