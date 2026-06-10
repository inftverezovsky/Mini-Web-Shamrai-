param(
  [string]$Workspace = "C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)",
  [string]$Server = "root@82.147.67.245",
  [string]$HostKey = "ssh-ed25519 255 SHA256:xdVRtRaXWqK6eAIsE3VwD0o2H6GJDcCm65L1ZUBjuMw",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [string]$PublicWebRoot = "/var/www/shamrai_web/dist",
  [string]$VkGroupId = "239419819",
  [switch]$SkipLocalChecks,
  [switch]$RepairShamraiConflicts,
  [switch]$PromptPassword
)

$ErrorActionPreference = "Stop"

function Invoke-Step {
  param([string]$Title, [scriptblock]$Script)
  Write-Host ""
  Write-Host "==> $Title" -ForegroundColor Cyan
  & $Script
}

function Find-Tool {
  param([string[]]$Candidates)
  foreach ($candidate in $Candidates) {
    if (Test-Path $candidate) { return $candidate }
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
  }
  return $null
}

function Invoke-NativeChecked {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
  )

  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
  }
}

function Get-ShamraiSshPassword {
  param([bool]$ForcePrompt = $false)

  $passwordFromEnv = $env:SHAMRAI_SSH_PASSWORD
  if (-not $ForcePrompt -and -not [string]::IsNullOrWhiteSpace($passwordFromEnv)) {
    return $passwordFromEnv
  }

  $securePassword = Read-Host "VDS password" -AsSecureString
  if ($securePassword.Length -eq 0) {
    throw "VDS password was empty."
  }

  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
  try {
    return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  }
}

function Set-Utf8NoBomLfContent {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Content
  )

  $encoding = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, ($Content -replace "`r`n", "`n"), $encoding)
}

function New-ServerGuardScript {
  param([bool]$Repair)

  $repairValue = if ($Repair) { "1" } else { "0" }
  $template = @'
set -e
CANON_PROJECT='__COMPOSE_PROJECT__'
CANON_PORT='8082'
REPAIR_SHAMRAI_CONFLICTS='__REPAIR__'

echo 'Shamrai server inventory:'
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' || true
echo 'Listening ports 80/443/8000/8081/8082:'
ss -ltnp | grep -E ':(80|443|8000|8081|8082)\b' || true

if [ "$REPAIR_SHAMRAI_CONFLICTS" = "1" ]; then
  for project in sports-betting shamrai-preview shamrai-mini shamrai-mini-app mini-web mini-web-shamrai; do
    if [ "$project" != "$CANON_PROJECT" ]; then
      docker compose -p "$project" down --remove-orphans || true
    fi
  done

  docker ps -a --format '{{.ID}}\t{{.Names}}' | while IFS="$(printf '\t')" read -r id name; do
    if [ "$name" = "shamrai-backend" ] || [ "$name" = "shamrai-frontend" ] || [ "$name" = "shamrai-postgres" ]; then
      continue
    fi
    case "$name" in
      *shamrai*|*sports-betting*|*mini-web*|*Mini-Web*)
        echo "Removing stale Shamrai-related container: $name"
        docker rm -f "$id" || true
        ;;
    esac
  done
fi

conflicts="$(docker ps --format '{{.ID}}\t{{.Names}}\t{{.Ports}}' | awk -F '\t' -v port="$CANON_PORT" 'index($3, ":" port "->") && $2 != "shamrai-frontend" {print $0}')"
if [ -n "$conflicts" ]; then
  echo "Port $CANON_PORT is owned by a non-canonical container. Do not deploy Shamrai to a new port." >&2
  echo "$conflicts" >&2
  echo "Rerun with -RepairShamraiConflicts only if those containers are stale Shamrai/sports-betting deployments." >&2
  exit 20
fi
'@

  return $template.
    Replace("__COMPOSE_PROJECT__", $ComposeProject).
    Replace("__REPAIR__", $repairValue)
}

if (-not (Test-Path $Workspace)) {
  throw "Workspace not found: $Workspace"
}

$plink = Find-Tool @("C:\Program Files\PuTTY\plink.exe", "plink.exe")
$pscp = Find-Tool @("C:\Program Files\PuTTY\pscp.exe", "pscp.exe")
if (-not $plink -or -not $pscp) {
  throw "PuTTY plink/pscp not found. Install PuTTY or adjust the script."
}

$password = Get-ShamraiSshPassword -ForcePrompt:$PromptPassword

$deployDir = Join-Path $Workspace ".deploy"
$repoArchive = Join-Path $deployDir "shamrai-public-repo.tar.gz"
$webArchive = Join-Path $deployDir "shamrai-web-dist.tar.gz"
$nginxConfig = Join-Path $Workspace "deploy\nginx\shamrai.conf"
$remoteGuardScript = Join-Path $deployDir "shamrai-public-guard.sh"
$remoteDeployScript = Join-Path $deployDir "shamrai-public-remote-deploy.sh"
$remotePublishScript = Join-Path $deployDir "shamrai-public-remote-publish.sh"

