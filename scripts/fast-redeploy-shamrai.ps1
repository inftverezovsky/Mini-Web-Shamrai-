param(
  [ValidateSet("backend", "frontend", "all")]
  [string]$Target = "all",
  [string]$Workspace = "C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)",
  [string]$Server = "root@82.147.67.245",
  [string]$HostKey = "ssh-ed25519 255 SHA256:xdVRtRaXWqK6eAIsE3VwD0o2H6GJDcCm65L1ZUBjuMw",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [switch]$RepairShamraiConflicts,
  [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"

function Find-Tool {
  param([string[]]$Candidates)
  foreach ($candidate in $Candidates) {
    if (Test-Path $candidate) { return $candidate }
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
  }
  return $null
}

function Invoke-Step {
  param([string]$Title, [scriptblock]$Script)
  Write-Host ""
  Write-Host "==> $Title" -ForegroundColor Cyan
  & $Script
}

function New-ShamraiServerGuardScript {
  param([bool]$Repair)

  $repairValue = if ($Repair) { "1" } else { "0" }
  $template = @'
set -e
CANON_PROJECT='__COMPOSE_PROJECT__'
CANON_PATH='__REMOTE_PATH__'
CANON_PORT='8082'
REPAIR_SHAMRAI_CONFLICTS='__REPAIR__'

echo 'Shamrai server inventory:'
docker ps -a --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}' || true
echo 'Compose labels:'
docker ps -aq | while read -r id; do
  [ -n "$id" ] || continue
  name="$(docker inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's#^/##' || true)"
  labels_json="$(docker inspect -f '{{json .Config.Labels}}' "$id" 2>/dev/null || echo '{}')"
  project="$(printf '%s' "$labels_json" | python3 -c "import json,sys; print((json.load(sys.stdin) or {}).get('com.docker.compose.project', ''))" 2>/dev/null || true)"
  workdir="$(printf '%s' "$labels_json" | python3 -c "import json,sys; print((json.load(sys.stdin) or {}).get('com.docker.compose.project.working_dir', ''))" 2>/dev/null || true)"
  printf '%s\tproject=%s\tworkdir=%s\n' "$name" "$project" "$workdir"
done
echo 'Listening ports 80/443/8000/8081/8082:'
ss -ltnp | grep -E ':(80|443|8000|8081|8082)\b' || true

if [ "$REPAIR_SHAMRAI_CONFLICTS" = "1" ]; then
  for project in sports-betting shamrai-preview shamrai-mini shamrai-mini-app mini-web mini-web-shamrai; do
    if [ "$project" != "$CANON_PROJECT" ]; then
      docker compose -p "$project" down --remove-orphans || true
    fi
  done

  docker ps -aq | while read -r id; do
    [ -n "$id" ] || continue
    name="$(docker inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's#^/##' || true)"
    labels_json="$(docker inspect -f '{{json .Config.Labels}}' "$id" 2>/dev/null || echo '{}')"
    project="$(printf '%s' "$labels_json" | python3 -c "import json,sys; print((json.load(sys.stdin) or {}).get('com.docker.compose.project', ''))" 2>/dev/null || true)"
    if [ "$project" = "$CANON_PROJECT" ]; then
      continue
    fi
    case "$name:$project" in
      *shamrai*|*sports-betting*|*mini-web*|*Mini-Web*)
        echo "Removing stale Shamrai-related container: $name project=$project"
        docker rm -f "$id" || true
        ;;
    esac
  done
fi

conflicts="$(
  docker ps --format '{{.ID}}\t{{.Names}}\t{{.Ports}}' | while IFS="$(printf '\t')" read -r id name ports; do
    labels_json="$(docker inspect -f '{{json .Config.Labels}}' "$id" 2>/dev/null || echo '{}')"
    project="$(printf '%s' "$labels_json" | python3 -c "import json,sys; print((json.load(sys.stdin) or {}).get('com.docker.compose.project', ''))" 2>/dev/null || true)"
    case "$ports" in
      *":$CANON_PORT->"*)
        if [ "$project" != "$CANON_PROJECT" ]; then
          printf '%s\t%s\t%s\t%s\n' "$id" "$name" "$ports" "$project"
        fi
        ;;
    esac
  done
)"
if [ -n "$conflicts" ]; then
  echo "Port $CANON_PORT is owned by a non-canonical container. Do not deploy Shamrai to a new port." >&2
  echo "$conflicts" >&2
  echo "Rerun with -RepairShamraiConflicts only if those containers are stale Shamrai/sports-betting deployments." >&2
  exit 20
fi

if [ -f "$CANON_PATH/.env" ]; then
  current_port="$(grep -E '^FRONTEND_PORT=' "$CANON_PATH/.env" | tail -n 1 | cut -d= -f2- || true)"
  if [ -n "$current_port" ] && [ "$current_port" != "$CANON_PORT" ]; then
    echo "Canonical preview .env had FRONTEND_PORT=$current_port; forcing FRONTEND_PORT=$CANON_PORT."
    python3 - <<'PY'
