param(
    [string]$Portfolio = "config\portfolio.repositories.json",
    [string]$LocalRoot = "C:\Users\agent\Documents\GitHub",
    [string]$ResultPath = "C:\Users\agent\AppData\Local\Temp\codestra-portfolio-clone-result.json",
    [string]$LogPath = "C:\Users\agent\AppData\Local\Temp\codestra-portfolio-clone.log"
)

$ErrorActionPreference = "Continue"
$env:GIT_TERMINAL_PROMPT = "0"
$env:GCM_INTERACTIVE = "Never"

$Git = "C:\Users\agent\AppData\Local\Programs\Git\cmd\git.exe"
if (-not (Test-Path $Git)) {
    throw "Git executable not found: $Git"
}

$RepoRoot = Split-Path $PSScriptRoot -Parent
$PortfolioPath = if ([IO.Path]::IsPathRooted($Portfolio)) {
    $Portfolio
} else {
    Join-Path $RepoRoot $Portfolio
}

$Config = Get-Content $PortfolioPath -Raw | ConvertFrom-Json
$Results = @()
Remove-Item $LogPath -Force -ErrorAction SilentlyContinue

foreach ($Repo in $Config.repositories) {
    $Destination = Join-Path $LocalRoot $Repo.name

    if (Test-Path (Join-Path $Destination ".git")) {
        $Results += [pscustomobject]@{
            repository = $Repo.name
            status = "already-present"
            path = $Destination
        }
        continue
    }

    if (Test-Path $Destination) {
        $Results += [pscustomobject]@{
            repository = $Repo.name
            status = "blocked-existing-non-git-path"
            path = $Destination
        }
        continue
    }

    Add-Content $LogPath ("START " + $Repo.name + " " + (Get-Date).ToString("o"))

    $Args = @(
        "-c", "credential.interactive=never",
        "-c", "http.lowSpeedLimit=1",
        "-c", "http.lowSpeedTime=30",
        "clone",
        "--filter=blob:none",
        "--no-tags",
        "--single-branch"
    )
    if ($Repo.default_branch) {
        $Args += @("--branch", [string]$Repo.default_branch)
    }
    $Args += @([string]$Repo.clone_url, $Destination)

    $CloneOutput = & $Git @Args 2>&1
    $Code = $LASTEXITCODE
    foreach ($Line in @($CloneOutput)) {
        Add-Content $LogPath ([string]$Line)
    }

    if ($Code -eq 0 -and (Test-Path (Join-Path $Destination ".git"))) {
        $Head = (& $Git -C $Destination rev-parse HEAD 2>$null | Out-String).Trim()
        $Results += [pscustomobject]@{
            repository = $Repo.name
            status = "cloned"
            path = $Destination
            head = $Head
        }
        Add-Content $LogPath ("PASS " + $Repo.name)
    } else {
        $Results += [pscustomobject]@{
            repository = $Repo.name
            status = "clone-failed"
            path = $Destination
            exit = $Code
        }
        Add-Content $LogPath ("FAIL " + $Repo.name + " exit=" + $Code)
    }
}

$Summary = [ordered]@{
    total = $Results.Count
    cloned = @($Results | Where-Object status -eq "cloned").Count
    already_present = @($Results | Where-Object status -eq "already-present").Count
    blocked = @($Results | Where-Object status -eq "blocked-existing-non-git-path").Count
    failed = @($Results | Where-Object status -eq "clone-failed").Count
    results = $Results
}
$Json = $Summary | ConvertTo-Json -Depth 5
$Json | Set-Content -Path $ResultPath -Encoding UTF8
$Json
exit $(if ($Summary.failed -eq 0 -and $Summary.blocked -eq 0) { 0 } else { 1 })
