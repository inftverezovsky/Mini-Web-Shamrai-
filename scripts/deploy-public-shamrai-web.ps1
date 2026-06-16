param(
  [string]$Workspace = "C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)",
  [string]$Server = "root@82.147.67.245",
  [string]$HostKey = "ssh-ed25519 255 SHA256:xdVRtRaXWqK6eAIsE3VwD0o2H6GJDcCm65L1ZUBjuMw",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [string]$PublicWebRoot = "/var/www/shamrai_web/dist",
  [string]$VkGroupId = "239419819",
  [string]$ExpectedVkCallbackConfirmationCode = $env:SHAMRAI_EXPECTED_VK_CALLBACK_CONFIRMATION_CODE,
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

function ConvertTo-ShellSingleQuoted {
  param([string]$Value)
  if ($null -eq $Value) { $Value = "" }
  $singleQuote = [char]39
  $doubleQuote = [char]34
  $escaped = $Value.Replace(
    "$singleQuote",
    "$singleQuote$doubleQuote$singleQuote$doubleQuote$singleQuote"
  )
  return "$singleQuote$escaped$singleQuote"
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

find_compose_workdir() {
  target_project="$1"
  docker ps -aq --filter "label=com.docker.compose.project=$target_project" | while read -r id; do
    [ -n "$id" ] || continue
    labels_json="$(docker inspect -f '{{json .Config.Labels}}' "$id" 2>/dev/null || echo '{}')"
    workdir="$(printf '%s' "$labels_json" | python3 -c "import json,sys; print((json.load(sys.stdin) or {}).get('com.docker.compose.project.working_dir', ''))" 2>/dev/null || true)"
    if [ -n "$workdir" ]; then
      printf '%s\n' "$workdir"
      break
    fi
  done
}

stop_stale_compose_project() {
  stale_project="$1"
  if [ "$stale_project" = "$CANON_PROJECT" ]; then
    return 0
  fi
  workdir="$(find_compose_workdir "$stale_project" | head -n 1)"
  if [ -n "$workdir" ] && [ -d "$workdir" ]; then
    echo "Stopping stale compose project $stale_project from $workdir"
    docker compose --project-directory "$workdir" -p "$stale_project" down --remove-orphans || true
    return 0
  fi
  docker compose -p "$stale_project" down --remove-orphans || true
}

if [ "$REPAIR_SHAMRAI_CONFLICTS" = "1" ]; then
  for project in sports-betting shamrai-preview shamrai-mini shamrai-mini-app mini-web mini-web-shamrai; do
    stop_stale_compose_project "$project"
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

listeners_8082="$(ss -ltnp | awk -v port="$CANON_PORT" 'NR > 1 { n=split($4, parts, ":"); if (parts[n] == port) print }' || true)"
canonical_8082="$(
  docker ps --filter "label=com.docker.compose.project=$CANON_PROJECT" --format '{{.Ports}}' | grep -F ":$CANON_PORT->" || true
)"
if [ -n "$listeners_8082" ] && [ -z "$canonical_8082" ]; then
  echo "Port $CANON_PORT already has a listener, but it is not owned by the canonical Docker project." >&2
  echo "$listeners_8082" >&2
  exit 21
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
$remoteRollbackScript = Join-Path $deployDir "shamrai-public-remote-rollback.sh"

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
      VITE_PLAUSIBLE_DOMAIN = $env:VITE_PLAUSIBLE_DOMAIN
      VITE_PLAUSIBLE_ENDPOINT = $env:VITE_PLAUSIBLE_ENDPOINT
      VITE_PLAUSIBLE_CAPTURE_LOCALHOST = $env:VITE_PLAUSIBLE_CAPTURE_LOCALHOST
    }
    try {
      $env:VITE_API_URL = ""
      $env:VITE_ENABLE_DEBUG_AUTH = "false"
      $env:VITE_VK_ID_APP_ID = "54626979"
      $env:VITE_VK_ID_REDIRECT_URI = "https://shamra1.pro"
      $env:VITE_VK_GROUP_ID = $VkGroupId
      $env:VITE_TELEGRAM_BOT_USERNAME = "Shamra1_bot"
      if ($null -eq $env:VITE_PLAUSIBLE_DOMAIN) { $env:VITE_PLAUSIBLE_DOMAIN = "shamra1.pro" }
      if ($null -eq $env:VITE_PLAUSIBLE_ENDPOINT) { $env:VITE_PLAUSIBLE_ENDPOINT = "" }
      if ($null -eq $env:VITE_PLAUSIBLE_CAPTURE_LOCALHOST) { $env:VITE_PLAUSIBLE_CAPTURE_LOCALHOST = "false" }
      Invoke-NativeChecked "npm" "run" "build"
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
    try { Invoke-NativeChecked "python" "-m" "compileall" "-q" "src" "alembic" } finally { Pop-Location }
  }
}

