#Requires -Version 5.1
param(
    [int]$Days = 60,
    [int]$MaxDays = 365
)

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "NSE Past Suggestions"
try { $Host.UI.RawUI.BackgroundColor = "Black"; Clear-Host } catch {}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$OutputDir = Join-Path $RepoRoot "output"
$OutputPath = Join-Path $OutputDir "past_suggestions_dashboard.html"
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host ""
Write-Host "  +----------------------------------------------------------+" -ForegroundColor DarkYellow
Write-Host "  |                                                          |" -ForegroundColor DarkYellow
Write-Host "  |    " -ForegroundColor DarkYellow -NoNewline
Write-Host ">>> PAST SUGGESTIONS PERFORMANCE <<<" -ForegroundColor Yellow -NoNewline
Write-Host "             |" -ForegroundColor DarkYellow
Write-Host "  |                                                          |" -ForegroundColor DarkYellow
Write-Host "  +----------------------------------------------------------+" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  Tracks MEDIUM, HIGH, and HIGHEST cards from saved reports." -ForegroundColor Gray
Write-Host "  Days      " -ForegroundColor DarkGray -NoNewline; Write-Host $Days -ForegroundColor Cyan
Write-Host "  Max days  " -ForegroundColor DarkGray -NoNewline; Write-Host $MaxDays -ForegroundColor Cyan
Write-Host ""

Set-Location $RepoRoot

$ArgsList = @(
    "scripts\build_past_recommendations_dashboard.py",
    "--days", [string]$Days,
    "--max-days", [string]$MaxDays,
    "--output", $OutputPath
)

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & python @ArgsList | ForEach-Object {
        Write-Host "  $_" -ForegroundColor Gray
    }
    $ExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}
$sw.Stop()

Write-Host ""
if ($ExitCode -eq 0 -and (Test-Path $OutputPath)) {
    Write-Host "  [OK] Past suggestions report ready" -ForegroundColor Green
    Write-Host "  Report  " -ForegroundColor DarkGray -NoNewline; Write-Host $OutputPath -ForegroundColor Cyan
    Write-Host "  Time    " -ForegroundColor DarkGray -NoNewline; Write-Host $sw.Elapsed.ToString("mm\:ss") -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  Opening report in browser..." -ForegroundColor Yellow
    Start-Process $OutputPath
} else {
    Write-Host "  [FAIL] Report build failed" -ForegroundColor Red
    Write-Host "  Exit code $ExitCode" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  Press Enter to close." -ForegroundColor DarkGray
Read-Host | Out-Null
exit $ExitCode