if (-not (Test-Path $nginxConfig)) {
  throw "Nginx config not found: $nginxConfig"
}

if (-not $SkipLocalChecks) {
  Invoke-Step "Frontend build" {
      Push-Location (Join-Path $Workspace "frontend")
    $previousEnv = @{
      VITE_API_URL = $env:VITE_API_URL
      VITE_ENABLE_DEBUG_AUTH = $env:VITE_ENABLE_DEBUG_AUTH
      VITE_VK_ID_APP_ID = $env:VITE_VK_ID_APP_ID
      VITE_VK_ID_REDIRECT_URI = $env:VITE_VK_ID_REDIRECT_URI
      VITE_VK_GROUP_ID = $env:VITE_VK_GROUP_ID
      VITE_TELEGRAM_BOT_USERNAME = $env:VITE_TELEGRAM_BOT_USERNAME
    }
    try {
      $env:VITE_API_URL = ""
      $env:VITE_ENABLE_DEBUG_AUTH = "false"
      $env:VITE_VK_ID_APP_ID = "54626979"
      $env:VITE_VK_ID_REDIRECT_URI = "https://shamra1.pro"
      $env:VITE_VK_GROUP_ID = $VkGroupId
      $env:VITE_TELEGRAM_BOT_USERNAME = "Shamra1_bot"
      npm run build
    } finally {
      foreach ($key in $previousEnv.Keys) {
        if ($null -eq $previousEnv[$key]) {
          Remove-Item -Path "Env:$key" -ErrorAction SilentlyContinue
        } else {
          Set-Item -Path "Env:$key" -Value $previousEnv[$key]
        }
      }
      Pop-Location
    }
  }

  Invoke-Step "Backend compile" {
    Push-Location (Join-Path $Workspace "backend")
    try { python -m compileall -q src alembic } finally { Pop-Location }
  }
}

Invoke-Step "Create deployment archives" {
  New-Item -ItemType Directory -Force -Path $deployDir | Out-Null
  Remove-Item -LiteralPath $repoArchive, $webArchive -Force -ErrorAction SilentlyContinue

  Push-Location $Workspace
  try {
    tar `
      --exclude="./.git" `
      --exclude="./.deploy" `
      --exclude="./.env" `
      --exclude="./.env.local" `
      --exclude="./.playwright-cli" `
      --exclude="./deploy.tar.gz" `
      --exclude="./remote.py" `
      --exclude="./test_proxies.py" `
      --exclude="./output" `
      --exclude="./frontend/node_modules" `
      --exclude="./frontend/dist" `
      --exclude="./frontend/.vite" `
      --exclude="./frontend/.env" `
      --exclude="./frontend/.env.local" `
      --exclude="./backend/.venv" `
      --exclude="*/__pycache__" `
      --exclude="*.pyc" `
      --exclude="./backend/.env" `
      --exclude="./backend/.env.local" `
      -czf $repoArchive .
  } finally {
    Pop-Location
  }

  Push-Location (Join-Path $Workspace "frontend\dist")
  try {
    tar -czf $webArchive .
  } finally {
    Pop-Location
  }
}

