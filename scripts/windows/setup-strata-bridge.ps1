# Run in an elevated PowerShell. The token is stored with the current Windows
# user's DPAPI protection; it is never printed. Reuse it with -ReuseStoredToken.
[CmdletBinding()]
param([switch]$ReuseStoredToken)

$ErrorActionPreference = 'Stop'
$port = 18081
$taskName = 'HomeServer-Strata-Bridge'
$ruleName = 'HomeServer-Strata-Bridge-WSL'
$installDir = Join-Path $env:ProgramData 'HomeServer\StrataBridge'
$tokenPath = Join-Path $installDir 'token.xml'
$serverPath = Join-Path $installDir 'bridge.ps1'
$logPath = Join-Path $installDir 'bridge.log'

$wslAddressOutput = & wsl.exe -d Ubuntu -- ip -o -4 addr show dev eth0 scope global
$wslExitCode = $LASTEXITCODE
$wslAddressLine = $wslAddressOutput | Select-Object -First 1
if ($wslExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($wslAddressLine)) {
    throw 'Could not read Ubuntu eth0 address. Start WSL2 and retry.'
}
$wslAddressFields = $wslAddressLine.Trim() -split '\s+'
$wslAddress = if ($wslAddressFields.Count -ge 4 -and $wslAddressFields[2] -eq 'inet') { $wslAddressFields[3] } else { '' }
if ($wslAddress -notmatch '^([0-9]{1,3}\.){3}[0-9]{1,3}/\d+$') {
    throw 'Could not read Ubuntu eth0 address/prefix. Start WSL2 and retry.'
}

function Get-NetworkCidr([string]$AddressPrefix) {
    $parts = $AddressPrefix.Split('/')
    $bytes = ([System.Net.IPAddress]::Parse($parts[0])).GetAddressBytes()
    $prefix = [int]$parts[1]
    for ($i = 0; $i -lt 4; $i++) {
        $remaining = [Math]::Max(0, [Math]::Min(8, $prefix - (8 * $i)))
        $mask = if ($remaining -eq 0) { 0 } else { (255 -shl (8 - $remaining)) -band 255 }
        $bytes[$i] = $bytes[$i] -band $mask
    }
    return "$([System.Net.IPAddress]::new($bytes))/$prefix"
}
$wslCidr = Get-NetworkCidr $wslAddress

New-Item -ItemType Directory -Force -Path $installDir | Out-Null
if ($ReuseStoredToken) {
    if (-not (Test-Path -LiteralPath $tokenPath)) { throw 'No stored bridge token is available to reuse.' }
    $token = Import-Clixml -LiteralPath $tokenPath
} else {
    $token = Read-Host 'Paste STRATA_BRIDGE_TOKEN from Vault (input hidden)' -AsSecureString
}
if ($token -isnot [Security.SecureString] -or $token.Length -lt 32) { throw 'The bridge token is missing or unexpectedly short.' }
$token | Export-Clixml -LiteralPath $tokenPath

$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask -and $existingTask.State -eq 'Running') {
    & schtasks.exe /End /TN $taskName | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the existing bridge task.' }
    $stopDeadline = (Get-Date).AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 200
        $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    } while ($existingTask -and $existingTask.State -eq 'Running' -and (Get-Date) -lt $stopDeadline)
    if ($existingTask -and $existingTask.State -eq 'Running') { throw 'The existing bridge task did not stop; left its files unchanged.' }
}

