param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8780,
    [switch]$NoTunnel,
    [ValidateSet("auto", "cloudflared", "wrangler")]
    [string]$TunnelRunner = "auto",
    [ValidateSet("http2", "quic", "auto")]
    [string]$TunnelProtocol = "http2",
    [switch]$DisableServerLlm
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

Start-Sleep -Seconds 5

try {
    $health = Invoke-WebRequest -UseBasicParsing -Uri "$url/api/health" -TimeoutSec 10
    if ($health.StatusCode -ne 200) {
        throw "Health check returned HTTP $($health.StatusCode)"
    }
    Write-Host "Local server is ready."
} catch {
    Write-Host "Server logs:"
    if (Test-Path $serverOut) { Get-Content $serverOut -Tail 40 }
    if (Test-Path $serverErr) { Get-Content $serverErr -Tail 80 }
    Stop-DemoProcess $serverProcess
    throw
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