from pathlib import Path
p = Path("__REMOTE_PATH__/.env")
lines = p.read_text().splitlines() if p.exists() else []
out = []
seen = False
for line in lines:
    if line.startswith("FRONTEND_PORT="):
        out.append("FRONTEND_PORT=8082")
        seen = True
    else:
        out.append(line)
if not seen:
    out.append("FRONTEND_PORT=8082")
p.write_text("\n".join(out) + "\n")
PY
  fi
fi
'@

  return $template.
    Replace("__COMPOSE_PROJECT__", $ComposeProject).
    Replace("__REMOTE_PATH__", $RemotePath).
    Replace("__REPAIR__", $repairValue)
}

if (-not (Test-Path $Workspace)) {
  throw "Workspace not found: $Workspace"
}

$plink = Find-Tool @("C:\Program Files\PuTTY\plink.exe", "plink.exe")
$pscp = Find-Tool @("C:\Program Files\PuTTY\pscp.exe", "pscp.exe")
if (-not $plink -or -not $pscp) {
  throw "PuTTY plink/pscp not found."
}

$password = $env:SHAMRAI_SSH_PASSWORD
if ([string]::IsNullOrWhiteSpace($password)) {
  throw "Set SHAMRAI_SSH_PASSWORD for this run. Do not store it in files."
}

function Invoke-RemoteSh {
  param([string]$Script)
  $normalizedScript = ((($Script -replace "`r`n", "`n") -replace "`r", "").TrimEnd("`n")) + "`n"
  $encodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($normalizedScript))
  $remoteCommand = "printf '%s' '$encodedScript' | base64 -d | sh"
  & $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey $remoteCommand
}

Invoke-Step "Server port/project guard" {
  $guardScript = New-ShamraiServerGuardScript -Repair:$RepairShamraiConflicts
  Invoke-RemoteSh -Script $guardScript
}

$deployDir = Join-Path $Workspace ".deploy"
New-Item -ItemType Directory -Force -Path $deployDir | Out-Null

$services = @()
if ($Target -eq "backend" -or $Target -eq "all") { $services += "backend" }
if ($Target -eq "frontend" -or $Target -eq "all") { $services += "frontend" }

if (-not $SkipChecks) {
  if ($Target -eq "backend" -or $Target -eq "all") {
    Invoke-Step "Backend compile/import" {
      Push-Location (Join-Path $Workspace "backend")
      try {
        $python = Join-Path $PWD ".venv\Scripts\python.exe"
        if (-not (Test-Path $python)) { $python = "python" }
        & $python -m compileall -q src alembic
        & $python -c "import src.main; print('backend_import_ok')"
      } finally {
        Pop-Location
      }
    }
  }

  if ($Target -eq "frontend" -or $Target -eq "all") {
    Invoke-Step "Frontend build" {
      Push-Location (Join-Path $Workspace "frontend")
      try { npm run build } finally { Pop-Location }
    }
  }
}

foreach ($service in $services) {
  $archive = Join-Path $deployDir "shamrai-$service-fast.tar.gz"
  if (Test-Path $archive) {
    Remove-Item -LiteralPath $archive -Force
  }

  Invoke-Step "Pack $service" {
    Push-Location $Workspace
    try {
      if ($service -eq "backend") {
        tar `
          --exclude="./backend/.venv" `
          --exclude="*/__pycache__" `
          --exclude="*.pyc" `
          --exclude="./backend/.env" `
          --exclude="./backend/.env.local" `
          -czf $archive backend
      } else {
        tar `
          --exclude="./frontend/node_modules" `
          --exclude="./frontend/dist" `
          --exclude="./frontend/.vite" `
          --exclude="./frontend/.env" `
          --exclude="./frontend/.env.local" `
          -czf $archive frontend
      }
    } finally {
      Pop-Location
    }
  }

  Invoke-Step "Upload $service archive" {
    & $pscp -batch -pw $password -hostkey $HostKey $archive "${Server}:/tmp/shamrai-$service-fast.tar.gz"
  }

  Invoke-Step "Extract $service on server" {
    $remote = @"
set -e
cd '$RemotePath'
tar -xzf /tmp/shamrai-$service-fast.tar.gz -C '$RemotePath'
rm -f '$RemotePath/$service/.env.local'
"@
    if ($service -eq "backend") {
      $remote += "`nrm -f '$RemotePath/backend/.env.local'"
    } else {
      $remote += "`nrm -f '$RemotePath/frontend/.env' '$RemotePath/frontend/.env.local'"
    }
    Invoke-RemoteSh -Script $remote
  }
}

Invoke-Step "Upload compose file" {
  & $pscp -batch -pw $password -hostkey $HostKey (Join-Path $Workspace "docker-compose.yml") "${Server}:/tmp/shamrai-docker-compose.yml"
  $remote = @"
set -e
install -m 0644 /tmp/shamrai-docker-compose.yml '$RemotePath/docker-compose.yml'
"@
  Invoke-RemoteSh -Script $remote
}

