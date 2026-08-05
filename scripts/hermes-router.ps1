$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
  $python = "C:\Python313\python.exe"
}
$actual = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\hermes.exe"
$env:HERMES_HOME = Join-Path $env:LOCALAPPDATA "hermes"
$env:PYTHONPATH = $repoRoot
$env:ORCHESTRATOR_HOME = Join-Path $repoRoot ".runtime\orchestrator"
# Provider lanes supported by the current Hermes runtime. OpenRouter is
# intentionally free-model-only in this no-extra-cost launcher.
$supportedHermesProviders = @("custom", "ollama-cloud", "openai-codex", "nous", "openrouter", "opencode-zen")

function Get-HermesPromptText {
  param([object[]]$Argv)
  for ($i = 0; $i -lt $Argv.Count; $i++) {
    $arg = [string]$Argv[$i]
    if (@("-z", "--oneshot", "--prompt", "-p") -contains $arg -and ($i + 1) -lt $Argv.Count) {
      return [string]$Argv[$i + 1]
    }
    if ($arg.StartsWith("--prompt=")) { return $arg.Substring("--prompt=".Length) }
  }
  if ($Argv.Count -gt 1 -and @("ask", "chat", "run", "prompt", "complete") -contains ([string]$Argv[0]).ToLowerInvariant()) {
    return (($Argv | Select-Object -Skip 1) -join " ")
  }
  return ""
}

function Get-HermesJobClass {
  param([object[]]$Argv)
  for ($i = 0; $i -lt $Argv.Count; $i++) {
    $arg = [string]$Argv[$i]
    if ($arg -eq "--orchestrator-job-class" -and ($i + 1) -lt $Argv.Count) { return [string]$Argv[$i + 1] }
    if ($arg.StartsWith("--orchestrator-job-class=")) { return $arg.Substring("--orchestrator-job-class=".Length) }
  }
  $mode = ([string]$Argv[0]).Trim().ToLowerInvariant()
  if (@("ask", "chat", "prompt", "complete") -contains $mode) { return "routing_triage" }
  if ($mode -eq "run") { return "repo_coding" }
  return $null
}

function Get-HermesProjectId {
  param([object[]]$Argv)
  return Get-HermesArgValue -Argv $Argv -Names @("--orchestrator-project", "--project-id")
}

function Test-HermesArg {
  param([object[]]$Argv, [string[]]$Names)
  foreach ($arg in $Argv) {
    $text = [string]$arg
    foreach ($name in $Names) {
      if ($text -eq $name -or $text.StartsWith("$name=")) { return $true }
    }
  }
  return $false
}

function Get-HermesArgValue {
  param([object[]]$Argv, [string[]]$Names)
  for ($i = 0; $i -lt $Argv.Count; $i++) {
    $text = [string]$Argv[$i]
    foreach ($name in $Names) {
      if ($text -eq $name -and ($i + 1) -lt $Argv.Count) { return [string]$Argv[$i + 1] }
      if ($text.StartsWith("$name=")) { return $text.Substring($name.Length + 1) }
    }
  }
  return ""
}

function Remove-OrchestratorArgs {
  param([object[]]$Argv)
  $clean = @()
  for ($i = 0; $i -lt $Argv.Count; $i++) {
    $arg = [string]$Argv[$i]
    if ($arg -eq "--orchestrator-job-class") { $i++; continue }
    if ($arg.StartsWith("--orchestrator-job-class=")) { continue }
    if ($arg -eq "--orchestrator-project" -or $arg -eq "--project-id") { $i++; continue }
    if ($arg.StartsWith("--orchestrator-project=") -or $arg.StartsWith("--project-id=")) { continue }
    $clean += $Argv[$i]
  }
  return $clean
}

function ConvertTo-ArgvB64 {
  param([object[]]$Argv)
  $json = ConvertTo-Json -InputObject @($Argv) -Compress
  return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
}

$forwardArgs = @($args)
$explicitProvider = Get-HermesArgValue -Argv $forwardArgs -Names @("--provider")
$explicitModel = Get-HermesArgValue -Argv $forwardArgs -Names @("-m", "--model")
if ($explicitProvider -and $explicitProvider.ToLowerInvariant() -notin $supportedHermesProviders) {
  Write-Error "Hermes provider '$explicitProvider' is a paid direct-API lane and is blocked until explicit billing authorization is recorded." -ErrorAction Continue
  exit 42
}
if ($explicitProvider -eq "openrouter" -and (-not $explicitModel -or -not $explicitModel.EndsWith(":free", [StringComparison]::OrdinalIgnoreCase))) {
  Write-Error "OpenRouter is restricted to an explicitly free model in the no-extra-cost Hermes lane." -ErrorAction Continue
  exit 42
}

$promptText = Get-HermesPromptText -Argv $forwardArgs
if ($promptText) {
  $knownJobClass = Get-HermesJobClass -Argv $forwardArgs
  $projectId = Get-HermesProjectId -Argv $forwardArgs
  $explicitJobClass = Get-HermesArgValue -Argv $forwardArgs -Names @("--orchestrator-job-class")
  $forwardArgs = @(Remove-OrchestratorArgs -Argv $forwardArgs)
  $routeArgs = @(
    "-m", "orchestrator.cli.main", "hermes-route",
    "--text-b64", ([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($promptText))),
    "--argv-b64", (ConvertTo-ArgvB64 -Argv $forwardArgs)
  )
  if ($knownJobClass) { $routeArgs += @("--job-class", $knownJobClass) }
  if ($projectId) { $routeArgs += @("--project-id", $projectId) }
  $brainJson = & $python @routeArgs
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  $brainRoute = $brainJson | ConvertFrom-Json
  if ($brainRoute.state -ne "produced") {
    Write-Error "Hermes prompt blocked because the orchestrator did not produce a route. Receipt: $($brainRoute.receipt_path)" -ErrorAction Continue
    exit 43
  }
  $selected = $brainRoute.selected
  if ($selected.lane_state -eq "blocked_metered") {
    Write-Error "Hermes prompt blocked because the selected lane is metered. Reason: $($selected.lane_reason)" -ErrorAction Continue
    exit 42
  }
  if ($selected.lane_state -eq "dispatch_only" -and $explicitJobClass -and -not $explicitProvider -and -not $explicitModel) {
    Write-Error "Hermes prompt blocked because the selected lane uses '$($selected.adapter_name)', which is not executable through Hermes. Use the orchestrator's direct adapter path or connect the provider natively in Hermes." -ErrorAction Continue
    exit 44
  }
  # Manual chat must retain the model/provider configured in Hermes Desktop.
  # Capacity injection is reserved for an explicitly classified orchestrated
  # job, which prevents a stale triage roster row from overriding GLM5.2.
  $applyRouteSelection = [bool]$explicitJobClass -and $selected.lane_state -eq "supported"
  if ($applyRouteSelection) {
    if (-not (Test-HermesArg -Argv $forwardArgs -Names @("--provider")) -and [string]$selected.hermes_provider) {
      $forwardArgs += @("--provider", [string]$selected.hermes_provider)
    }
    if (-not (Test-HermesArg -Argv $forwardArgs -Names @("-m", "--model")) -and [string]$selected.hermes_model) {
      $forwardArgs += @("-m", [string]$selected.hermes_model)
    }
  }
}

$finalArgvB64 = ConvertTo-ArgvB64 -Argv $forwardArgs
& $python -m orchestrator.governance.hermes_exec --executable $actual --argv-b64 $finalArgvB64
exit $LASTEXITCODE
