param(
  [Parameter(Mandatory=$true)]
  [string]$Selection,

  [ValidateSet("file", "folder", "url", "text")]
  [string]$Kind = "text"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
Set-Location -LiteralPath $RepoRoot
python -m orchestrator.cli.main add-selection $Selection --kind $Kind --source command-palette | Out-Null
