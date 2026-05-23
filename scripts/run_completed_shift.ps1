<#
.SYNOPSIS
    Run the Pumpvision completed-shift accounting scrape for yesterday (or a named date).

.DESCRIPTION
    Wraps:
        python -X utf8 scrapers/daily_scrape.py --completed-shift --date YYYY-MM-DD
                                                 --paytm-wait-seconds N

    op_date defaults to yesterday (today - 1 day), assuming the script runs after 06:00
    local time on the current calendar day.

    Logs all output (stdout + stderr) to:
        data/logs/scheduler/completed_shift_YYYYMMDD_HHMMSS.log

    DATABASE_URL is required in the environment. This script checks that it is present
    but never prints or logs its value.

    Do NOT add --iras-manual-captcha here -- it would block a scheduled run indefinitely.

    ASCII-safe: parses correctly under Windows PowerShell 5 (powershell.exe).

.PARAMETER Date
    Accounting op_date in YYYY-MM-DD format.
    Default: yesterday (Get-Date).AddDays(-1).
    Always supply -Date when running manually for the first time to verify correctness.

.PARAMETER PaytmWaitSeconds
    Maximum seconds to poll for the Paytm report download link.
    Default: 900 (15 minutes). Pass 0 only for fully manual/supervised runs.

.PARAMETER PythonExe
    Full path to python.exe.
    Default: C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe

.EXAMPLE
    # Standard scheduled use - op_date is yesterday:
    .\scripts\run_completed_shift.ps1

    # Manual test with explicit date (always do this before scheduling):
    .\scripts\run_completed_shift.ps1 -Date 2026-05-22

    # Extended Paytm wait for slow report generation:
    .\scripts\run_completed_shift.ps1 -Date 2026-05-22 -PaytmWaitSeconds 1800
#>
[CmdletBinding()]
param(
    [string]$Date             = "",
    [int]   $PaytmWaitSeconds = 900,
    [string]$PythonExe        = "C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe"
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

# -- Determine op_date --------------------------------------------------------
if ($Date -ne "") {
    try {
        $null = [datetime]::ParseExact($Date, "yyyy-MM-dd", $null)
        $OpDate = $Date
    } catch {
        Write-Host "[ERROR] -Date '$Date' is not a valid YYYY-MM-DD date." -ForegroundColor Red
        exit 1
    }
} else {
    # Yesterday - assumes this script runs after 06:00 local time
    $OpDate = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")
}

# -- Prepare log directory + file ---------------------------------------------
$RunTs   = (Get-Date).ToString("yyyyMMdd_HHmmss")
$LogDir  = Join-Path $RepoRoot "data\logs\scheduler"
$LogFile = Join-Path $LogDir "completed_shift_${RunTs}.log"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# -- Build Python arguments ---------------------------------------------------
$ScriptPath = Join-Path $RepoRoot "scrapers\daily_scrape.py"
$ArgList = @(
    "-X", "utf8",
    $ScriptPath,
    "--completed-shift",
    "--date", $OpDate,
    "--paytm-wait-seconds", "$PaytmWaitSeconds"
)

# -- Print + log header -------------------------------------------------------
$StartedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$Header = @(
    "======================================================="
    "  Pumpvision - completed-shift scrape"
    "======================================================="
    "  op_date    : $OpDate"
    "  mode       : --completed-shift"
    "  paytm wait : ${PaytmWaitSeconds}s"
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

# Display stdout and append to log
if ($outLines) {
    $outLines | Out-Host
    $outLines | Add-Content -Path $LogFile -Encoding utf8
}

# Display stderr (if any) and append to log
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
    "  op_date    : $OpDate"
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
