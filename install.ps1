# vysor-pro-patch / install.ps1
#
# One-shot installer. Downloads the patched app.asar from this repo and
# drops it on top of the user's Vysor install, after stopping the app
# and clearing its renderer caches.
#
# Run with:
#   iwr https://raw.githubusercontent.com/kloduss/vysor-pro-patch/main/install.ps1 -UseBasicParsing | iex
#
# or, after cloning, just double-click install.bat.

$ErrorActionPreference = 'Stop'

$RepoRaw = 'https://raw.githubusercontent.com/kloduss/vysor-pro-patch/main'
$AsarUrl = "$RepoRaw/app.asar"
$ApkUrl  = "$RepoRaw/Vysor-release.apk"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "!! $msg" -ForegroundColor Red; exit 1 }

# 1. locate the Vysor install ----------------------------------------------
Write-Step 'Locating Vysor install'
$vysorRoot = Join-Path $env:LOCALAPPDATA 'vysor'
if (-not (Test-Path $vysorRoot)) {
    Fail "Vysor not found at $vysorRoot. Install Vysor from https://vysor.io first."
}

$appDir = Get-ChildItem $vysorRoot -Directory -Filter 'app-*' |
    Sort-Object Name -Descending |
    Select-Object -First 1
if (-not $appDir) {
    Fail "No app-X.Y.Z directory found under $vysorRoot."
}

$resources = Join-Path $appDir.FullName 'resources'
$asar = Join-Path $resources 'app.asar'
$bak  = "$asar.bak"
if (-not (Test-Path $asar)) {
    Fail "app.asar not found at $asar."
}
Write-Ok "Found $($appDir.Name) -> $asar"

# 2. stop Vysor -------------------------------------------------------------
Write-Step 'Stopping running Vysor processes'
Get-Process -Name Vysor -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process -Name adb   -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
Write-Ok 'Done'

# 3. back up the original ---------------------------------------------------
Write-Step 'Backing up original app.asar'
if (-not (Test-Path $bak)) {
    Copy-Item $asar $bak
    Write-Ok "Saved -> $bak"
} else {
    Write-Warn2 "Backup already exists at $bak (not overwriting)"
}

# 4. download patched app.asar ---------------------------------------------
Write-Step "Downloading patched app.asar"
$tmp = Join-Path $env:TEMP "vysor-pro-app.asar.$([guid]::NewGuid().ToString('N'))"
try {
    Invoke-WebRequest -Uri $AsarUrl -OutFile $tmp -UseBasicParsing
} catch {
    Fail "Download failed: $($_.Exception.Message)"
}
$size = (Get-Item $tmp).Length
if ($size -lt 5MB) {
    Remove-Item $tmp -Force
    Fail "Downloaded file is suspiciously small ($size bytes); aborting."
}
Write-Ok "Got $size bytes"

# 5. drop it in place -------------------------------------------------------
Write-Step "Replacing app.asar"
Move-Item -Force $tmp $asar
Write-Ok "Wrote -> $asar"

# 5b. sync the native Vysor APK into app.asar.unpacked ---------------------
# The renderer references /native/android/Vysor-release.<hash>.apk which is
# resolved through app.asar.unpacked, so the file must keep its hashed name.
# Drop the current APK in there so the bundled daemon matches the bundled
# renderer protocol (vysor-io-130).
Write-Step "Syncing native Vysor APK"
$apkName = 'Vysor-release.2f862e923a9ebcd21c2d8898c683b47a5362568c7091dd8e80337952b5020b5f.apk'
$unpackedNative = Join-Path $resources 'app.asar.unpacked\native\android'
if (-not (Test-Path $unpackedNative)) {
    New-Item -ItemType Directory -Force -Path $unpackedNative | Out-Null
}
$tmpApk = Join-Path $env:TEMP "vysor-pro-release.apk.$([guid]::NewGuid().ToString('N'))"
try {
    Invoke-WebRequest -Uri $ApkUrl -OutFile $tmpApk -UseBasicParsing
} catch {
    Fail "APK download failed: $($_.Exception.Message)"
}
$apkSize = (Get-Item $tmpApk).Length
if ($apkSize -lt 1MB) {
    Remove-Item $tmpApk -Force
    Fail "Downloaded APK is suspiciously small ($apkSize bytes); aborting."
}
Get-ChildItem $unpackedNative -Filter 'Vysor-release.*.apk' -ErrorAction SilentlyContinue |
    Remove-Item -Force
Move-Item -Force $tmpApk (Join-Path $unpackedNative $apkName)
Write-Ok "Wrote -> $(Join-Path $unpackedNative $apkName)"

# 6. clear renderer caches so the new bundle is actually loaded ------------
Write-Step 'Clearing Vysor renderer caches'
$profile = Join-Path $env:APPDATA 'vysor'
if (Test-Path $profile) {
    foreach ($rel in @(
        'Service Worker\CacheStorage',
        'Service Worker\ScriptCache',
        'Service Worker\Database',
        'Cache',
        'Code Cache'
    )) {
        $p = Join-Path $profile $rel
        if (Test-Path $p) {
            try { Remove-Item -Recurse -Force -LiteralPath $p }
            catch { Write-Warn2 "Could not delete $p ($($_.Exception.Message))" }
        }
    }
    Write-Ok 'Caches wiped'
} else {
    Write-Warn2 "No $profile directory yet, nothing to clear"
}

Write-Host ''
Write-Host 'Done.' -ForegroundColor Green
Write-Host 'Launch Vysor and About should now read "Vysor Pro".'
