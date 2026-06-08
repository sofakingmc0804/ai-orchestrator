$ErrorActionPreference = "Stop"
$governorBin = "C:\Users\Couch\.ai-resource-governor\bin"
$profilePath = $profile.CurrentUserAllHosts
$profileDir = Split-Path -Parent $profilePath
New-Item -ItemType Directory -Path $profileDir -Force | Out-Null

$start = "# BEGIN AI Resource Governor PATH"
$end = "# END AI Resource Governor PATH"
$block = @"
$start
if (`$env:Path -notlike "*$governorBin*") {
  `$env:Path = "$governorBin;`$env:Path"
}
$end
"@

if (Test-Path -Path $profilePath) {
  $existing = Get-Content -Path $profilePath -Raw
  $backup = "$profilePath.ai-resource-governor.$((Get-Date).ToString('yyyyMMddHHmmss')).bak"
  Copy-Item -Path $profilePath -Destination $backup -Force
  $pattern = "(?s)# BEGIN AI Resource Governor PATH.*?# END AI Resource Governor PATH\r?\n?"
  $newContent = [regex]::Replace($existing, $pattern, "")
  Set-Content -Path $profilePath -Value ($newContent.TrimEnd() + "`r`n`r`n" + $block) -Encoding UTF8
} else {
  Set-Content -Path $profilePath -Value $block -Encoding UTF8
}

[Environment]::SetEnvironmentVariable(
  "Path",
  "$governorBin;" + [Environment]::GetEnvironmentVariable("Path", "User"),
  "User"
)

Write-Output "installed ai-resource-governor shims at $governorBin"
