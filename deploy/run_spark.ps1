<#
.SYNOPSIS
  Run a FinPortfolio IR Big Data job locally on PySpark, from PowerShell.

.DESCRIPTION
  PySpark is not installed in the default interpreter on this machine -- `py` and
  `python` resolve to a Python that has no pyspark, so `py -m bigdata.run_...
  --engine spark` fails with EngineUnavailableError. The interpreter that does
  have it is a conda environment.

  This script finds an interpreter that can actually import pyspark and runs the
  module with it, so nothing has to be remembered or set up first. It is the
  local counterpart of deploy/spark_cluster/submit.ps1.

  Search order:
    1. $env:FINPORTFOLIO_PYTHON, if set
    2. known conda environments under the user profile
    3. whatever `python` / `py` resolve to (in case pyspark is installed there)

.EXAMPLE
  .\deploy\run_spark.ps1 bigdata.run_inverted_index --corpus macro --partitions 12

.EXAMPLE
  .\deploy\run_spark.ps1 bigdata.run_sql_inverted_index --corpus macro --partitions 4

.EXAMPLE
  .\deploy\run_spark.ps1 bigdata.run_all --corpus macro --partitions 4 --query "inflation"

.EXAMPLE
  # Point it at a different interpreter for one run:
  $env:FINPORTFOLIO_PYTHON = "D:\envs\spark\python.exe"
  .\deploy\run_spark.ps1 bigdata.run_inverted_index --corpus sample
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Module,

    # Everything after the module name is forwarded to the job unchanged.
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$JobArgs
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Test-PySpark {
    param([string]$Exe)
    if (-not $Exe) { return $false }
    if (-not (Test-Path $Exe)) { return $false }
    & $Exe -c "import pyspark" *> $null
    return ($LASTEXITCODE -eq 0)
}

# --- find an interpreter that can import pyspark --------------------------
$candidates = New-Object System.Collections.Generic.List[string]
if ($env:FINPORTFOLIO_PYTHON) { $candidates.Add($env:FINPORTFOLIO_PYTHON) }
foreach ($env_dir in @("tensorflow", "spark", "bigdata")) {
    $candidates.Add((Join-Path $env:USERPROFILE "anaconda3\envs\$env_dir\python.exe"))
    $candidates.Add((Join-Path $env:USERPROFILE "miniconda3\envs\$env_dir\python.exe"))
}
foreach ($name in @("python", "py")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { $candidates.Add($cmd.Source) }
}

$python = $null
foreach ($candidate in $candidates) {
    if (Test-PySpark $candidate) { $python = $candidate; break }
}

if (-not $python) {
    Write-Host "No interpreter with PySpark was found. Tried:" -ForegroundColor Red
    foreach ($candidate in $candidates) { Write-Host "  $candidate" }
    Write-Host ""
    Write-Host "Fix it either way:" -ForegroundColor Yellow
    Write-Host '  $env:FINPORTFOLIO_PYTHON = "<path to a python.exe that has pyspark>"'
    Write-Host "  ...or install it:  pip install -r requirements-bigdata.txt"
    Write-Host "     (pins pyspark>=3.5,<4.0; 4.0.0 has a Windows worker bug)"
    exit 1
}

$version = (& $python -c "import pyspark,sys; print(pyspark.__version__, 'on Python', sys.version.split()[0])")
Write-Host "PySpark $version" -ForegroundColor Cyan
Write-Host "  $python" -ForegroundColor DarkGray

# --- only supply defaults the caller did not give -------------------------
$forwarded = @()
if ($JobArgs) { $forwarded = $JobArgs }
if ($forwarded -notcontains "--engine") { $forwarded = @("--engine", "spark") + $forwarded }
if ($forwarded -notcontains "--master") { $forwarded = @("--master", "local[*]") + $forwarded }

Write-Host "Running $Module $($forwarded -join ' ')" -ForegroundColor Cyan

# Spark logs to stderr; with ErrorActionPreference=Stop PowerShell would turn
# each of those lines into a terminating error even on a successful run.
$ErrorActionPreference = "Continue"

Push-Location $repoRoot
try {
    & $python -m $Module @forwarded
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