Invoke-Step "Create deployment archives" {
  New-Item -ItemType Directory -Force -Path $deployDir | Out-Null
  Remove-Item -LiteralPath $repoArchive, $webArchive -Force -ErrorAction SilentlyContinue

  Push-Location $Workspace
  try {
    Invoke-NativeChecked "tar" `
      "--exclude=./.git" `
      "--exclude=./.deploy" `
      "--exclude=./.env" `
      "--exclude=./.env.local" `
      "--exclude=./.playwright-cli" `
      "--exclude=./deploy.tar.gz" `
      "--exclude=./remote.py" `
      "--exclude=./test_proxies.py" `
      "--exclude=./output" `
      "--exclude=./frontend/node_modules" `
      "--exclude=./frontend/dist" `
      "--exclude=./frontend/.vite" `
      "--exclude=./frontend/.env" `
      "--exclude=./frontend/.env.local" `
      "--exclude=./backend/.venv" `
      "--exclude=*/__pycache__" `
      "--exclude=*.pyc" `
      "--exclude=*.pem" `
      "--exclude=./backend/.env" `
      "--exclude=./backend/.env.local" `
      "--exclude=./backend/static/coupons" `
      "--exclude=./backend/static/coupons/*" `
      "-czf" $repoArchive "."
  } finally {
    Pop-Location
  }

  Push-Location (Join-Path $Workspace "frontend\dist")
  try {
    Invoke-NativeChecked "tar" "-czf" $webArchive "."
  } finally {
    Pop-Location
  }
}

Invoke-Step "Create remote scripts" {
  $remoteGuardContent = New-ServerGuardScript -Repair:$RepairShamraiConflicts
  Set-Utf8NoBomLfContent -Path $remoteGuardScript -Content $remoteGuardContent

  $remoteDeployContent = @"
set -e
deploy_id="`$(date +%Y%m%d%H%M%S)"
stage_path="$RemotePath.stage.`$deploy_id"
backup_path="$RemotePath.rollback.`$deploy_id"
failed_path="$RemotePath.failed.`$deploy_id"
backup_marker="/tmp/shamrai-public-code-backup.path"
db_backup_dir="$RemotePath.db-backups"
db_backup_path="`$db_backup_dir/shamrai-db.`$deploy_id.dump"
rm -rf "`$stage_path"
rm -f "`$backup_marker"
mkdir -p "`$stage_path"
tar -xzf /tmp/shamrai-public-repo.tar.gz -C "`$stage_path"
if [ -f "$RemotePath/.env" ]; then
  install -m 0600 "$RemotePath/.env" "`$stage_path/.env"
elif [ -f "`$stage_path/.env.example" ]; then
  install -m 0600 "`$stage_path/.env.example" "`$stage_path/.env"
else
  echo "Missing root runtime env and .env.example fallback." >&2
  exit 20
fi
if [ -f "$RemotePath/backend/.env" ]; then
  install -m 0600 "$RemotePath/backend/.env" "`$stage_path/backend/.env"
elif [ -f "`$stage_path/backend/.env.example" ]; then
  install -m 0600 "`$stage_path/backend/.env.example" "`$stage_path/backend/.env"
else
  echo "Missing backend runtime env and backend/.env.example fallback." >&2
  exit 21
fi
python3 - "`$stage_path/.env" "`$stage_path/backend/.env" <<'PY'
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
    "APP_ENV": "production",
    "DEBUG_MODE": "false",
    "ALLOW_DEBUG_AUTH_BYPASS": "false",
    "FRONTEND_PORT": "8082",
    "VITE_API_URL": "",
    "VITE_ENABLE_DEBUG_AUTH": "false",
    "VITE_VK_ID_APP_ID": "54626979",
    "VITE_VK_ID_REDIRECT_URI": "https://shamra1.pro",
    "VITE_VK_GROUP_ID": "$VkGroupId",
    "VITE_TELEGRAM_BOT_USERNAME": "Shamra1_bot",
    "VITE_PLAUSIBLE_DOMAIN": "shamra1.pro",
    "VITE_PLAUSIBLE_ENDPOINT": "",
    "VITE_PLAUSIBLE_CAPTURE_LOCALHOST": "false",
})
patch_env(backend_env, {
    "DEBUG_MODE": "false",
    "ALLOW_DEBUG_AUTH_BYPASS": "false",
    "TELEGRAM_USE_POLLING": "true",
    "TELEGRAM_START_RESPONSE_TIMEOUT_SECONDS": "4.0",
    "TELEGRAM_WEBHOOK_IP_ADDRESS": "",
    "VK_DIALOG_POLLING_ENABLED": "true",
    "VK_DIALOG_POLLING_INTERVAL_SECONDS": "1.0",
    "VK_DIALOG_POLLING_BATCH_SIZE": "20",
    "CORS_ALLOWED_ORIGINS": "https://shamra1.pro,https://www.shamra1.pro",
    "API_BASE_URL": "https://shamra1.pro",
    "FRONTEND_BASE_URL": "https://shamra1.pro/app",
    "YOOKASSA_RETURN_URL": "https://shamra1.pro/app",
    "VK_ID_APP_ID": "54626979",
    "VK_ID_REDIRECT_URI": "https://shamra1.pro",
})
PY

