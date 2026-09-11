<#
.SYNOPSIS
  Architecture experiment: which Spark cluster shape is best on one host.

.DESCRIPTION
  Runs four cluster shapes with the SAME total heap (6000m), changing only how
  executors and cores are split:

    A  2 x 5 SMT = 10 threads   (2 left for master/history)
    B  5 x 2 SMT = 10 threads   (2 left)
    C  4 x 2 SMT =  8 threads   (4 left)
    D  3 x 4 SMT = 12 threads   (none left)

  For each shape it measures:
    * skeleton -- the same plan and task count over 12 documents: pure fixed overhead
    * full     -- the whole corpus (3,029 filings, 57.2 M tokens)

  Time comes from bm25_stats.json -> run.seconds (the timer around the BM25 build),
  the figure the tables in BIG_DATA_INFRASTRUCTURE.md use.

  -Api rdd runs the original map/reduce through Python workers; -Api sql runs the
  same statistics as one Catalyst job (bigdata.jobs.sql_inverted_index), no Python
  worker. The first run of every shape is a warm-up and is discarded.

  Task counts: rdd runs 4 stages x p tasks; sql runs p scan + p reduce tasks (AQE
  coalescing off). The actual scan task count is recorded in the scan_tasks column.

.EXAMPLE
  .\deploy\spark_cluster\run_arch_experiment.ps1 -Api sql -Repeats 3

.EXAMPLE
  # one shape, quick
  .\deploy\spark_cluster\run_arch_experiment.ps1 -Api sql -Only C -Repeats 1
#>
[CmdletBinding()]
param(
    [int]$Repeats = 3,
    [string]$Only = "",
    [string]$Csv = "",
    [string]$Corpus = "/fast/sec_full_text_heavy_documents.jsonl",
    [ValidateSet('rdd','sql')][string]$Api = "rdd",
    # By default no host ports are published (docker-compose.noports.yml): after a
    # WSL restart Windows often reserves 8080-8083. Jobs go through `exec` anyway.
    [switch]$HostPorts
)
if (-not $Csv) {
    $Csv = if ($Api -eq 'sql') { "data/exports/bigdata/arch_experiment_sql.csv" } else { "data/exports/bigdata/arch_experiment.csv" }
}
if (-not $env:FINPORTFOLIO_EXTRA_DIR) { $env:FINPORTFOLIO_EXTRA_DIR = "C:/tmp/finportfolio_bigdata" }

$ErrorActionPreference = "Continue"
$env:MSYS_NO_PATHCONV = "1"
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$stats = Join-Path $root "data/exports/bigdata/run/inverted_index/bm25_stats.json"

# tag, file, workers, coresPerExec, heapMb, partition counts to test
$archs = @(
    [pscustomobject]@{ Tag='A'; File='docker-compose.arch-A-2x5.yml'; Workers=2; Cores=5; Heap=3000; Parts=@(10,12) },
    [pscustomobject]@{ Tag='B'; File='docker-compose.arch-B-5x2.yml'; Workers=5; Cores=2; Heap=1200; Parts=@(10,12) },
    [pscustomobject]@{ Tag='C'; File='docker-compose.arch-C-4x2.yml'; Workers=4; Cores=2; Heap=1500; Parts=@(8,12)  },
    [pscustomobject]@{ Tag='D'; File='docker-compose.arch-D-3x4.yml'; Workers=3; Cores=4; Heap=2000; Parts=@(12)    }
)
if ($Only) { $archs = $archs | Where-Object { $_.Tag -eq $Only } }

function Get-ComposeFiles([string]$File) {
    $files = @('-f', "deploy/spark_cluster/$File")
    if (-not $HostPorts) {
        # A per-shape overlay. docker-compose.noports.yml names D's services only:
        # layered over A it defines a spark-worker-3 that A lacks (`up` fails), and
        # over B/C it leaves workers 4-5 publishing ports that may be reserved.
        $services = docker compose -f "deploy/spark_cluster/$File" config --services
        $overlay = Join-Path $env:TEMP ("finportfolio-noports-" + ($File -replace '\.yml$', '') + ".yml")
        $lines = @("name: spark_cluster", "services:")
        foreach ($s in $services) { $lines += "  ${s}:"; $lines += "    ports: !reset []" }
        [IO.File]::WriteAllLines($overlay, [string[]]$lines)   # UTF-8, no BOM
        $files += @('-f', $overlay)
    }
    return $files
}

if (-not (Test-Path (Split-Path $Csv))) { New-Item -ItemType Directory -Force (Split-Path $Csv) | Out-Null }
"arch,workers,cores_per_exec,total_cores,heap_mb_per_exec,kind,partitions,tasks,run,seconds,n_docs,vocab,api,scan_tasks" |
    Out-File -FilePath $Csv -Encoding utf8

