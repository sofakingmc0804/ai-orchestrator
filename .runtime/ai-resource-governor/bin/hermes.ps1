$ErrorActionPreference = "Stop"
$root = "C:\Users\Couch\.ai-resource-governor"
$python = "C:\Python313\python.exe"
$actual = "C:\Users\Couch\AppData\Roaming\Python\Python313\Scripts\hermes.exe"
$env:PYTHONPATH = $root

$receipt = @{
  created_at = (Get-Date).ToUniversalTime().ToString("o")
  shim = "hermes"
  command = @($args)
  route_policy = "ai-resource-governor"
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
  & $python (Join-Path $root "ai_governor.py") route --task-type "hermes_cli_model_call" --context-tokens 0 | Out-Null
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $actual @args
exit $LASTEXITCODE
