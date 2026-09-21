param(
    [string]$Python = "C:\Users\agent\AppData\Local\Programs\Python\Python312\python.exe",
    [string]$Db = ".runtime\mission-control.db",
    [int]$Interval = 30,
    [int]$MaxParallel = 3
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path $PSScriptRoot -Parent
$env:PYTHONPATH = Join-Path $Repo "src"
Set-Location $Repo

& $Python -m mission_control.watchdog_runtime --db $Db --runtime-root ".runtime\watchdog" --worktree-root "C:\Users\agent\Documents\GitHub\.worktrees" --max-parallel $MaxParallel --interval $Interval
exit $LASTEXITCODE
