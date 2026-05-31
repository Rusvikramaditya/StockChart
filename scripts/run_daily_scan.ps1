param(
    [string]$Universe = "nifty500",
    [int]$Workers = 8,
    [string]$PythonExe = "python",
    [switch]$SkipFetch,
    [switch]$NoTelegram
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$OutputDir = Join-Path $RepoRoot "output"
$LogDir = Join-Path $OutputDir "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$DashboardPath = Join-Path $OutputDir "scheduled_scan_$Stamp.html"
$LogPath = Join-Path $LogDir "scheduled_scan_$Stamp.log"

Set-Location $RepoRoot

$ScannerArgs = @(
    "scanner.py",
    "--universe", $Universe,
    "--workers", [string]$Workers,
    "--output", $DashboardPath
)

if ($SkipFetch) {
    $ScannerArgs += "--skip-fetch"
}

if ($NoTelegram) {
    $ScannerArgs += "--no-telegram"
}

"[$(Get-Date -Format o)] Starting Pattern Finder scan" | Out-File -FilePath $LogPath -Encoding utf8
"RepoRoot: $RepoRoot" | Out-File -FilePath $LogPath -Encoding utf8 -Append
"Command: $PythonExe $($ScannerArgs -join ' ')" | Out-File -FilePath $LogPath -Encoding utf8 -Append

# PowerShell 5.1 can turn native stderr into NativeCommandError when ErrorActionPreference is Stop.
$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $PythonExe @ScannerArgs *>&1 | Tee-Object -FilePath $LogPath -Append
    $ExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $PreviousErrorActionPreference
}

"[$(Get-Date -Format o)] Scanner exit code: $ExitCode" | Out-File -FilePath $LogPath -Encoding utf8 -Append

if ($ExitCode -ne 0) {
    Write-Error "Pattern Finder scan failed with exit code $ExitCode. Log: $LogPath"
}

Write-Host "Dashboard: $DashboardPath"
Write-Host "Log: $LogPath"
exit $ExitCode
