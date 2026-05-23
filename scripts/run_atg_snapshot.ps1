<#
.SYNOPSIS
    Run the Pumpvision ATG tank stock snapshot (current/live reading).

.DESCRIPTION
    Wraps:
        python -X utf8 scrapers/daily_scrape.py --atg-only

    ATG is a live/current snapshot of what is in the tanks RIGHT NOW.
    It is NOT historical accounting data. No date argument is used or needed.
    Run this on a repeating schedule (every 30 or 60 minutes) separately from
    completed-shift. Do NOT include ATG in the completed-shift task.

    Logs all output (stdout + stderr) to:
        data/logs/scheduler/atg_snapshot_YYYYMMDD_HHMMSS.log

    DATABASE_URL is required in the environment. This script checks that it is present
    but never prints or logs its value.

    ASCII-safe: parses correctly under Windows PowerShell 5 (powershell.exe).

.PARAMETER PythonExe
    Full path to python.exe.
    Default: C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe

.EXAMPLE
    # Standard use (no date - ATG is always current):
    .\scripts\run_atg_snapshot.ps1

    # Override Python path:
    .\scripts\run_atg_snapshot.ps1 -PythonExe "C:\Python312\python.exe"
#>
[CmdletBinding()]
param(
    [string]$PythonExe = "C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Decode Python's UTF-8 output correctly in the host console
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# -- Locate repo root (this script lives at <repo>/scripts/) ------------------
$RepoRoot = Split-Path -Parent $PSScriptRoot

# -- Guard: DATABASE_URL must be set externally -------------------------------
if (-not $env:DATABASE_URL) {
    Write-Host "[ERROR] DATABASE_URL is not set in the environment." -ForegroundColor Red
    Write-Host "        Set it via System > Environment Variables before running." -ForegroundColor Red
    Write-Host "        Do not paste the value into this script." -ForegroundColor Red
    exit 1
}

# -- Guard: Python must exist -------------------------------------------------
if (-not (Test-Path $PythonExe)) {
    Write-Host "[ERROR] Python not found: $PythonExe" -ForegroundColor Red
    Write-Host "        Override with -PythonExe if installed elsewhere." -ForegroundColor Red
    exit 1
}

# -- Prepare log directory + file ---------------------------------------------
$RunTs   = (Get-Date).ToString("yyyyMMdd_HHmmss")
$LogDir  = Join-Path $RepoRoot "data\logs\scheduler"
$LogFile = Join-Path $LogDir "atg_snapshot_${RunTs}.log"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# -- Build Python arguments ---------------------------------------------------
$ScriptPath = Join-Path $RepoRoot "scrapers\daily_scrape.py"
$ArgList    = @("-X", "utf8", $ScriptPath, "--atg-only")

# -- Print + log header -------------------------------------------------------
$StartedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$Header = @(
    "======================================================="
    "  Pumpvision - ATG tank snapshot"
    "======================================================="
    "  mode       : --atg-only (live/current - no date)"
    "  log        : $LogFile"
    "  started    : $StartedAt"
    "======================================================="
    ""
)
$Header | ForEach-Object { Write-Host $_ }
$Header | Set-Content -Path $LogFile -Encoding utf8

# -- Run Python ---------------------------------------------------------------
# Call operator with splatting (@ArgList): PowerShell quotes each element
# individually, so paths containing spaces (e.g. "Rishab 2") are passed
# correctly without manual escaping.
# Stderr is redirected to a temp file (2>); stdout is captured as a string
# array. $LASTEXITCODE is set by the native process -- no ExitCode property.
$TmpErr = "${LogFile}.stderr.tmp"

$PrevLocation = Get-Location
Set-Location $RepoRoot
$outLines = & $PythonExe @ArgList 2> $TmpErr
$ExitCode = $LASTEXITCODE
Set-Location $PrevLocation

if ($outLines) {
    $outLines | Out-Host
    $outLines | Add-Content -Path $LogFile -Encoding utf8
}

if (Test-Path $TmpErr) {
    $errLines = Get-Content $TmpErr -Encoding utf8 -ErrorAction SilentlyContinue
    if ($errLines) {
        "" | Out-Host
        Write-Host "[stderr]" -ForegroundColor Yellow
        $errLines | Out-Host
        "", "[stderr]" | Add-Content -Path $LogFile -Encoding utf8
        $errLines            | Add-Content -Path $LogFile -Encoding utf8
    }
    Remove-Item $TmpErr -ErrorAction SilentlyContinue
}

# -- Final summary ------------------------------------------------------------
$FinishedAt  = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$StatusText  = if ($ExitCode -eq 0) { "SUCCESS" }  else { "FAILED (exit $ExitCode)" }
$StatusColor = if ($ExitCode -eq 0) { "Green" }    else { "Red" }

$Footer = @(
    ""
    "======================================================="
    "  RESULT     : $StatusText"
    "  finished   : $FinishedAt"
    "  log        : $LogFile"
    "======================================================="
)
Write-Host ""
$Footer | ForEach-Object {
    if ($_ -match "RESULT") {
        Write-Host $_ -ForegroundColor $StatusColor
    } else {
        Write-Host $_
    }
}
$Footer | Add-Content -Path $LogFile -Encoding utf8

exit $ExitCode
