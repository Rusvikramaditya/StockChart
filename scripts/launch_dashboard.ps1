#Requires -Version 5.1
<#
.SYNOPSIS
    Start the NSE Pattern Finder web control dashboard and open it in browser.

.DESCRIPTION
    Launches scripts/scanner_control_server.py on http://localhost:8765,
    waits until the port is accepting connections, opens the default browser
    to the dashboard URL, and keeps the console window open so the user can
    see live server logs and Ctrl+C to stop it.

    Pure ASCII output for Windows PowerShell 5.1 compatibility.
#>
param(
    [int]$Port = 8765,
    [string]$BindHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "NSE Pattern Finder Dashboard"
try { $Host.UI.RawUI.BackgroundColor = "Black"; Clear-Host } catch {}

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$ServerPath = Join-Path $RepoRoot "scripts\scanner_control_server.py"
$Url        = "http://${BindHost}:${Port}"

if (-not (Test-Path $ServerPath)) {
    Write-Host "  [FAIL] Could not find scanner_control_server.py at:" -ForegroundColor Red
    Write-Host "         $ServerPath" -ForegroundColor DarkGray
    Read-Host "  Press Enter to close" | Out-Null
    exit 1
}

Write-Host ""
Write-Host "  +----------------------------------------------------------+" -ForegroundColor DarkYellow
Write-Host "  |    NSE PATTERN FINDER  |  Web Dashboard                  |" -ForegroundColor Yellow
Write-Host "  +----------------------------------------------------------+" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  URL    " -ForegroundColor DarkGray -NoNewline; Write-Host $Url -ForegroundColor Cyan
Write-Host "  Repo   " -ForegroundColor DarkGray -NoNewline; Write-Host $RepoRoot -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Starting control server. Browser opens automatically when ready." -ForegroundColor DarkGray
Write-Host "  Ctrl+C in this window stops the server." -ForegroundColor DarkGray
Write-Host "  ----------------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""

Set-Location $RepoRoot

# Start the server in the background; capture its output to a temp log so we
# can mirror it to this console after the browser is open.
$LogDir = Join-Path $RepoRoot "output\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Stamp     = Get-Date -Format "yyyyMMdd_HHmmss"
$ServerLog = Join-Path $LogDir "control_server_$Stamp.log"

$ServerArgs = @("`"$ServerPath`"", "--host", $BindHost, "--port", [string]$Port)
$Proc = Start-Process -FilePath "python" -ArgumentList $ServerArgs `
    -WorkingDirectory $RepoRoot `
    -RedirectStandardOutput $ServerLog `
    -RedirectStandardError ($ServerLog + ".err") `
    -PassThru -WindowStyle Hidden

if (-not $Proc) {
    Write-Host "  [FAIL] Could not start python process." -ForegroundColor Red
    Read-Host "  Press Enter to close" | Out-Null
    exit 1
}

# Wait until the port accepts connections (max 20 seconds).
$Ready    = $false
$Deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $Deadline) {
    if ($Proc.HasExited) { break }
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $task   = $client.ConnectAsync($BindHost, $Port)
        if ($task.Wait(500) -and $client.Connected) {
            $client.Close()
            $Ready = $true
            break
        }
        $client.Close()
    } catch { }
    Start-Sleep -Milliseconds 400
}

if (-not $Ready) {
    Write-Host "  [FAIL] Server did not start on $Url within 20s." -ForegroundColor Red
    if (Test-Path $ServerLog)         { Write-Host "  Log: $ServerLog"         -ForegroundColor DarkGray }
    if (Test-Path ($ServerLog+".err")) { Write-Host "  Err: $($ServerLog).err" -ForegroundColor DarkGray }
    if (-not $Proc.HasExited) { try { $Proc.Kill() } catch {} }
    Read-Host "  Press Enter to close" | Out-Null
    exit 1
}

Write-Host "  [OK] Server up. Opening browser..." -ForegroundColor Green
Start-Process $Url
Write-Host ""
Write-Host "  Server PID: $($Proc.Id)" -ForegroundColor DarkGray
Write-Host "  Server log: $ServerLog" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  ----------------------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Streaming server log. Press Ctrl+C to stop the server." -ForegroundColor Yellow
Write-Host "  ----------------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""

# Stream the log live to this console. Loop until the server process exits or
# the user presses Ctrl+C (which Get-Content -Wait honors).
try {
    Get-Content -Path $ServerLog -Wait -Tail 0 | ForEach-Object {
        $line = [string]$_
        if     ($line -match "(?i)error|fail|exception") { Write-Host "  $line" -ForegroundColor Red }
        elseif ($line -match "(?i)warn")                 { Write-Host "  $line" -ForegroundColor Yellow }
        else                                             { Write-Host "  $line" -ForegroundColor Gray }
    }
} finally {
    if (-not $Proc.HasExited) {
        Write-Host ""
        Write-Host "  Stopping server (PID $($Proc.Id))..." -ForegroundColor DarkGray
        try { $Proc.Kill() } catch {}
    }
    Write-Host "  Server stopped." -ForegroundColor DarkGray
}
