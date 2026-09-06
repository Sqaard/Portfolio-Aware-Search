<#
.SYNOPSIS
  Submit a FinPortfolio IR Big Data job to the standalone Spark cluster (PowerShell).

.DESCRIPTION
  PowerShell cannot execute the sibling submit.sh (on most Windows boxes ".sh" is
  associated with an editor, so running it just opens the file). Use this wrapper
  instead - it does exactly what submit.sh does: runs the driver inside the master
  container against spark://spark-master:7077, so map/reduce stages are distributed
  to the worker containers.

  Watch progress at http://localhost:8080 (master) and http://localhost:4040
  (driver UI, only while a job runs).

.EXAMPLE
  .\deploy\spark_cluster\submit.ps1 bigdata.run_all --corpus all_ppo --native-read --partitions 16

.EXAMPLE
  .\deploy\spark_cluster\submit.ps1 bigdata.run_build_search_index --corpus all_ppo --native-read `
      --output data/exports/bigdata/search_index/finportfolio_search_spark.sqlite
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Module,

    # Everything after the module name is forwarded verbatim to the job.
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$JobArgs,

    # Driver heap. The SQLite index build collects the whole corpus, so raise it there.
    [string]$DriverMemory = "2g"
)

$ErrorActionPreference = "Stop"
$composeFile = Join-Path $PSScriptRoot "docker-compose.yml"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker not found on PATH. Install Docker Desktop and start it."
}
try {
    docker ps *> $null
    if ($LASTEXITCODE -ne 0) { throw "engine down" }
} catch {
    throw "Docker engine is not running. Start Docker Desktop, then retry."
}

$running = (docker ps --filter "name=finportfolio-spark-master" --format "{{.Names}}")
if (-not $running) {
    Write-Host "Spark master is not running. Starting the cluster..." -ForegroundColor Yellow
    docker compose -f $composeFile up -d
    Write-Host "Waiting for workers to register..."
    Start-Sleep -Seconds 12
}

Write-Host "Submitting $Module to spark://spark-master:7077" -ForegroundColor Cyan
Write-Host "  master UI: http://localhost:8080   driver UI (while running): http://localhost:4040"

# MSYS_NO_PATHCONV stops Git-Bash-style path mangling of /workspace paths.
$env:MSYS_NO_PATHCONV = "1"

# Spark logs progress to stderr. With ErrorActionPreference=Stop, PowerShell wraps
# each native-stderr line in an ErrorRecord and aborts the script even on success,
# so relax it for the actual submit and judge the run by its exit code instead.
$ErrorActionPreference = "Continue"

docker compose -f $composeFile exec -T `
    -e PYSPARK_SUBMIT_ARGS="--driver-memory $DriverMemory pyspark-shell" `
    spark-master python3 -m $Module `
    --engine spark `
    --master spark://spark-master:7077 `
    @JobArgs

exit $LASTEXITCODE