cd "`$stage_path"
docker compose -p "$ComposeProject" config -q

rollback_code() {
  status="`$?"
  echo "Preview deploy failed; rolling back code snapshot." >&2
  if [ -s "`$db_backup_path" ]; then
    echo "Database backup is available at `$db_backup_path" >&2
  fi
  if [ -d "$RemotePath" ]; then
    rm -rf "`$failed_path"
    mv "$RemotePath" "`$failed_path" || true
  fi
  if [ -d "`$backup_path" ]; then
    mv "`$backup_path" "$RemotePath"
    cd "$RemotePath"
    docker compose -p "$ComposeProject" up -d --build || true
  fi
  exit "`$status"
}

mkdir -p "`$db_backup_dir"
chmod 0700 "`$db_backup_dir"
if [ -d "$RemotePath" ] && [ -f "$RemotePath/docker-compose.yml" ]; then
  (
    cd "$RemotePath"
    if docker compose -p "$ComposeProject" ps -q postgres >/dev/null 2>&1; then
      docker compose -p "$ComposeProject" exec -T postgres sh -c 'pg_dump -U "`$POSTGRES_USER" -d "`$POSTGRES_DB" -Fc' > "`$db_backup_path"
      chmod 0600 "`$db_backup_path"
    fi
  ) || {
    echo "Database backup failed; aborting deploy before migrations." >&2
    exit 22
  }
fi

if [ -d "$RemotePath" ]; then
  mv "$RemotePath" "`$backup_path"
  printf '%s\n' "`$backup_path" > "`$backup_marker"
