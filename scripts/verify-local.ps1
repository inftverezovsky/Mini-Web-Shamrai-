param(
  [switch]$SkipFrontend,
  [switch]$SkipBackend,
  [switch]$SkipBackendTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$FrontendDir = Join-Path $RepoRoot "frontend"
$BackendDir = Join-Path $RepoRoot "backend"
$BackendPython = Join-Path $BackendDir ".venv\Scripts\python.exe"

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string[]]$Arguments = @()
  )

  Write-Host ">> $FilePath $($Arguments -join ' ')"
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code $LASTEXITCODE`: $FilePath $($Arguments -join ' ')"
  }
}

function Invoke-InDirectory {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][scriptblock]$ScriptBlock
  )

  Push-Location $Path
  try {
    & $ScriptBlock
  } finally {
    Pop-Location
  }
}

if (-not $SkipBackend) {
  if (-not (Test-Path -LiteralPath $BackendPython)) {
    throw @"
Backend virtual environment was not found:
$BackendPython

Create it before running backend checks:
  cd backend
  py -3.11 -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
"@
  }

  Invoke-InDirectory $BackendDir {
    Invoke-Checked $BackendPython @("--version")
    Invoke-Checked $BackendPython @("-c", "import fastapi, sqlalchemy; print('backend_deps_ok')")
    Invoke-Checked $BackendPython @("-m", "compileall", "-q", "src", "alembic")
    Invoke-Checked $BackendPython @("-m", "alembic", "heads")
    Invoke-Checked $BackendPython @("-c", "import src.main; print('backend_import_ok')")
    if (-not $SkipBackendTests) {
      Invoke-Checked $BackendPython @("-m", "pytest", "-q")
    }
  }
}

if (-not $SkipFrontend) {
  Invoke-InDirectory $FrontendDir {
    Invoke-Checked "npm" @("run", "lint")
    Invoke-Checked "npm" @("test")
    Invoke-Checked "npm" @("run", "build")
  }
}

Write-Host "verify_local_ok"
