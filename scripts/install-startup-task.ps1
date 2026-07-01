$ErrorActionPreference = "Stop"

$TaskName = "AI Orchestrator"
$Launcher = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "start-orchestrator.ps1")
$Pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
$NoConsoleLauncher = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "no-console-task-launcher.pyw")
$ActionArgs = "`"$NoConsoleLauncher`" -- powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -File `"$Launcher`" -Watchdog"
$Action = New-ScheduledTaskAction -Execute $Pythonw -Argument $ActionArgs -WorkingDirectory $PSScriptRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -Hidden
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Starts AI Orchestrator on logon without opening a visible window." -Force | Out-Null
Write-Output "installed task=$TaskName launcher=$Launcher"
