$ErrorActionPreference = "Stop"
$root = "C:\Users\Couch\dev\ai-orchestrator\.runtime\ai-resource-governor"
$python = "C:\Python313\python.exe"
$actual = "C:\Users\Couch\AppData\Roaming\Python\Python313\Scripts\hermes.exe"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$orchestratorHome = Join-Path $repoRoot ".runtime\orchestrator"
$env:PYTHONPATH = "$repoRoot;$root"
$env:ORCHESTRATOR_HOME = $orchestratorHome

function Get-HermesPromptText {
  param([object[]]$Argv)
  if (-not $Argv -or $Argv.Count -eq 0) { return "" }
  for ($i = 0; $i -lt $Argv.Count; $i++) {
    $arg = [string]$Argv[$i]
    if (@("-z", "--oneshot", "--prompt", "-p") -contains $arg -and ($i + 1) -lt $Argv.Count) {
      return [string]$Argv[$i + 1]
    }
    if ($arg.StartsWith("--prompt=")) {
      return $arg.Substring("--prompt=".Length)
    }
  }
  if (@("ask", "chat", "run", "prompt", "complete") -contains [string]$Argv[0]) {
    if ($Argv.Count -le 1) { return "" }
    return (($Argv | Select-Object -Skip 1) -join " ")
  }
  return ""
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

function Get-HermesKnownJobClass {
  param([object[]]$Argv)
  if (-not $Argv -or $Argv.Count -eq 0) { return $null }
  for ($i = 0; $i -lt $Argv.Count; $i++) {
    $arg = [string]$Argv[$i]
    if ($arg -eq "--orchestrator-job-class" -and ($i + 1) -lt $Argv.Count) {
      return [string]$Argv[$i + 1]
    }
    if ($arg.StartsWith("--orchestrator-job-class=")) {
      return $arg.Substring("--orchestrator-job-class=".Length)
    }
  }
  $mode = ([string]$Argv[0]).Trim().ToLowerInvariant()
  if (@("ask", "chat", "prompt", "complete") -contains $mode) {
    return "routing_triage"
  }
  if ($mode -eq "run") {
    return "repo_coding"
  }
  return $null
}

function Remove-HermesOrchestratorArgs {
  param([object[]]$Argv)
  $clean = @()
  for ($i = 0; $i -lt $Argv.Count; $i++) {
    $arg = [string]$Argv[$i]
    if ($arg -eq "--orchestrator-job-class") {
      $i++
      continue
    }
    if ($arg.StartsWith("--orchestrator-job-class=")) {
      continue
    }
    $clean += $Argv[$i]
  }
  return $clean
}

$receipt = @{
  created_at = (Get-Date).ToUniversalTime().ToString("o")
  shim = "hermes"
  command = @($args)
  route_policy = "ai-orchestrator"
  default_lane = "ollama-local-first; copilot-account-only-when-cost-safe"
} | ConvertTo-Json -Compress
Add-Content -Path (Join-Path $root "receipts\command-invocations.jsonl") -Value $receipt -Encoding UTF8

$providerBilling = @{
  "custom" = "local_resource"
  "ollama" = "local_resource"
  "lmstudio" = "local_resource"
  "copilot" = "subscription_quota"
  "copilot-acp" = "subscription_quota"
  "openai-codex" = "subscription_unlimited"
  "google-gemini-cli" = "subscription_quota"
  "qwen-oauth" = "subscription_quota"
  "minimax-oauth" = "subscription_quota"
  "xai-oauth" = "subscription_quota"
  "nous" = "subscription_quota"
  "ollama-cloud" = "subscription_quota"
  "anthropic" = "metered_extra_cost"
  "openai-api" = "metered_extra_cost"
  "openrouter" = "metered_extra_cost"
  "gemini" = "metered_extra_cost"
  "xai" = "metered_extra_cost"
  "zai" = "metered_extra_cost"
  "kimi-coding" = "metered_extra_cost"
  "kimi-coding-cn" = "metered_extra_cost"
  "minimax" = "metered_extra_cost"
  "minimax-cn" = "metered_extra_cost"
  "deepseek" = "metered_extra_cost"
  "nvidia" = "metered_extra_cost"
  "stepfun" = "metered_extra_cost"
  "alibaba" = "metered_extra_cost"
  "bedrock" = "metered_extra_cost"
  "azure-foundry" = "metered_extra_cost"
  "huggingface" = "metered_extra_cost"
}
$blockedBilling = @("metered_extra_cost", "unknown_cost")
$explicitProvider = $null
for ($i = 0; $i -lt $args.Count; $i++) {
  $arg = [string]$args[$i]
  if ($arg -eq "--provider" -and ($i + 1) -lt $args.Count) {
    $explicitProvider = ([string]$args[$i + 1]).Trim().ToLowerInvariant()
  } elseif ($arg.StartsWith("--provider=")) {
    $explicitProvider = $arg.Substring("--provider=".Length).Trim().ToLowerInvariant()
  }
}
if (-not $explicitProvider -and $env:HERMES_INFERENCE_PROVIDER) {
  $explicitProvider = $env:HERMES_INFERENCE_PROVIDER.Trim().ToLowerInvariant()
}
if ($explicitProvider) {
  $billing = $providerBilling[$explicitProvider]
  if (-not $billing) { $billing = "unknown_cost" }
  if ($blockedBilling -contains $billing) {
    $deny = @{
      created_at = (Get-Date).ToUniversalTime().ToString("o")
      shim = "hermes"
      denied_provider = $explicitProvider
      billing_class = $billing
      reason = "AI Resource Governor blocks Hermes provider overrides that are metered or unproved."
    } | ConvertTo-Json -Compress
    Add-Content -Path (Join-Path $root "receipts\blocked-command-invocations.jsonl") -Value $deny -Encoding UTF8
    Write-Error "Hermes provider '$explicitProvider' is blocked by AI Resource Governor ($billing). Use local Ollama/custom or a proved subscription-backed provider."
    exit 42
  }
}

if ($args.Count -gt 0 -and (@("ask","chat","run","prompt","complete") -contains [string]$args[0] -or @($args) -contains "-z" -or @($args) -contains "--oneshot")) {
  $promptText = Get-HermesPromptText -Argv @($args)
  if (-not $promptText) {
    Write-Error "Hermes prompt command could not be routed because no prompt text was found."
    exit 43
  }
  $knownJobClass = Get-HermesKnownJobClass -Argv @($args)
  $args = @(Remove-HermesOrchestratorArgs -Argv @($args))
  $argvJson = @($args) | ConvertTo-Json -Compress
  $argvB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($argvJson))
  $routeArgs = @("-m", "orchestrator.cli.main", "hermes-route", "--text", $promptText, "--argv-b64", $argvB64)
  if ($knownJobClass) {
    $routeArgs += @("--job-class", $knownJobClass)
  }
  $brainJson = & $python @routeArgs
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  $brainRoute = $brainJson | ConvertFrom-Json
  if ($brainRoute.state -ne "produced") {
    Write-Error "Hermes prompt blocked because the orchestrator brain did not produce a route. Receipt: $($brainRoute.receipt_path)"
    exit 43
  }
  $routeReceipt = @{
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    shim = "hermes"
    route_receipt_path = $brainRoute.receipt_path
    route_state = $brainRoute.state
    selected = $brainRoute.selected
  } | ConvertTo-Json -Compress
  Add-Content -Path (Join-Path $root "receipts\brain-route-invocations.jsonl") -Value $routeReceipt -Encoding UTF8
  if (-not (Test-HermesArg -Argv @($args) -Names @("--provider"))) {
    $chosenProvider = [string]$brainRoute.selected.hermes_provider
    if ($chosenProvider) {
      $args = @($args) + @("--provider", $chosenProvider)
    }
  }
  if (-not (Test-HermesArg -Argv @($args) -Names @("-m", "--model"))) {
    $chosenModel = [string]$brainRoute.selected.model_id
    if ($chosenModel) {
      $args = @($args) + @("-m", $chosenModel)
    }
  }
}

& $actual @args
exit $LASTEXITCODE
