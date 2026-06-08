$ErrorActionPreference = "Stop"

$TaskName = "AI Orchestrator"
$Launcher = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "start-orchestrator.ps1")
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Launcher`""
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Starts AI Orchestrator on logon without opening a visible window." -Force | Out-Null
Write-Output "installed task=$TaskName launcher=$Launcher"
