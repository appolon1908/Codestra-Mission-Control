param(
    [string]$TemporalAddress = "100.76.208.87:7233",
    [string]$Namespace = "codestra-mission-control",
    [string]$TaskQueue = "codestra-mission-control",
    [int]$PollSeconds = 15
)

$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Database = Join-Path $Root ".runtime\mission-control.db"
$LogDir = Join-Path $Root ".runtime\logs"
$Log = Join-Path $LogDir "temporal-worker.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if (-not (Test-Path $Python)) {
    throw "Mission Control virtual environment is missing: $Python"
}

$env:PYTHONPATH = Join-Path $Root "src"
$env:TEMPORAL_ADDRESS = $TemporalAddress
$env:TEMPORAL_NAMESPACE = $Namespace
$env:TEMPORAL_TASK_QUEUE = $TaskQueue
$env:MISSION_CONTROL_DB = $Database
$env:MISSION_CONTROL_POLL_SECONDS = [string]$PollSeconds

("START " + (Get-Date).ToUniversalTime().ToString("o") + " " + $TemporalAddress) |
    Add-Content -Path $Log

& $Python -m mission_control.temporal_runtime worker *>> $Log
$Code = $LASTEXITCODE

("EXIT " + (Get-Date).ToUniversalTime().ToString("o") + " code=" + $Code) |
    Add-Content -Path $Log

exit $Code
