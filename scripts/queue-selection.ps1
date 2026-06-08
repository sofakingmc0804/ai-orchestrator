param(
  [Parameter(Mandatory=$true)]
  [string]$SelectedPath,

  [ValidateSet("file", "folder", "url", "text")]
  [string]$Kind = "file",

  [string]$Source = "windows-context-menu"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
Set-Location -LiteralPath $RepoRoot
python -m orchestrator.cli.main add-selection $SelectedPath --kind $Kind --source $Source | Out-Null
