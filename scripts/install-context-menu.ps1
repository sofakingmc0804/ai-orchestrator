$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
Set-Location -LiteralPath $RepoRoot
python -m orchestrator.ui.shell_integration.windows_registry install --repo-root $RepoRoot
