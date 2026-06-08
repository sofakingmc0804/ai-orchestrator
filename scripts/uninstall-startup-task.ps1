$ErrorActionPreference = "Stop"

$TaskName = "AI Orchestrator"
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Output "removed task=$TaskName"
} else {
  Write-Output "not_installed task=$TaskName"
}
