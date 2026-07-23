param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8780,
    [switch]$NoTunnel,
    [ValidateSet("auto", "cloudflared", "wrangler")]
    [string]$TunnelRunner = "auto",
    [ValidateSet("http2", "quic", "auto")]
    [string]$TunnelProtocol = "http2",
    [switch]$DisableServerLlm,
    [switch]$RunSmoke
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$LogDir = Join-Path $Root "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$serverOut = Join-Path $LogDir "web_public_demo.out.log"
$serverErr = Join-Path $LogDir "web_public_demo.err.log"
$pidFile = Join-Path $LogDir "web_public_demo.pid"
$url = "http://${HostName}:${Port}"

function Clear-ServerLlmSecretsForChild {
    $secretNames = @(
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "MISTRAL_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENAI_API_KEY",
        "MISTRAL_BASE_URL",
        "DEEPSEEK_BASE_URL",
        "OPENAI_BASE_URL"
    )
    $oldValues = @{}
    foreach ($name in $secretNames) {
        $oldValues[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, $null, "Process")
    }
    return $oldValues
}

function Restore-ProcessEnv($oldValues) {
    foreach ($name in $oldValues.Keys) {
        [Environment]::SetEnvironmentVariable($name, $oldValues[$name], "Process")
    }
}

function Stop-DemoProcess($process) {
    if ($null -ne $process -and -not $process.HasExited) {
        Write-Host ""
        Write-Host "Stopping FinPortfolio public demo server PID $($process.Id)"
        Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
    }
}

try {
    $probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Parse($HostName), $Port)
    $probe.Start()
    $probe.Stop()
} catch {
    throw "Port $Port on $HostName is already in use. Stop the old demo or pass a different -Port."
}

# Preflight: the demo serves search over this prebuilt SQLite index.
$indexPath = Join-Path $Root "data\search_index\finportfolio_search.sqlite"
if (-not (Test-Path $indexPath)) {
    throw "Search index not found at $indexPath. Rebuild it before sharing the demo (see docs/PUBLIC_DEMO_CLOUDFLARE.md)."
}

# Preflight: make sure `python` can actually import the app before we background it.
try {
    & python -c "import sys; import web_app" *> $null
    if ($LASTEXITCODE -ne 0) { throw "python could not import web_app.py" }
} catch {
    throw "The 'python' on PATH cannot run web_app.py. Use a Python 3.9+ that can import the project (e.g. the conda env), then retry."
}

Write-Host "Starting FinPortfolio IR public demo server at $url"
$env:PYTHONUNBUFFERED = "1"
$serverArgs = @(
    "web_app.py",
    "--host", $HostName,
    "--port", "$Port",
    "--public-demo",
    "--demo-settings-dir", "data\user_settings\demo_sessions"
)

if ($DisableServerLlm) {
    $oldEnv = Clear-ServerLlmSecretsForChild
    [Environment]::SetEnvironmentVariable("FINPORTFOLIO_DISABLE_SERVER_LLM", "1", "Process")
}
try {
    $serverProcess = Start-Process `
        -FilePath "python" `
        -ArgumentList $serverArgs `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $serverOut `
        -RedirectStandardError $serverErr `
        -WindowStyle Hidden `
        -PassThru
} finally {
    if ($DisableServerLlm) {
        [Environment]::SetEnvironmentVariable("FINPORTFOLIO_DISABLE_SERVER_LLM", $null, "Process")
        Restore-ProcessEnv $oldEnv
    }
}

$serverProcess.Id | Set-Content -Path $pidFile -Encoding ASCII

# Poll the health endpoint instead of guessing a fixed sleep: the server loads
# an 811 MB SQLite index and can take several seconds to answer.
Write-Host "Waiting for the local server to become ready..."
$ready = $false
for ($i = 0; $i -lt 45; $i++) {
    if ($serverProcess.HasExited) {
        Write-Host "Demo server exited during startup. Logs:"
        if (Test-Path $serverErr) { Get-Content $serverErr -Tail 80 }
        throw "Demo server exited before it became ready."
    }
    try {
        $health = Invoke-WebRequest -UseBasicParsing -Uri "$url/api/health" -TimeoutSec 5
        if ($health.StatusCode -eq 200) { $ready = $true; break }
    } catch {
        # not up yet; keep polling
    }
    Start-Sleep -Seconds 1
}
if (-not $ready) {
    Write-Host "Server did not become ready in time. Logs:"
    if (Test-Path $serverOut) { Get-Content $serverOut -Tail 40 }
    if (Test-Path $serverErr) { Get-Content $serverErr -Tail 80 }
    Stop-DemoProcess $serverProcess
    throw "Health check did not pass within 45 seconds."
}
Write-Host "Local server is ready."

if ($RunSmoke) {
    Write-Host "Running live search smoke checks before opening the tunnel..."
    $smokeOutput = Join-Path $Root "data\exports\live_search_smoke_cloudflare_preflight"
    try {
        & python "evaluation\run_live_search_smoke.py" `
            --base-url $url `
            --output-dir $smokeOutput `
            --strict
        Write-Host "Live search smoke checks passed. Results: $smokeOutput"
    } catch {
        Write-Host "Live search smoke checks failed. Server logs:"
        if (Test-Path $serverOut) { Get-Content $serverOut -Tail 40 }
        if (Test-Path $serverErr) { Get-Content $serverErr -Tail 80 }
        Stop-DemoProcess $serverProcess
        throw
    }
}

if ($NoTunnel) {
    Write-Host "NoTunnel was set. Open $url locally."
    exit 0
}

function Invoke-CloudflareTunnel($targetUrl) {
    $cloudflared = Get-Command "cloudflared" -ErrorAction SilentlyContinue
    $npx = Get-Command "npx" -ErrorAction SilentlyContinue

    if ($TunnelRunner -eq "cloudflared" -or ($TunnelRunner -eq "auto" -and $cloudflared)) {
        $configCandidates = @(
            (Join-Path -Path $HOME -ChildPath ".cloudflared\config.yml"),
            (Join-Path -Path $HOME -ChildPath ".cloudflared\config.yaml")
        )
        if ($configCandidates | Where-Object { Test-Path $_ }) {
            Write-Warning "A .cloudflared config file exists. If quick tunnel fails, rerun with -TunnelRunner wrangler."
        }
        $cloudflaredArgs = @("tunnel", "--url", $targetUrl)
        if ($TunnelProtocol -ne "auto") {
            $cloudflaredArgs = @("tunnel", "--protocol", $TunnelProtocol, "--url", $targetUrl)
        }
        Write-Host "Using cloudflared protocol: $TunnelProtocol"
        & $cloudflared.Source @cloudflaredArgs
        return
    }

    if ($TunnelRunner -eq "wrangler" -or ($TunnelRunner -eq "auto" -and $npx)) {
        if ($TunnelProtocol -ne "http2") {
            Write-Warning "TunnelProtocol applies to cloudflared only. Wrangler quick-start chooses its own transport."
        }
        & $npx.Source --yes wrangler@latest tunnel quick-start $targetUrl
        return
    }

    throw "No tunnel runner found. Install cloudflared, or install Node.js/npm so npx wrangler can run."
}

Write-Host ""
Write-Host "Starting Cloudflare quick tunnel. Copy the trycloudflare.com URL from the output below."
Write-Host "Default transport is HTTP/2 over TCP. Use -TunnelProtocol quic only if UDP/QUIC is stable on this network."
Write-Host "Keep this PowerShell window open while testers use the site."
Write-Host ""
try {
    Invoke-CloudflareTunnel $url
} finally {
    Stop-DemoProcess $serverProcess
}