Invoke-Step "Patch server runtime config" {
  $remote = @"
set -e
cd '$RemotePath'
python3 - .env backend/.env <<'PY'
from pathlib import Path
import sys

def patch_env(path, values):
    p = Path(path)
    lines = p.read_text().splitlines() if p.exists() else []
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
    p.write_text("\n".join(out).rstrip() + "\n")

patch_env(Path(sys.argv[1]), {
    "APP_ENV": "local",
    "DEBUG_MODE": "false",
    "ALLOW_DEBUG_AUTH_BYPASS": "false",
    "FRONTEND_PORT": "8082",
    "VITE_API_URL": "",
    "VITE_ENABLE_DEBUG_AUTH": "false",
    "VITE_VK_ID_APP_ID": "54626979",
    "VITE_VK_ID_REDIRECT_URI": "https://shamra1.pro",
    "VITE_VK_GROUP_ID": "239419819",
    "VITE_TELEGRAM_BOT_USERNAME": "Shamra1_bot",
})
patch_env(Path(sys.argv[2]), {
    "DEBUG_MODE": "false",
    "ALLOW_DEBUG_AUTH_BYPASS": "false",
    "TELEGRAM_USE_POLLING": "true",
    "TELEGRAM_START_RESPONSE_TIMEOUT_SECONDS": "4.0",
    "TELEGRAM_WEBHOOK_IP_ADDRESS": "82.147.67.245",
    "API_BASE_URL": "https://shamra1.pro",
    "FRONTEND_BASE_URL": "https://shamra1.pro/app",
    "CORS_ALLOWED_ORIGINS": "https://shamra1.pro,https://www.shamra1.pro",
    "VK_ID_APP_ID": "54626979",
    "VK_ID_REDIRECT_URI": "https://shamra1.pro",
})
PY
"@
  Invoke-RemoteSh -Script $remote
}

Invoke-Step "Docker compose rebuild: $($services -join ', ')" {
  $serviceArgs = $services -join " "
  $remote = @"
set -e
cd '$RemotePath'
docker compose -p '$ComposeProject' up -d --build $serviceArgs
"@
  Invoke-RemoteSh -Script $remote
}

if ($Target -eq "backend" -or $Target -eq "all") {
  Invoke-Step "Reset Telegram delivery state" {
    $remote = @"
set -e
cd '$RemotePath'
docker compose -p '$ComposeProject' exec -T backend python - <<'PY'
import re
import time

from src.api.payments import call_telegram_api
from src.core.config import settings

if not settings.has_real_telegram_token:
    raise SystemExit("Telegram bot token is not configured")

if settings.TELEGRAM_USE_POLLING:
    result = call_telegram_api(
        "deleteWebhook",
        {"drop_pending_updates": False},
        settings.TELEGRAM_API_TIMEOUT_SECONDS,
        1,
    )
    if not result.get("ok"):
        raise SystemExit(f"Telegram deleteWebhook failed: {result.get('description', 'unknown error')}")
    print("telegram_polling_delivery_ok")
    raise SystemExit(0)

webhook_base = settings.API_BASE_URL.strip() or settings.FRONTEND_BASE_URL.strip()
webhook_url = f"{webhook_base.rstrip('/')}/api/telegram/webhook"
payload = {
    "url": webhook_url,
    "drop_pending_updates": True,
    "max_connections": 100,
    "allowed_updates": ["message", "callback_query", "pre_checkout_query"],
}
if settings.TELEGRAM_WEBHOOK_SECRET_TOKEN:
    payload["secret_token"] = settings.TELEGRAM_WEBHOOK_SECRET_TOKEN

result = {}
for attempt in range(1, 4):
    result = call_telegram_api("setWebhook", payload, settings.TELEGRAM_API_TIMEOUT_SECONDS, 1)
    if result.get("ok"):
        break
    description = result.get("description", "unknown error")
    retry_match = re.search(r"retry after\s+(\d+)", description, re.IGNORECASE)
    if attempt >= 3 or not retry_match:
        raise SystemExit(f"Telegram setWebhook failed: {description}")
    time.sleep(max(1, int(retry_match.group(1))) + 1)

print("telegram_webhook_reset_ok")
PY
"@
    Invoke-RemoteSh -Script $remote
  }
}

Invoke-Step "Health verification" {
  $remote = @"
set -e
cd '$RemotePath'
docker compose -p '$ComposeProject' ps
curl -fsS http://127.0.0.1:8082/api/health
curl -fsS http://127.0.0.1:8082/api/health/telegram
curl -fsS -I http://127.0.0.1:8082/ | head -n 8
"@
  Invoke-RemoteSh -Script $remote
}

Write-Host ""
Write-Host "Fast redeploy finished: $Target" -ForegroundColor Green
