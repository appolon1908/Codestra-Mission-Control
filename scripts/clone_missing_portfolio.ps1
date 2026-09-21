param(
    [string]$Portfolio = "config\portfolio.repositories.json",
    [string]$LocalRoot = "C:\Users\agent\Documents\GitHub"
)

$ErrorActionPreference = "Stop"
$Git = "C:\Users\agent\AppData\Local\Programs\Git\cmd\git.exe"
if (-not (Test-Path $Git)) {
    throw "Git executable not found: $Git"
}

$PortfolioPath = if ([IO.Path]::IsPathRooted($Portfolio)) {
    $Portfolio
} else {
    Join-Path (Split-Path $PSScriptRoot -Parent) $Portfolio
}

$Config = Get-Content $PortfolioPath -Raw | ConvertFrom-Json
$Results = @()

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

    $Args = @(
        "clone",
        "--filter=blob:none",
        "--no-tags",
        "--single-branch"
    )
    if ($Repo.default_branch) {
        $Args += @("--branch", [string]$Repo.default_branch)
    }
    $Args += @([string]$Repo.clone_url, $Destination)

    & $Git @Args 2>&1 | Out-Host
    $Code = $LASTEXITCODE

    if ($Code -eq 0 -and (Test-Path (Join-Path $Destination ".git"))) {
        $Head = (& $Git -C $Destination rev-parse HEAD 2>$null | Out-String).Trim()
        $Results += [pscustomobject]@{
            repository = $Repo.name
            status = "cloned"
            path = $Destination
            head = $Head
        }
    } else {
        $Results += [pscustomobject]@{
            repository = $Repo.name
            status = "clone-failed"
            path = $Destination
            exit = $Code
        }
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
$Summary | ConvertTo-Json -Depth 5