Invoke-Step "Create remote scripts" {
  $remoteGuardContent = New-ServerGuardScript -Repair:$RepairShamraiConflicts
  Set-Utf8NoBomLfContent -Path $remoteGuardScript -Content $remoteGuardContent

  $remoteDeployContent = @"
set -e
tmp_root_env="/tmp/shamrai-root.env.keep"
tmp_backend_env="/tmp/shamrai-backend.env.keep"
rm -f "`$tmp_root_env" "`$tmp_backend_env"
[ -f "$RemotePath/.env" ] && cp "$RemotePath/.env" "`$tmp_root_env" || true
[ -f "$RemotePath/backend/.env" ] && cp "$RemotePath/backend/.env" "`$tmp_backend_env" || true
rm -rf "$RemotePath"
mkdir -p "$RemotePath"
tar -xzf /tmp/shamrai-public-repo.tar.gz -C "$RemotePath"
[ -f "`$tmp_root_env" ] && cp "`$tmp_root_env" "$RemotePath/.env" || cp "$RemotePath/.env.example" "$RemotePath/.env"
[ -f "`$tmp_backend_env" ] && cp "`$tmp_backend_env" "$RemotePath/backend/.env" || cp "$RemotePath/backend/.env.example" "$RemotePath/backend/.env"
python3 - "$RemotePath/.env" "$RemotePath/backend/.env" <<'PY'
from pathlib import Path
import sys

root_env = Path(sys.argv[1])
backend_env = Path(sys.argv[2])

def patch_env(path, values):
    lines = path.read_text().splitlines() if path.exists() else []
    seen = set()
    out = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0]
            if key in values:
                out.append(f"{key}={values[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out).rstrip() + "\n")

patch_env(root_env, {
    "APP_ENV": "local",
    "DEBUG_MODE": "false",
    "ALLOW_DEBUG_AUTH_BYPASS": "false",
    "FRONTEND_PORT": "8082",
    "VITE_API_URL": "",
    "VITE_ENABLE_DEBUG_AUTH": "false",
    "VITE_VK_ID_APP_ID": "54626979",
    "VITE_VK_ID_REDIRECT_URI": "https://shamra1.pro",
    "VITE_VK_GROUP_ID": "$VkGroupId",
    "VITE_TELEGRAM_BOT_USERNAME": "Shamra1_bot",
})
patch_env(backend_env, {
    "DEBUG_MODE": "false",
    "ALLOW_DEBUG_AUTH_BYPASS": "false",
    "TELEGRAM_USE_POLLING": "true",
    "TELEGRAM_START_RESPONSE_TIMEOUT_SECONDS": "4.0",
    "TELEGRAM_WEBHOOK_IP_ADDRESS": "82.147.67.245",
    "CORS_ALLOWED_ORIGINS": "https://shamra1.pro,https://www.shamra1.pro",
    "API_BASE_URL": "https://shamra1.pro",
    "FRONTEND_BASE_URL": "https://shamra1.pro/app",
    "YOOKASSA_RETURN_URL": "https://shamra1.pro/app",
    "VK_ID_APP_ID": "54626979",
    "VK_ID_REDIRECT_URI": "https://shamra1.pro",
})
PY
cd "$RemotePath"
docker compose -p "$ComposeProject" up -d --build
"@
  Set-Utf8NoBomLfContent -Path $remoteDeployScript -Content $remoteDeployContent

  $remotePublishContent = @"
set -e
mkdir -p "$PublicWebRoot"
find "$PublicWebRoot" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
tar -xzf /tmp/shamrai-web-dist.tar.gz -C "$PublicWebRoot"
install -m 0644 /tmp/shamrai.conf /etc/nginx/sites-available/shamrai.conf
for enabled in /etc/nginx/sites-enabled/*; do
  [ -e "`$enabled" ] || continue
  if grep -qE 'server_name .*shamra1\.pro' "`$enabled"; then
    rm -f "`$enabled"
  fi
done
ln -s /etc/nginx/sites-available/shamrai.conf /etc/nginx/sites-enabled/shamrai.conf
nginx -t
systemctl reload nginx
"@
  Set-Utf8NoBomLfContent -Path $remotePublishScript -Content $remotePublishContent
}

Invoke-Step "Upload server guard" {
  Invoke-NativeChecked $pscp -batch -pw $password -hostkey $HostKey $remoteGuardScript "${Server}:/tmp/shamrai-public-guard.sh"
}

Invoke-Step "Server port/project guard" {
  Invoke-NativeChecked $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey "bash /tmp/shamrai-public-guard.sh"
}

Invoke-Step "Upload archives and nginx config" {
  Invoke-NativeChecked $pscp -batch -pw $password -hostkey $HostKey $repoArchive "${Server}:/tmp/shamrai-public-repo.tar.gz"
  Invoke-NativeChecked $pscp -batch -pw $password -hostkey $HostKey $webArchive "${Server}:/tmp/shamrai-web-dist.tar.gz"
  Invoke-NativeChecked $pscp -batch -pw $password -hostkey $HostKey $nginxConfig "${Server}:/tmp/shamrai.conf"
  Invoke-NativeChecked $pscp -batch -pw $password -hostkey $HostKey $remoteDeployScript "${Server}:/tmp/shamrai-public-remote-deploy.sh"
  Invoke-NativeChecked $pscp -batch -pw $password -hostkey $HostKey $remotePublishScript "${Server}:/tmp/shamrai-public-remote-publish.sh"
}

Invoke-Step "Deploy code, preserve env, rebuild compose" {
  Invoke-NativeChecked $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey "bash /tmp/shamrai-public-remote-deploy.sh"
}

Invoke-Step "Publish host static web and reload nginx" {
  Invoke-NativeChecked $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey "bash /tmp/shamrai-public-remote-publish.sh"
}

Invoke-Step "Health verification" {
  $remote = @"
set -e
cd '$RemotePath'
docker compose -p '$ComposeProject' ps
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/health/telegram
curl -I -fsS http://shamra1.pro/ | head -n 8
curl -I -fsS https://shamra1.pro/ | head -n 8
curl -I -fsS https://shamra1.pro/app/ | head -n 8
"@
  Invoke-NativeChecked $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey $remote
}

Write-Host ""
Write-Host "Public Shamrai web deploy finished." -ForegroundColor Green
Write-Host "Site: https://shamra1.pro/"
Write-Host "Mini App: https://shamra1.pro/app"
