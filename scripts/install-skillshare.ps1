[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$versions = Get-Content -LiteralPath (Join-Path $repoRoot 'versions.yml') -Raw
if ($versions -notmatch '(?m)^skillshare_cli: "([^"]+)"$') { throw 'versions.yml is missing skillshare_cli.' }
$version = $Matches[1].TrimStart('v')
if ($versions -notmatch '(?m)^skillshare_windows_amd64_sha256: "([a-f0-9]{64})"$') { throw 'versions.yml is missing the Windows Skillshare checksum.' }
$expectedHash = $Matches[1]
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'This installer currently supports Windows AMD64 only.' }

$installDir = Join-Path $env:LOCALAPPDATA 'Programs\skillshare'
$stageDir = Join-Path $env:LOCALAPPDATA "skillshare-installer\$version"
New-Item -ItemType Directory -Path $installDir, $stageDir -Force | Out-Null
$archive = Join-Path $stageDir 'skillshare.zip'
$url = "https://github.com/runkids/skillshare/releases/download/v$version/skillshare_${version}_windows_amd64.zip"
Invoke-WebRequest -Uri $url -OutFile $archive -UseBasicParsing

$actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) { throw "Skillshare archive checksum mismatch: expected $expectedHash, got $actualHash." }

Expand-Archive -LiteralPath $archive -DestinationPath $stageDir -Force
$binary = Join-Path $stageDir 'skillshare.exe'
if (-not (Test-Path -LiteralPath $binary)) { throw 'Skillshare archive did not contain skillshare.exe.' }
Copy-Item -LiteralPath $binary -Destination (Join-Path $installDir 'skillshare.exe') -Force
& (Join-Path $installDir 'skillshare.exe') version
Write-Host "Installed verified Skillshare $version at $installDir"
