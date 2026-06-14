[CmdletBinding()]
param(
  [int]$Port = 8765,
  [switch]$Watchdog,
  [switch]$WatchdogOnce
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$env:ORCHESTRATOR_HOME = Join-Path $RepoRoot ".runtime\orchestrator"
$env:AI_RESOURCE_GOVERNOR_HOME = Join-Path $RepoRoot ".runtime\ai-resource-governor"
$env:ORCHESTRATOR_PORT = "$Port"
$LogDir = Join-Path $env:ORCHESTRATOR_HOME "logs"
$SupervisorDir = Join-Path $env:ORCHESTRATOR_HOME "supervisor"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path $SupervisorDir | Out-Null

function Write-OrchestratorStartReceipt {
  param(
    [string]$EventName,
    [string]$State,
    [int]$ProcessId,
    [bool]$Healthy,
    [string]$ErrorText = ""
  )
  $Stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
  $Path = Join-Path $SupervisorDir "$Stamp-start-$EventName.json"
  $Payload = [ordered]@{
    id = "start_$Stamp"
    event = $EventName
    state = $State
    proof_kind = "live"
    port = $Port
    pid = $ProcessId
    healthy = $Healthy
    health_url = "http://127.0.0.1:$Port/api/spec-status"
    repo_root = "$RepoRoot"
    started_at = (Get-Date).ToUniversalTime().ToString("o")
    completed_at = (Get-Date).ToUniversalTime().ToString("o")
    error = $ErrorText
    receipt_path = $Path
    restart_recovery_proved = ($EventName -eq "watchdog_restart")
  }
  $Payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $Path -Encoding UTF8
  return $Path
}

function Test-OrchestratorHealth {
  try {
    Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/spec-status" -TimeoutSec 3 | Out-Null
    return $true
  } catch {
    return $false
  }
}

function Ensure-OrchestratorRunning {
  param([bool]$RestartEvent)

  $Existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($Existing) {
    $Healthy = Test-OrchestratorHealth
    $Receipt = Write-OrchestratorStartReceipt -EventName "already_running" -State "produced" -ProcessId $Existing.OwningProcess -Healthy $Healthy
    Write-Output "already_running pid=$($Existing.OwningProcess) healthy=$Healthy receipt=$Receipt"
    if ($Healthy) {
      return
    }
    throw "orchestrator listener exists on port $Port but failed the health check"
  }

  $Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
  $Out = Join-Path $LogDir "server-$Stamp.out.log"
  $Err = Join-Path $LogDir "server-$Stamp.err.log"
  $Started = Start-Process -FilePath python -ArgumentList "-B","-m","orchestrator.main","--port","$Port" -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $Out -RedirectStandardError $Err -PassThru
  $Listener = $null
  $Healthy = $false
  for ($i = 0; $i -lt 120; $i++) {
    Start-Sleep -Seconds 1
    if ($Started.HasExited) {
      break
    }
    $Listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Listener) {
      $Healthy = Test-OrchestratorHealth
      if ($Healthy) {
        break
      }
    }
  }
  if (-not $Listener) {
    $Receipt = Write-OrchestratorStartReceipt -EventName "start_failed" -State "blocked_after_repair_attempt" -ProcessId 0 -Healthy $false -ErrorText "orchestrator failed to bind port $Port"
    throw "orchestrator failed to bind port $Port receipt=$Receipt"
  }
  $EventName = if ($RestartEvent) { "watchdog_restart" } else { "started" }
  $State = if ($Healthy) { "produced" } else { "blocked_after_repair_attempt" }
  $Receipt = Write-OrchestratorStartReceipt -EventName $EventName -State $State -ProcessId $Listener.OwningProcess -Healthy $Healthy
  Write-Output "$EventName pid=$($Listener.OwningProcess) healthy=$Healthy receipt=$Receipt"
  if (-not $Healthy) {
    throw "orchestrator started on port $Port but failed the health check"
  }
}

if ($Watchdog) {
  $DelaySeconds = 5
  while ($true) {
    try {
      Ensure-OrchestratorRunning -RestartEvent:$true
      $DelaySeconds = 30
    } catch {
      $Receipt = Write-OrchestratorStartReceipt -EventName "watchdog_error" -State "blocked_after_repair_attempt" -ProcessId 0 -Healthy $false -ErrorText "$_"
      Write-Output "watchdog_error receipt=$Receipt error=$_"
      $DelaySeconds = [Math]::Min([Math]::Max($DelaySeconds * 2, 10), 300)
    }
    Start-Sleep -Seconds $DelaySeconds
  }
}
Ensure-OrchestratorRunning -RestartEvent:$WatchdogOnce
