param(
  [string]$Home
)

$ErrorActionPreference = "Stop"
$ArgsList = @("-m", "orchestrator.cli.main")
if ($Home) {
  $ArgsList += @("--home", $Home)
}
$ArgsList += @("acceptance-battery")

python @ArgsList