$server = @'
$ErrorActionPreference = 'Continue'
Add-Type -AssemblyName System.Net.Http
$port = 18081
$bridgeRoot = Join-Path $env:ProgramData 'HomeServer\StrataBridge'
$tokenPath = Join-Path $bridgeRoot 'token.xml'
$logPath = Join-Path $bridgeRoot 'bridge.log'
$secureToken = Import-Clixml -LiteralPath $tokenPath
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try { $bridgeToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
$listener = [Net.HttpListener]::new()
$listener.Prefixes.Add("http://+:$port/")
$listener.Start()
$client = [System.Net.Http.HttpClient]::new()
$client.Timeout = [System.Threading.Timeout]::InfiniteTimeSpan
$expected = [Text.Encoding]::UTF8.GetBytes($bridgeToken)
while ($listener.IsListening) {
    $context = $null
    $request = $null
    $response = $null
    try {
        $context = $listener.GetContext()
        if ($context.Request.Url.AbsolutePath -eq '/healthz') {
            $context.Response.StatusCode = 200
            $context.Response.Close()
            continue
        }
        $authorization = $context.Request.Headers['Authorization']
        $provided = if ($authorization -match '^Bearer (.+)$') { [Text.Encoding]::UTF8.GetBytes($Matches[1]) } else { [byte[]]@() }
        $authorized = $false
        if ($provided.Length -eq $expected.Length) {
            $difference = 0
            for ($index = 0; $index -lt $expected.Length; $index++) {
                $difference = $difference -bor ($provided[$index] -bxor $expected[$index])
            }
            $authorized = $difference -eq 0
        }
        if (-not $authorized) {
            $context.Response.StatusCode = 401
            $context.Response.Close()
            continue
        }
        if ($context.Request.ContentLength64 -gt 33554432) {
            $context.Response.StatusCode = 413
            $context.Response.Close()
            continue
        }
        $target = [Uri]::new("http://127.0.0.1:8081$($context.Request.Url.PathAndQuery)")
        $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::new($context.Request.HttpMethod), $target)
        if ($context.Request.HasEntityBody) { $request.Content = [Net.Http.StreamContent]::new($context.Request.InputStream) }
        foreach ($headerName in $context.Request.Headers.AllKeys) {
            if ($headerName -in @('Authorization','Host','Connection','Transfer-Encoding','Content-Length')) { continue }
            $headerValue = $context.Request.Headers[$headerName]
            if (-not $request.Headers.TryAddWithoutValidation($headerName, $headerValue) -and $null -ne $request.Content) {
                [void]$request.Content.Headers.TryAddWithoutValidation($headerName, $headerValue)
            }
        }
        $response = $client.SendAsync($request, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        $context.Response.StatusCode = [int]$response.StatusCode
        foreach ($header in $response.Headers) {
            if ($header.Key -notin @('Transfer-Encoding','Connection','Keep-Alive','Date','Server')) { $context.Response.Headers[$header.Key] = [string]::Join(',', $header.Value) }
        }
        foreach ($header in $response.Content.Headers) {
            if ($header.Key -eq 'Content-Type') { $context.Response.ContentType = [string]::Join(',', $header.Value); continue }
            if ($header.Key -notin @('Transfer-Encoding','Connection','Keep-Alive','Content-Length')) { $context.Response.Headers[$header.Key] = [string]::Join(',', $header.Value) }
        }
        $contentLength = $response.Content.Headers.ContentLength
        if ($null -ne $contentLength) { $context.Response.ContentLength64 = [long]$contentLength }
        else { $context.Response.SendChunked = $true }
        $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $stream.CopyToAsync($context.Response.OutputStream).GetAwaiter().GetResult()
    } catch {
        try { Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format o) $($_.Exception.GetType().FullName): $($_.Exception.Message)" -Encoding UTF8 } catch {}
        if ($null -ne $context) { try { $context.Response.StatusCode = 502; $context.Response.Close() } catch {} }
    } finally {
        if ($null -ne $response) { $response.Dispose() }
        if ($null -ne $request) { $request.Dispose() }
    }
}
'@
Set-Content -LiteralPath $serverPath -Value $server -Encoding UTF8

& netsh.exe http delete urlacl "url=http://+:$port/" 2>$null | Out-Null
& netsh.exe http add urlacl "url=http://+:$port/" "user=$env:USERDOMAIN\$env:USERNAME" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Failed to reserve the bridge listener URL.' }
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -Confirm:$false
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -RemoteAddress $wslCidr -Profile Any | Out-Null

$account = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$serverPath`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $account
$principal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

$deadline = (Get-Date).AddSeconds(20)
$bridgeReady = $false
do {
    Start-Sleep -Milliseconds 500
    try { $bridgeReady = (Invoke-WebRequest -Uri "http://127.0.0.1:$port/healthz" -TimeoutSec 2).StatusCode -eq 200 } catch { }
} while (-not $bridgeReady -and (Get-Date) -lt $deadline)
if (-not $bridgeReady) { throw "Bridge did not become healthy. Check $logPath." }

$tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($token)
try {
    $plainToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    $models = Invoke-RestMethod -Uri "http://127.0.0.1:$port/v1/models" -Headers @{ Authorization = "Bearer $plainToken" } -TimeoutSec 10
} catch {
    throw "Bridge health succeeded, but authenticated llama-swap model discovery failed. Check $logPath. $($_.Exception.Message)"
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    $plainToken = $null
}
if (-not $models.data -or $models.data.Count -lt 1) { throw "Bridge returned no models. Check $logPath." }

Write-Host "Installed and verified the authenticated WSL-only bridge on TCP $port (firewall source: $wslCidr)."
Write-Host "llama-swap model discovery succeeded ($($models.data.Count) models)."
Write-Host 'Run Ansible prepare-host.yml in WSL to refresh the Kubernetes endpoint after WSL networking changes.'
