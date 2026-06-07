param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8780
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$pidFile = Join-Path $Root "data\logs\web_public_demo.pid"
$targetUrl = "http://${HostName}:${Port}"

if (Test-Path $pidFile) {
    $pidText = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($pidText -match "^\d+$") {
        $demoPid = [int]$pidText
        $process = Get-Process -Id $demoPid -ErrorAction SilentlyContinue
        if ($process) {
            Write-Host "Stopping FinPortfolio public demo server PID $demoPid"
            Stop-Process -Id $demoPid -Force
        }
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}

$processes = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like "python*" -and $_.CommandLine -like "*web_app.py*" -and $_.CommandLine -like "*--public-demo*"
}

foreach ($process in $processes) {
    Write-Host "Stopping FinPortfolio public demo server PID $($process.ProcessId)"
    Stop-Process -Id $process.ProcessId -Force
}

if (-not $processes) {
    Write-Host "No public demo web_app.py process found."
}

$tunnelProcesses = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like "cloudflared*" -and
    $_.CommandLine -like "*tunnel*" -and
    $_.CommandLine -like "*--url*" -and
    $_.CommandLine -like "*$targetUrl*"
}

foreach ($process in $tunnelProcesses) {
    Write-Host "Stopping Cloudflare tunnel PID $($process.ProcessId) for $targetUrl"
    Stop-Process -Id $process.ProcessId -Force
}

if (-not $tunnelProcesses) {
    Write-Host "No Cloudflare tunnel process found for $targetUrl."
}
