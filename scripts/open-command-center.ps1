# Opens the Command Center dashboard once the orchestrator is healthy.
# Registered as the "AI Orchestrator Dashboard" logon task. Never run by hand.
$ErrorActionPreference = "SilentlyContinue"
$base = "http://127.0.0.1:8765"
$url  = "$base/static/command-center.html"

# Wait for the always-on orchestrator to answer (up to ~2 minutes after logon).
for ($i = 0; $i -lt 60; $i++) {
  try { Invoke-RestMethod -Uri "$base/api/dashboard-status" -TimeoutSec 4 | Out-Null; break }
  catch { Start-Sleep -Seconds 2 }
}

function Find-Browser($cmd, $paths) {
  $p = (Get-Command $cmd -ErrorAction SilentlyContinue).Source
  if ($p) { return $p }
  foreach ($cand in $paths) { if ($cand -and (Test-Path $cand)) { return $cand } }
  return $null
}
$chrome = Find-Browser "chrome.exe" @(
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
  "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
  "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe")
$edge = Find-Browser "msedge.exe" @(
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe")

if ($chrome)   { Start-Process $chrome -ArgumentList "--app=$url" }
elseif ($edge) { Start-Process $edge   -ArgumentList "--app=$url" }
else           { Start-Process $url }