fi
mv "`$stage_path" "$RemotePath"
trap rollback_code ERR
cd "$RemotePath"
docker compose -p "$ComposeProject" up -d postgres
docker compose -p "$ComposeProject" build backend frontend
backend_static_volume="${ComposeProject}_backend_static"
docker volume inspect "`$backend_static_volume" >/dev/null 2>&1 || docker volume create "`$backend_static_volume" >/dev/null
docker run --rm -v "`$backend_static_volume:/target" alpine:3.20 sh -c 'mkdir -p /target/coupons && chown -R 10001:10001 /target'
docker compose -p "$ComposeProject" run --rm backend alembic upgrade head
docker compose -p "$ComposeProject" up -d
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if curl -fsS http://127.0.0.1:8082/api/health; then
    break
  fi
  if [ "`$attempt" = "15" ]; then
    exit 1
  fi
  sleep 1
done
trap - ERR
"@
  Set-Utf8NoBomLfContent -Path $remoteDeployScript -Content $remoteDeployContent

  $remotePublishContent = @"
set -e
deploy_id="`$(date +%Y%m%d%H%M%S)"
web_stage="$PublicWebRoot.stage.`$deploy_id"
web_backup="$PublicWebRoot.rollback.`$deploy_id"
web_failed="$PublicWebRoot.failed.`$deploy_id"
nginx_backup="/tmp/shamrai-public-nginx-backup.`$deploy_id"
web_backup_marker="/tmp/shamrai-public-web-backup.path"
nginx_backup_marker="/tmp/shamrai-public-nginx-backup.path"
rm -rf "`$web_stage" "`$nginx_backup"
rm -f "`$web_backup_marker" "`$nginx_backup_marker"
mkdir -p "`$(dirname "$PublicWebRoot")" "`$web_stage" "`$nginx_backup"
tar -xzf /tmp/shamrai-web-dist.tar.gz -C "`$web_stage"

restore_public() {
  status="`$?"
  echo "Public publish failed; rolling back web root and nginx snapshot." >&2
  if [ -e "$PublicWebRoot" ]; then
    rm -rf "`$web_failed"
    mv "$PublicWebRoot" "`$web_failed" || true
  fi
  if [ -d "`$web_backup" ]; then
    mv "`$web_backup" "$PublicWebRoot"
  fi
  if [ -f "`$nginx_backup/shamrai.conf" ]; then
    cp -a "`$nginx_backup/shamrai.conf" /etc/nginx/sites-available/shamrai.conf
  else
    rm -f /etc/nginx/sites-available/shamrai.conf
  fi
  rm -f /etc/nginx/sites-enabled/shamrai.conf
  if [ -f "`$nginx_backup/enabled-manifest" ]; then
    while IFS="`$(printf '\t')" read -r kind name target; do
      [ -n "`$name" ] || continue
      rm -f "/etc/nginx/sites-enabled/`$name"
      if [ "`$kind" = "symlink" ]; then
        ln -s "`$target" "/etc/nginx/sites-enabled/`$name"
      elif [ "`$kind" = "file" ]; then
        cp -a "`$nginx_backup/enabled-`$name" "/etc/nginx/sites-enabled/`$name"
      fi
    done < "`$nginx_backup/enabled-manifest"
  fi
  nginx -t && systemctl reload nginx || true
  exit "`$status"
}

trap restore_public ERR

if [ -e "$PublicWebRoot" ]; then
  mv "$PublicWebRoot" "`$web_backup"
  printf '%s\n' "`$web_backup" > "`$web_backup_marker"
fi
if [ -f /etc/nginx/sites-available/shamrai.conf ]; then
  cp -a /etc/nginx/sites-available/shamrai.conf "`$nginx_backup/shamrai.conf"
