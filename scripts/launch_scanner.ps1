#Requires -Version 5.1
param(
    [string]$Universe = "nifty500",
    [int]$Workers     = 8,
    [ValidateSet("daily", "weekly", "all")]
    [string]$ScanTimeframe = "daily",
    [switch]$SkipFetch,
    [switch]$NoTelegram
)

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "NSE Pattern Finder"
try { $Host.UI.RawUI.BackgroundColor = "Black"; Clear-Host } catch {}

$RepoRoot  = Split-Path -Parent $PSScriptRoot
$OutputDir = Join-Path $RepoRoot "output"
$LogDir    = Join-Path $OutputDir "logs"
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir    | Out-Null

$Stamp         = Get-Date -Format "yyyyMMdd_HHmmss"
$TimeframeSuffix = if ($ScanTimeframe -eq "daily") { "" } else { "_$ScanTimeframe" }
$DashboardPath = Join-Path $OutputDir "scan_$Stamp$TimeframeSuffix.html"
$LogPath       = Join-Path $LogDir    "scan_$Stamp.log"

# Banner (pure ASCII for PowerShell 5.1 compatibility)
Write-Host ""
Write-Host "  +----------------------------------------------------------+" -ForegroundColor DarkYellow
Write-Host "  |                                                          |" -ForegroundColor DarkYellow
Write-Host "  |    " -ForegroundColor DarkYellow -NoNewline
Write-Host ">>> NSE PATTERN FINDER  |  Intelligence Engine <<<" -ForegroundColor Yellow -NoNewline
Write-Host "   |" -ForegroundColor DarkYellow
Write-Host "  |                                                          |" -ForegroundColor DarkYellow
Write-Host "  +----------------------------------------------------------+" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  Universe  " -ForegroundColor DarkGray -NoNewline; Write-Host $Universe -ForegroundColor Cyan
Write-Host "  Workers   " -ForegroundColor DarkGray -NoNewline; Write-Host $Workers  -ForegroundColor Cyan
Write-Host "  Timeframe " -ForegroundColor DarkGray -NoNewline; Write-Host $ScanTimeframe -ForegroundColor Cyan
Write-Host "  Date      " -ForegroundColor DarkGray -NoNewline; Write-Host (Get-Date -Format "yyyy-MM-dd  HH:mm:ss") -ForegroundColor Cyan
Write-Host ""
Write-Host "  ----------------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""

# Run scanner
$ScanArgs = @(
    "scanner.py",
    "--universe", $Universe,
    "--workers", [string]$Workers,
    "--scan-timeframe", $ScanTimeframe,
    "--output", $DashboardPath
)
if ($SkipFetch)  { $ScanArgs += "--skip-fetch"   }
if ($NoTelegram) { $ScanArgs += "--no-telegram"  }

Set-Location $RepoRoot

$sw = [System.Diagnostics.Stopwatch]::StartNew()

# PowerShell 5.1 can turn native stderr into NativeCommandError when ErrorActionPreference is Stop.
$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & python @ScanArgs *>&1 | Tee-Object -FilePath $LogPath | ForEach-Object {
        $line = [string]$_
        if     ($line -match "(?i)error|fail|critical|exception") { Write-Host "  $line" -ForegroundColor Red }
        elseif ($line -match "(?i)warn")                          { Write-Host "  $line" -ForegroundColor Yellow }
        elseif ($line -match "(?i)done|complete|success|passed|hit|found|\bOK\b") { Write-Host "  $line" -ForegroundColor Green }
        elseif ($line -match "^\s*\[")                            { Write-Host "  $line" -ForegroundColor DarkCyan }
        else                                                      { Write-Host "  $line" -ForegroundColor Gray }
    }
    $ExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}
$sw.Stop()
$Elapsed = $sw.Elapsed.ToString("mm\:ss")

Write-Host ""
Write-Host "  ----------------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""

# Result
if ($ExitCode -eq 0 -and (Test-Path $DashboardPath)) {
    Write-Host "  " -NoNewline
    Write-Host "[OK] Scan complete" -ForegroundColor Green -NoNewline
    Write-Host "  ($Elapsed)" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Dashboard  " -ForegroundColor DarkGray -NoNewline; Write-Host $DashboardPath -ForegroundColor Cyan
    Write-Host "  Log        " -ForegroundColor DarkGray -NoNewline; Write-Host $LogPath -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Opening dashboard in browser..." -ForegroundColor Yellow
    Start-Process $DashboardPath
    Write-Host ""
    Write-Host "  ----------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host "  Press Enter to close." -ForegroundColor DarkGray
    Read-Host | Out-Null
} else {
    Write-Host "  " -NoNewline
    Write-Host "[FAIL] Scan FAILED" -ForegroundColor Red -NoNewline
    Write-Host "  (exit $ExitCode, elapsed $Elapsed)" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Log  " -ForegroundColor DarkGray -NoNewline; Write-Host $LogPath -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  ----------------------------------------------------------" -ForegroundColor DarkGray
    Write-Host "  Press Enter to close." -ForegroundColor DarkGray
    Read-Host | Out-Null
    exit $ExitCode
}
