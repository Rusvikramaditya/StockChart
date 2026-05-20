[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$TaskName = "PatternFinderDailyScan",
    [string]$Universe = "nifty500",
    [string]$StartTime = "15:45",
    [int]$Workers = 8,
    [string]$PythonExe = "python",
    [switch]$SkipFetch,
    [switch]$NoTelegram
)

$ErrorActionPreference = "Stop"

if ($StartTime -notmatch "^\d{2}:\d{2}$") {
    throw "StartTime must be HH:mm, for example 15:45"
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RunnerPath = Join-Path $ScriptDir "run_daily_scan.ps1"
if (-not (Test-Path $RunnerPath)) {
    throw "Runner script missing: $RunnerPath"
}

function Quote-TaskArg {
    param([string]$Value)
    if ($Value -match "\s") {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

$RunnerArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", (Quote-TaskArg $RunnerPath),
    "-Universe", $Universe,
    "-Workers", [string]$Workers,
    "-PythonExe", (Quote-TaskArg $PythonExe)
)

if ($SkipFetch) {
    $RunnerArgs += "-SkipFetch"
}

if ($NoTelegram) {
    $RunnerArgs += "-NoTelegram"
}

$TaskArguments = $RunnerArgs -join " "
$TaskRun = "powershell.exe $TaskArguments"
$At = [datetime]::ParseExact($StartTime, "HH:mm", [System.Globalization.CultureInfo]::InvariantCulture)

Write-Host "Task name: $TaskName"
Write-Host "Schedule: DAILY at $StartTime"
Write-Host "Universe: $Universe"
Write-Host "Action: $TaskRun"

if ($PSCmdlet.ShouldProcess($TaskName, "Create or update Windows scheduled task")) {
    $Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $TaskArguments
    $Trigger = New-ScheduledTaskTrigger -Daily -At $At
    $Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "Runs Pattern Finder scanner daily for the selected universe." `
        -Force | Out-Null
    Write-Host "PASS: scheduled task installed"
}