function Invoke-Run {
    param($Arch, [int]$Parts, [string]$Kind)

    if (Test-Path $stats) { Remove-Item $stats -Force }
    $total = $Arch.Workers * $Arch.Cores
    $conf = "--conf spark.executor.cores=$($Arch.Cores) " +
            "--conf spark.executor.memory=$($Arch.Heap)m " +
            "--conf spark.cores.max=$total --driver-memory 1g pyspark-shell"

    # skeleton = --limit 12 (same plan and tasks, no work); full = the whole corpus
    if ($Kind -eq 'skeleton') { $extra = @('--limit','12') }
    elseif ($Api -eq 'rdd') { $extra = @('--native-read') }
    else { $extra = @() }

    $files = Get-ComposeFiles $Arch.File
    docker compose @files exec -T `
        -e PYSPARK_SUBMIT_ARGS="$conf" spark-master `
        python3 -m bigdata.run_inverted_index --engine spark --api $Api `
        --master spark://spark-master:7077 --corpus $Corpus `
        @extra --partitions $Parts *> $null

    if (-not (Test-Path $stats)) { return $null }
    return (Get-Content $stats -Raw | ConvertFrom-Json)
}

foreach ($a in $archs) {
    $total = $a.Workers * $a.Cores
    Write-Host ""
    Write-Host "=== Shape $($a.Tag): $($a.Workers) x $($a.Cores) = $total SMT, heap $($a.Heap)m x $($a.Workers), api $Api ===" -ForegroundColor Cyan

    docker compose -f "deploy/spark_cluster/docker-compose.yml" down *> $null
    foreach ($other in $archs) { docker compose -f "deploy/spark_cluster/$($other.File)" down *> $null }
    if ($Only) {   # the other shapes are not in $archs: make sure none of them is left running
        foreach ($f in @('docker-compose.arch-A-2x5.yml','docker-compose.arch-B-5x2.yml','docker-compose.arch-C-4x2.yml','docker-compose.arch-D-3x4.yml','docker-compose.6x2.yml')) {
            docker compose -f "deploy/spark_cluster/$f" down *> $null
        }
    }
    $files = Get-ComposeFiles $a.File
    docker compose @files up -d *> $null
    Start-Sleep -Seconds 18

    # confirm the master sees exactly the shape we asked for -- and refuse to
    # measure anything else (a worker that failed to start leaves a smaller cluster)
    $seen = ""
    for ($try = 1; $try -le 6; $try++) {
        $seen = docker exec finportfolio-spark-master python3 -c @"
import re, urllib.request
t = re.sub(r'<[^>]+>', ' ', urllib.request.urlopen('http://spark-master:8080').read().decode('utf-8','replace'))
print(' | '.join(l.strip() for l in t.splitlines() if 'Alive Workers' in l or 'Cores in use' in l))
"@
        if ("$seen" -match "Alive Workers:\s*$($a.Workers)\b" -and "$seen" -match "Cores in use:\s*$total Total") { break }
        Start-Sleep -Seconds 5
    }
    Write-Host "  master sees: $seen"
    if (-not ("$seen" -match "Alive Workers:\s*$($a.Workers)\b" -and "$seen" -match "Cores in use:\s*$total Total")) {
        Write-Host "  shape $($a.Tag) did not come up as $($a.Workers) x $($a.Cores); skipped" -ForegroundColor Red
        continue
    }

    Invoke-Run -Arch $a -Parts $a.Parts[0] -Kind 'full' | Out-Null   # warm-up, discarded

    foreach ($kind in @('skeleton','full')) {
        foreach ($p in $a.Parts) {
            $tasks = if ($Api -eq 'sql') { 2 * $p } else { 4 * $p }
            for ($i = 1; $i -le $Repeats; $i++) {
                $r = Invoke-Run -Arch $a -Parts $p -Kind $kind
                if ($null -eq $r) {
                    Write-Host ("  {0,-9} p={1,-3} run {2}: FAILED" -f $kind, $p, $i) -ForegroundColor Red
                    "$($a.Tag),$($a.Workers),$($a.Cores),$total,$($a.Heap),$kind,$p,$tasks,$i,,,,$Api," | Out-File $Csv -Append -Encoding utf8
                    continue
                }
                $sec = $r.run.seconds
                Write-Host ("  {0,-9} p={1,-3} run {2}: {3,7:N2} s   docs={4}  scan_tasks={5}" -f $kind, $p, $i, $sec, $r.n_docs, $r.run.scan_tasks)
                "$($a.Tag),$($a.Workers),$($a.Cores),$total,$($a.Heap),$kind,$p,$tasks,$i,$sec,$($r.n_docs),$($r.vocabulary_size),$Api,$($r.run.scan_tasks)" |
                    Out-File $Csv -Append -Encoding utf8
            }
        }
    }
}

Write-Host ""
Write-Host "Results written to $Csv" -ForegroundColor Green