fi
for enabled in /etc/nginx/sites-enabled/*; do
  [ -e "`$enabled" ] || continue
  if grep -qE 'server_name .*shamra1\.pro' "`$enabled"; then
    enabled_name="`$(basename "`$enabled")"
    if [ -L "`$enabled" ]; then
      printf 'symlink\t%s\t%s\n' "`$enabled_name" "`$(readlink "`$enabled")" >> "`$nginx_backup/enabled-manifest"
    else
      cp -a "`$enabled" "`$nginx_backup/enabled-`$enabled_name"
      printf 'file\t%s\t\n' "`$enabled_name" >> "`$nginx_backup/enabled-manifest"
    fi
  fi
done
printf '%s\n' "`$nginx_backup" > "`$nginx_backup_marker"

mv "`$web_stage" "$PublicWebRoot"
install -m 0644 /tmp/shamrai.conf /etc/nginx/sites-available/shamrai.conf
for enabled in /etc/nginx/sites-enabled/*; do
  [ -e "`$enabled" ] || continue
  if grep -qE 'server_name .*shamra1\.pro' "`$enabled"; then
    rm -f "`$enabled"
  fi
done
rm -f /etc/nginx/sites-enabled/shamrai.conf
ln -s /etc/nginx/sites-available/shamrai.conf /etc/nginx/sites-enabled/shamrai.conf
nginx -t
systemctl reload nginx
curl -fsS -I https://shamra1.pro/app/ | head -n 8
trap - ERR
"@
  Set-Utf8NoBomLfContent -Path $remotePublishScript -Content $remotePublishContent

  $remoteRollbackContent = @"
set -e
code_backup_marker="/tmp/shamrai-public-code-backup.path"
web_backup_marker="/tmp/shamrai-public-web-backup.path"
nginx_backup_marker="/tmp/shamrai-public-nginx-backup.path"

if [ -f "`$code_backup_marker" ]; then
  code_backup="`$(cat "`$code_backup_marker")"
  if [ -d "`$code_backup" ]; then
    failed_path="$RemotePath.failed.manual.`$(date +%Y%m%d%H%M%S)"
    [ -d "$RemotePath" ] && mv "$RemotePath" "`$failed_path" || true
    mv "`$code_backup" "$RemotePath"
    cd "$RemotePath"
    docker compose -p "$ComposeProject" up -d --build
  fi
fi

if [ -f "`$web_backup_marker" ]; then
  web_backup="`$(cat "`$web_backup_marker")"
  if [ -d "`$web_backup" ]; then
    web_failed="$PublicWebRoot.failed.manual.`$(date +%Y%m%d%H%M%S)"
    [ -e "$PublicWebRoot" ] && mv "$PublicWebRoot" "`$web_failed" || true
    mv "`$web_backup" "$PublicWebRoot"
  fi
fi

if [ -f "`$nginx_backup_marker" ]; then
  nginx_backup="`$(cat "`$nginx_backup_marker")"
  if [ -f "`$nginx_backup/shamrai.conf" ]; then
    cp -a "`$nginx_backup/shamrai.conf" /etc/nginx/sites-available/shamrai.conf
  else
    rm -f /etc/nginx/sites-available/shamrai.conf
  fi
  rm -f /etc/nginx/sites-enabled/shamrai.conf
  if [ -f "`$nginx_backup/enabled-manifest" ]; then
    while IFS="`$(printf '\t')" read -r kind name target; do
      [ -n "`$name" ] || continue
      rm -f "/etc/nginx/sites-enabled/`$name"
      if [ "`$kind" = "symlink" ]; then
        ln -s "`$target" "/etc/nginx/sites-enabled/`$name"
      elif [ "`$kind" = "file" ]; then
        cp -a "`$nginx_backup/enabled-`$name" "/etc/nginx/sites-enabled/`$name"
      fi
    done < "`$nginx_backup/enabled-manifest"
  fi
  nginx -t
  systemctl reload nginx
fi

curl -fsS http://127.0.0.1:8082/api/health
"@
  Set-Utf8NoBomLfContent -Path $remoteRollbackScript -Content $remoteRollbackContent
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
  Invoke-NativeChecked $pscp -batch -pw $password -hostkey $HostKey $remoteRollbackScript "${Server}:/tmp/shamrai-public-remote-rollback.sh"
}

try {
  Invoke-Step "Deploy code through staging snapshot" {
    Invoke-NativeChecked $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey "bash /tmp/shamrai-public-remote-deploy.sh"
  }

  Invoke-Step "Publish host static web with rollback snapshot" {
    Invoke-NativeChecked $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey "bash /tmp/shamrai-public-remote-publish.sh"
  }

  Invoke-Step "Health verification" {
    $expectedVkCode = ConvertTo-ShellSingleQuoted $ExpectedVkCallbackConfirmationCode
    if ($VkGroupId -notmatch "^\d+$") {
      throw "VK group id must be numeric for deploy verification."
    }
    $vkCallbackPayload = ConvertTo-ShellSingleQuoted ('{"type":"confirmation","group_id":' + $VkGroupId + '}')
    $remote = @"
set -e
cd '$RemotePath'
docker compose -p '$ComposeProject' ps
curl -fsS http://127.0.0.1:8082/api/health
curl -fsS http://127.0.0.1:8082/api/health/telegram
curl -fsS http://127.0.0.1:8082/api/health/vk
curl -fsS http://127.0.0.1:8082/api/health/vk/deep
expected_vk_callback_confirmation=$expectedVkCode
vk_callback_payload=$vkCallbackPayload
runtime_vk_callback_confirmation="`$(python3 - <<'PY'
from pathlib import Path
value = ""
path = Path("backend/.env")
if path.exists():
    for line in path.read_text().splitlines():
        if line.startswith("VK_CALLBACK_CONFIRMATION_CODE="):
            value = line.split("=", 1)[1].strip()
print(value)
PY
)"
if [ -z "`$runtime_vk_callback_confirmation" ]; then
  echo 'VK callback confirmation code is missing in backend runtime env.' >&2
  exit 30
fi
actual_runtime_vk_callback_confirmation="`$(curl -fsS -X POST -H 'Content-Type: application/json' --data "`$vk_callback_payload" https://shamra1.pro/api/vk/callback)"
if [ "`$actual_runtime_vk_callback_confirmation" != "`$runtime_vk_callback_confirmation" ]; then
  echo 'VK callback runtime confirmation mismatch.' >&2
  exit 30
fi
echo 'vk_callback_runtime_confirmation_ok'
if [ -n "`$expected_vk_callback_confirmation" ]; then
  actual_vk_callback_confirmation="`$actual_runtime_vk_callback_confirmation"
  if [ "`$actual_vk_callback_confirmation" != "`$expected_vk_callback_confirmation" ]; then
    echo 'VK callback confirmation mismatch.' >&2
    exit 31
  fi
  echo 'vk_callback_dashboard_confirmation_ok'
else
  echo 'WARNING: VK dashboard confirmation was not verified because expected code was not provided.' >&2
fi
curl -I -fsS http://shamra1.pro/ | head -n 8
curl -I -fsS https://shamra1.pro/ | head -n 8
curl -I -fsS https://shamra1.pro/app/ | head -n 8
"@
    $normalizedRemote = ((($remote -replace "`r`n", "`n") -replace "`r", "").TrimEnd("`n")) + "`n"
    $encodedRemote = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($normalizedRemote))
    $remoteCommand = "printf '%s' '$encodedRemote' | base64 -d | bash"
    Invoke-NativeChecked $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey $remoteCommand
  }
} catch {
  Write-Warning "Deploy verification failed; attempting remote rollback."
  try {
    Invoke-NativeChecked $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey "bash /tmp/shamrai-public-remote-rollback.sh"
  } catch {
    Write-Warning "Remote rollback command also failed. Manual server inspection is required."
  }
  throw
}

Write-Host ""
Write-Host "Public Shamrai web deploy finished." -ForegroundColor Green
Write-Host "Site: https://shamra1.pro/"
Write-Host "Mini App: https://shamra1.pro/app"
