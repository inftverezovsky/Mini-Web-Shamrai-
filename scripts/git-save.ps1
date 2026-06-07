param(
    [string]$Message,
    [string]$Remote = "origin",
    [switch]$NoPush
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Text)
    Write-Host "[git-save] $Text"
}

$repoRoot = git rev-parse --show-toplevel 2>$null
if (-not $repoRoot) {
    throw "This directory is not inside a Git repository."
}

Set-Location $repoRoot

if (-not $Message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Message = "Save changes $stamp"
}

Write-Step "Staging all changes..."
git add --all

$status = git status --porcelain
if (-not $status) {
    Write-Step "No local changes to commit."
} else {
    Write-Step "Creating local commit..."
    git commit -m $Message
}

if ($NoPush) {
    Write-Step "Push skipped by -NoPush."
    exit 0
}

$remoteUrl = git remote get-url $Remote 2>$null
if (-not $remoteUrl) {
    Write-Step "Remote '$Remote' is not configured. Local commit is saved."
    Write-Step "Add a remote later, then run: git remote add origin <github-repo-url>"
    exit 0
}

$branch = git branch --show-current
if (-not $branch) {
    throw "Cannot detect current branch."
}

Write-Step "Pushing $branch to $Remote..."
git push -u $Remote $branch
Write-Step "Done."
