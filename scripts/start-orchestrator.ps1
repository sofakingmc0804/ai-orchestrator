$ErrorActionPreference = "Stop"

$Port = 8765
$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$env:ORCHESTRATOR_HOME = Join-Path $RepoRoot ".runtime\orchestrator"
$env:AI_RESOURCE_GOVERNOR_HOME = Join-Path $RepoRoot ".runtime\ai-resource-governor"
$LogDir = Join-Path $env:ORCHESTRATOR_HOME "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Existing) {
  Write-Output "already_running pid=$($Existing.OwningProcess)"
  exit 0
}

$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Out = Join-Path $LogDir "server-$Stamp.out.log"
$Err = Join-Path $LogDir "server-$Stamp.err.log"
Start-Process -FilePath python -ArgumentList "-B","-m","orchestrator.ui.simple_main" -WorkingDirectory $RepoRoot -WindowStyle Hidden -RedirectStandardOutput $Out -RedirectStandardError $Err | Out-Null
$Listener = $null
for ($i = 0; $i -lt 20; $i++) {
  Start-Sleep -Seconds 1
  $Listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($Listener) {
    break
  }
}
if (-not $Listener) {
  throw "orchestrator failed to bind port $Port"
}
Write-Output "started pid=$($Listener.OwningProcess)"
