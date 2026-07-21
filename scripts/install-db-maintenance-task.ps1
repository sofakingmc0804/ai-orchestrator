[CmdletBinding()]
<#
.SYNOPSIS
  Registers a Windows Scheduled Task that runs scripts/db_maintenance.py off-hours
  (PRAGMA integrity_check + VACUUM INTO a dated, compacted backup of state.sqlite).

  Decoupled from the orchestrator server on purpose: DB maintenance should run even
  if the server is down. VACUUM INTO only reads the source, so it is safe while the
  server is live.
#>
param(
  [string]$Time = "03:30",
  [ValidateSet("Daily", "Weekly")][string]$Frequency = "Daily",
  [string]$DayOfWeek = "Sunday",
  [string]$TaskName = "AI Orchestrator DB Maintenance"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Script = Join-Path $RepoRoot "scripts\db_maintenance.py"
$Pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
$NoConsoleLauncher = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "no-console-task-launcher.pyw")
$Python = (Get-Command python.exe -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $Script)) {
  throw "Maintenance script not found: $Script"
}

$ActionArgs = "`"$NoConsoleLauncher`" -- `"$Python`" -B `"$Script`""
$Action = New-ScheduledTaskAction -Execute $Pythonw -Argument $ActionArgs -WorkingDirectory $RepoRoot
if ($Frequency -eq "Weekly") {
  $Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $Time
}
else {
  $Trigger = New-ScheduledTaskTrigger -Daily -At $Time
}
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
Write-Output "Registered '$TaskName': $Frequency at $Time"
Write-Output "  Command: `"$Pythonw`" $ActionArgs"
Write-Output "  Working dir: $RepoRoot"
