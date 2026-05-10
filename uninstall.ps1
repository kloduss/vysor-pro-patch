# vysor-pro-patch / uninstall.ps1
#
# Restores app.asar.bak (the pre-patch original) over app.asar and clears
# the renderer caches. Safe to run even if no backup exists.

$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "!! $msg" -ForegroundColor Red; exit 1 }

Write-Step 'Locating Vysor install'
$vysorRoot = Join-Path $env:LOCALAPPDATA 'vysor'
$appDir = Get-ChildItem $vysorRoot -Directory -Filter 'app-*' -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending |
    Select-Object -First 1
if (-not $appDir) { Fail "No Vysor install found under $vysorRoot." }

$asar = Join-Path $appDir.FullName 'resources\app.asar'
$bak  = "$asar.bak"

if (-not (Test-Path $bak)) {
    Fail "No backup found at $bak. Cannot restore."
}

Write-Step 'Stopping running Vysor processes'
Get-Process -Name Vysor -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process -Name adb   -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

Write-Step 'Restoring original app.asar'
Copy-Item -Force $bak $asar
Write-Ok "$asar restored from $bak"

Write-Step 'Clearing Vysor renderer caches'
$profile = Join-Path $env:APPDATA 'vysor'
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

Write-Host ''
Write-Host 'Done. Vysor is back to its stock state.' -ForegroundColor Green
