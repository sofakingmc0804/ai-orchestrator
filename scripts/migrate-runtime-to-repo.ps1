$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$RuntimeRoot = Join-Path $RepoRoot ".runtime"
$OrchestratorDest = Join-Path $RuntimeRoot "orchestrator"
$GovernorDest = Join-Path $RuntimeRoot "ai-resource-governor"
$SpecDest = Join-Path $RepoRoot "docs\specs"

New-Item -ItemType Directory -Force -Path $OrchestratorDest, $GovernorDest, $SpecDest | Out-Null

function Invoke-RobocopyChecked {
  param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Destination,
    [string[]]$ExcludeFiles = @()
  )

  $args = @($Source, $Destination, "/E", "/R:2", "/W:1")
  if ($ExcludeFiles.Count -gt 0) {
    $args += "/XF"
    $args += $ExcludeFiles
  }
  & robocopy @args | Out-Host
  if ($LASTEXITCODE -gt 7) {
    throw "robocopy failed source=$Source destination=$Destination exit=$LASTEXITCODE"
  }
}

Invoke-RobocopyChecked -Source (Join-Path $env:USERPROFILE ".orchestrator") -Destination $OrchestratorDest -ExcludeFiles @("state.sqlite", "state.sqlite-wal", "state.sqlite-shm")
Invoke-RobocopyChecked -Source (Join-Path $env:USERPROFILE ".ai-resource-governor") -Destination $GovernorDest -ExcludeFiles @("inventory.sqlite", "inventory.sqlite-wal", "inventory.sqlite-shm")

$SpecSource = Join-Path $env:USERPROFILE "Downloads\AI_ORCHESTRATOR_SPEC_v4.0.md"
if (Test-Path -LiteralPath $SpecSource) {
  Copy-Item -LiteralPath $SpecSource -Destination (Join-Path $SpecDest "AI_ORCHESTRATOR_SPEC_v4.0.md") -Force
}

@"
import sqlite3
from pathlib import Path

pairs = [
    (Path(r"$env:USERPROFILE") / ".orchestrator" / "state.sqlite", Path(r"$OrchestratorDest") / "state.sqlite"),
    (Path(r"$env:USERPROFILE") / ".ai-resource-governor" / "inventory.sqlite", Path(r"$GovernorDest") / "inventory.sqlite"),
]
for src, dst in pairs:
    if not src.exists():
        print(f"missing {src}")
        continue
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    source = sqlite3.connect(str(src))
    target = sqlite3.connect(str(dst))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    print(f"backed up {src} -> {dst} ({dst.stat().st_size} bytes)")
"@ | python -

Write-Output "runtime_migrated repo=$RepoRoot"
