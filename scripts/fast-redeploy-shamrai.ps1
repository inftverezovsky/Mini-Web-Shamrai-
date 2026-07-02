param(
  [ValidateSet("backend", "frontend", "all")]
  [string]$Target = "all",
  [string]$Workspace = "C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)",
  [string]$Server = "root@82.147.67.245",
  [string]$HostKey = "ssh-ed25519 255 SHA256:xdVRtRaXWqK6eAIsE3VwD0o2H6GJDcCm65L1ZUBjuMw",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [string]$PublicWebRoot = "/var/www/shamrai_web/dist",
  [string]$VkGroupId = "239419819",
  [string]$ExpectedVkCallbackConfirmationCode = $env:SHAMRAI_EXPECTED_VK_CALLBACK_CONFIRMATION_CODE,
  [string]$SshKeyPath = $env:SHAMRAI_SSH_KEY_PATH,
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

function Get-FrontendDistAssets {
  param([string]$DistPath)

  $indexPath = Join-Path $DistPath "index.html"
  if (-not (Test-Path -LiteralPath $indexPath)) {
    throw "Frontend dist index was not found: $indexPath"
  }

  $html = Get-Content -Raw -LiteralPath $indexPath
  $matches = [regex]::Matches($html, "/?assets/[^`"'<>\s]+\.(?:css|js)")
  $assets = @(
    foreach ($match in $matches) {
      $match.Value.TrimStart("/")
    }
  ) | Sort-Object -Unique

  if ($assets.Count -eq 0) {
    throw "Frontend dist index has no CSS/JS assets: $indexPath"
  }

  return $assets
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

$defaultSshKeyPath = Join-Path $env:USERPROFILE ".ssh\codex_deploy_ed25519"
if ([string]::IsNullOrWhiteSpace($SshKeyPath) -and (Test-Path -LiteralPath $defaultSshKeyPath)) {
  $SshKeyPath = $defaultSshKeyPath
}

$ssh = Find-Tool @("ssh.exe", "ssh")
$scp = Find-Tool @("scp.exe", "scp")
$useSshKey = (-not [string]::IsNullOrWhiteSpace($SshKeyPath)) -and (Test-Path -LiteralPath $SshKeyPath)
$plink = $null
$pscp = $null
$password = $null

if ($useSshKey) {
  if (-not $ssh -or -not $scp) {
    throw "OpenSSH ssh/scp not found. Install OpenSSH Client or provide SHAMRAI_SSH_PASSWORD."
  }
} else {
  $plink = Find-Tool @("C:\Program Files\PuTTY\plink.exe", "plink.exe")
  $pscp = Find-Tool @("C:\Program Files\PuTTY\pscp.exe", "pscp.exe")
  if (-not $plink -or -not $pscp) {
    throw "PuTTY plink/pscp not found."
  }

  $password = $env:SHAMRAI_SSH_PASSWORD
  if ([string]::IsNullOrWhiteSpace($password)) {
    throw "Set SHAMRAI_SSH_PASSWORD for this run or provide a valid SshKeyPath. Do not store passwords in files."
  }
}

function Invoke-RemoteChecked {
  param([Parameter(Mandatory = $true)][string]$Command)

  if ($useSshKey) {
    Invoke-NativeChecked $ssh `
      "-i" $SshKeyPath `
      "-o" "BatchMode=yes" `
      "-o" "IdentitiesOnly=yes" `
      "-o" "StrictHostKeyChecking=accept-new" `
      $Server `
      $Command
    return
  }

  Invoke-NativeChecked $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey $Command
}

function Copy-ToRemoteChecked {
  param(
    [Parameter(Mandatory = $true)][string]$LocalPath,
    [Parameter(Mandatory = $true)][string]$RemotePath
  )

  $target = "${Server}:$RemotePath"
  if ($useSshKey) {
    Invoke-NativeChecked $scp `
      "-i" $SshKeyPath `
      "-o" "BatchMode=yes" `
      "-o" "IdentitiesOnly=yes" `
      "-o" "StrictHostKeyChecking=accept-new" `
      $LocalPath `
      $target
    return
  }

  Invoke-NativeChecked $pscp -batch -pw $password -hostkey $HostKey $LocalPath $target
}

function Invoke-RemoteSh {
  param([string]$Script)
  $normalizedScript = ((($Script -replace "`r`n", "`n") -replace "`r", "").TrimEnd("`n")) + "`n"
  $encodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($normalizedScript))
  $remoteCommand = "printf '%s' '$encodedScript' | base64 -d | bash"
  Invoke-RemoteChecked $remoteCommand
}

Invoke-Step "Server port/project guard" {
  $guardScript = New-ShamraiServerGuardScript -Repair:$RepairShamraiConflicts
  Invoke-RemoteSh -Script $guardScript
}

$deployDir = Join-Path $Workspace ".deploy"
New-Item -ItemType Directory -Force -Path $deployDir | Out-Null
$deployId = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
$frontendDistPath = Join-Path $Workspace "frontend\dist"
$webArchive = Join-Path $deployDir "shamrai-web-dist-fast.tar.gz"
$script:frontendPublicAssets = @()
$publishPublicFrontend = $Target -eq "frontend" -or $Target -eq "all"

$services = @()
if ($Target -eq "backend" -or $Target -eq "all") { $services += "backend" }
if ($Target -eq "frontend" -or $Target -eq "all") { $services += "frontend" }

$rollbackArmed = $false

function Invoke-FastRollback {
  param([string[]]$RollbackServices)

  $serviceArgs = $RollbackServices -join " "
  if ([string]::IsNullOrWhiteSpace($serviceArgs)) {
    return
  }

  $remote = @"
set -e
backup_root='$RemotePath/.deploy-backups/fast-$deployId'
cd '$RemotePath'
if [ ! -d "`$backup_root" ]; then
  echo "Fast redeploy backup root was not found: `$backup_root" >&2
  exit 41
fi
for service in $serviceArgs; do
  if [ -d "`$backup_root/`$service" ]; then
    rm -rf "$RemotePath/`$service"
    cp -a "`$backup_root/`$service" "$RemotePath/`$service"
  else
    echo "No backup found for `$service; leaving current files in place." >&2
  fi
done
if [ -f "`$backup_root/docker-compose.yml" ]; then
  cp -a "`$backup_root/docker-compose.yml" "$RemotePath/docker-compose.yml"
elif [ -f "`$backup_root/docker-compose.yml.absent" ]; then
  rm -f "$RemotePath/docker-compose.yml"
fi
if [ -f "`$backup_root/root.env" ]; then
  cp -a "`$backup_root/root.env" "$RemotePath/.env"
elif [ -f "`$backup_root/root.env.absent" ]; then
  rm -f "$RemotePath/.env"
fi
if [ -f "`$backup_root/backend.env" ]; then
  mkdir -p "$RemotePath/backend"
  cp -a "`$backup_root/backend.env" "$RemotePath/backend/.env"
elif [ -f "`$backup_root/backend.env.absent" ]; then
  rm -f "$RemotePath/backend/.env"
fi
docker compose -p '$ComposeProject' up -d --build $serviceArgs
curl -fsS http://127.0.0.1:8082/api/health
"@
  Invoke-RemoteSh -Script $remote
}

function Invoke-PublicFrontendRollback {
  $remote = @"
set -e
backup_root='$RemotePath/.deploy-backups/fast-$deployId'
if [ ! -d "`$backup_root/public-web-root" ]; then
  echo "No public web root backup found for fast deploy $deployId; leaving current public files in place." >&2
  exit 0
fi
web_failed='$PublicWebRoot.failed.fast-manual.$deployId'
[ -e '$PublicWebRoot' ] && mv '$PublicWebRoot' "`$web_failed" || true
mv "`$backup_root/public-web-root" '$PublicWebRoot'
nginx -t
systemctl reload nginx
"@
  Invoke-RemoteSh -Script $remote
}

if (-not $SkipChecks) {
  if ($Target -eq "backend" -or $Target -eq "all") {
    Invoke-Step "Backend compile/import" {
      Push-Location (Join-Path $Workspace "backend")
      try {
        $python = Join-Path $PWD ".venv\Scripts\python.exe"
        if (-not (Test-Path $python)) { $python = "python" }
        Invoke-NativeChecked $python "-m" "compileall" "-q" "src" "alembic"
        Invoke-NativeChecked $python "-c" "import src.main; print('backend_import_ok')"
      } finally {
        Pop-Location
      }
    }
  }

  if ($Target -eq "frontend" -or $Target -eq "all") {
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
  }
}

if ($publishPublicFrontend) {
  Invoke-Step "Pack public frontend dist" {
    $script:frontendPublicAssets = @(Get-FrontendDistAssets -DistPath $frontendDistPath)
    if (Test-Path -LiteralPath $webArchive) {
      Remove-Item -LiteralPath $webArchive -Force
    }

    Push-Location $frontendDistPath
    try {
      Invoke-NativeChecked "tar" "-czf" $webArchive "."
    } finally {
      Pop-Location
    }
  }
}

try {
  Invoke-Step "Create fast redeploy server snapshot" {
    $remote = @"
set -e
cd '$RemotePath'
backup_root='$RemotePath/.deploy-backups/fast-$deployId'
rm -rf "`$backup_root"
mkdir -p "`$backup_root"
if [ -f docker-compose.yml ]; then cp -a docker-compose.yml "`$backup_root/docker-compose.yml"; else touch "`$backup_root/docker-compose.yml.absent"; fi
if [ -f .env ]; then cp -a .env "`$backup_root/root.env"; else touch "`$backup_root/root.env.absent"; fi
if [ -f backend/.env ]; then cp -a backend/.env "`$backup_root/backend.env"; else touch "`$backup_root/backend.env.absent"; fi
"@
    Invoke-RemoteSh -Script $remote
  }

  if ($publishPublicFrontend) {
    Invoke-Step "Upload public frontend dist archive" {
      Copy-ToRemoteChecked $webArchive "/tmp/shamrai-web-dist-fast.tar.gz"
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
          Invoke-NativeChecked "tar" `
            "--exclude=./backend/.venv" `
            "--exclude=*/__pycache__" `
            "--exclude=*.pyc" `
            "--exclude=*.pem" `
            "--exclude=./backend/.env" `
            "--exclude=./backend/.env.local" `
            "--exclude=./backend/.env.*" `
            "--exclude=./backend/static/coupons" `
            "--exclude=./backend/static/coupons/*" `
            "-czf" $archive "backend"
        } else {
          Invoke-NativeChecked "tar" `
            "--exclude=./frontend/node_modules" `
            "--exclude=./frontend/dist" `
            "--exclude=./frontend/.vite" `
            "--exclude=./frontend/.env" `
            "--exclude=./frontend/.env.local" `
            "--exclude=./frontend/.env.*" `
            "-czf" $archive "frontend"
        }
      } finally {
        Pop-Location
      }
    }

    Invoke-Step "Upload $service archive" {
      Copy-ToRemoteChecked $archive "/tmp/shamrai-$service-fast.tar.gz"
    }

    Invoke-Step "Stage and swap $service on server" {
      $remote = @"
set -e
cd '$RemotePath'
backup_root='$RemotePath/.deploy-backups/fast-$deployId'
stage_root='$RemotePath/.deploy-stage/fast-$deployId'
mkdir -p "`$backup_root" "`$stage_root"
rm -rf "`$stage_root/$service"
tar -xzf /tmp/shamrai-$service-fast.tar.gz -C "`$stage_root"
test -d "`$stage_root/$service"
if [ -d '$RemotePath/$service' ]; then
  rm -rf "`$backup_root/$service"
  cp -a '$RemotePath/$service' "`$backup_root/$service"
fi
"@
      if ($service -eq "backend") {
        $remote += @"

if [ -f '$RemotePath/backend/.env' ]; then
  cp '$RemotePath/backend/.env' "`$stage_root/backend/.env"
fi
rm -rf '$RemotePath/backend'
mv "`$stage_root/backend" '$RemotePath/backend'
rm -f '$RemotePath/backend/.env.local'
"@
      } else {
        $remote += @"

rm -rf '$RemotePath/frontend'
mv "`$stage_root/frontend" '$RemotePath/frontend'
rm -f '$RemotePath/frontend/.env' '$RemotePath/frontend/.env.local'
"@
      }
      Invoke-RemoteSh -Script $remote
      $rollbackArmed = $true
    }
  }

  Invoke-Step "Upload compose file" {
    Copy-ToRemoteChecked (Join-Path $Workspace "docker-compose.yml") "/tmp/shamrai-docker-compose.yml"
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
                if key not in seen:
                    out.append(f"{key}={values[key]}")
                    seen.add(key)
                continue
        out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
    p.write_text("\n".join(out).rstrip() + "\n")

patch_env(Path(sys.argv[1]), {
    "APP_ENV": "production",
    "DEBUG_MODE": "false",
    "ALLOW_DEBUG_AUTH_BYPASS": "false",
    "FRONTEND_PORT": "8082",
    "VITE_API_URL": "",
    "VITE_ENABLE_DEBUG_AUTH": "false",
    "VITE_VK_ID_APP_ID": "54626979",
    "VITE_VK_ID_REDIRECT_URI": "https://shamra1.pro",
    "VITE_VK_GROUP_ID": "239419819",
    "VITE_TELEGRAM_BOT_USERNAME": "Shamra1_bot",
    "VITE_PLAUSIBLE_DOMAIN": "shamra1.pro",
    "VITE_PLAUSIBLE_ENDPOINT": "",
    "VITE_PLAUSIBLE_CAPTURE_LOCALHOST": "false",
})
patch_env(Path(sys.argv[2]), {
    "DEBUG_MODE": "false",
    "ALLOW_DEBUG_AUTH_BYPASS": "false",
    "TELEGRAM_USE_POLLING": "true",
    "TELEGRAM_START_RESPONSE_TIMEOUT_SECONDS": "4.0",
    "TELEGRAM_WEBHOOK_IP_ADDRESS": "",
    "VK_DIALOG_POLLING_ENABLED": "true",
    "VK_DIALOG_POLLING_INTERVAL_SECONDS": "1.0",
    "VK_DIALOG_POLLING_BATCH_SIZE": "20",
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
    $runBackendMigrations = if ($Target -eq "backend" -or $Target -eq "all") { "1" } else { "0" }
    $remote = @"
set -e
cd '$RemotePath'
docker compose -p '$ComposeProject' config -q
if [ '$runBackendMigrations' = '1' ]; then
  docker compose -p '$ComposeProject' up -d postgres redis
  docker compose -p '$ComposeProject' build $serviceArgs
else
  docker compose -p '$ComposeProject' up -d --build $serviceArgs
fi
"@
    Invoke-RemoteSh -Script $remote
  }

  if ($Target -eq "backend" -or $Target -eq "all") {
    Invoke-Step "Run database migrations" {
      $serviceArgs = $services -join " "
      $remote = @"
set -e
cd '$RemotePath'
db_backup_dir='$RemotePath/.deploy-backups/db'
db_backup_path="`$db_backup_dir/fast-$deployId-before-migrations.dump"
mkdir -p "`$db_backup_dir"
chmod 0700 "`$db_backup_dir"
docker compose -p '$ComposeProject' exec -T postgres sh -c 'pg_dump -U "`$POSTGRES_USER" -d "`$POSTGRES_DB" -Fc' > "`$db_backup_path"
chmod 0600 "`$db_backup_path"
docker compose -p '$ComposeProject' run --rm backend alembic upgrade head
docker compose -p '$ComposeProject' up -d $serviceArgs
"@
      Invoke-RemoteSh -Script $remote
    }

    Invoke-Step "Reset Telegram delivery state" {
      $remote = @"
set -e
cd '$RemotePath'
docker compose -p '$ComposeProject' exec -T backend python - <<'PY'
import re
import time

from src.core.config import settings
from src.services.telegram_bot import call_telegram_api

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
        print(f"telegram_webhook_reset_warning={description}")
        raise SystemExit(0)
    time.sleep(max(1, int(retry_match.group(1))) + 1)

print("telegram_webhook_reset_ok")
PY
"@
      Invoke-RemoteSh -Script $remote
    }
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
docker compose -p '$ComposeProject' exec -T backend python - <<'PY'
from src.core.config import settings
from src.services.telegram_bot import call_telegram_api
from src.services.vk_delivery import probe_vk_api, vk_delivery_configured, vk_group_id

errors = []

get_me = call_telegram_api("getMe", {}, settings.TELEGRAM_API_TIMEOUT_SECONDS, 1)
webhook_info = call_telegram_api("getWebhookInfo", {}, settings.TELEGRAM_API_TIMEOUT_SECONDS, 1)
if not settings.has_real_telegram_token:
    errors.append("telegram token missing")
if not get_me.get("ok"):
    errors.append("telegram getMe failed")
if not webhook_info.get("ok"):
    errors.append("telegram getWebhookInfo failed")

if settings.TELEGRAM_USE_POLLING:
    print("telegram_delivery_probe_ok mode=polling")
else:
    expected = f"{settings.API_BASE_URL.rstrip('/')}/api/telegram/webhook"
    actual = (webhook_info.get("result") or {}).get("url") or ""
    if expected and actual != expected:
        errors.append("telegram webhook mismatch")
    print("telegram_delivery_probe_ok mode=webhook")

if not settings.VK_ID_APP_ID.strip() or not settings.VK_ID_REDIRECT_URI.strip():
    errors.append("vk id config missing")
if not vk_group_id() or not vk_delivery_configured():
    errors.append("vk delivery config missing")
if not probe_vk_api():
    errors.append("vk api probe failed")

if errors:
    raise SystemExit("; ".join(errors))
print("vk_delivery_probe_ok")
PY
expected_vk_callback_confirmation=$expectedVkCode
vk_callback_payload=$vkCallbackPayload
runtime_vk_callback_confirmation="`$(python3 - <<'PY'
from pathlib import Path
path = Path("backend/.env")
values = []
if path.exists():
    for line in path.read_text().splitlines():
        if line.startswith("VK_CALLBACK_CONFIRMATION_CODE="):
            values.append(line.split("=", 1)[1].strip())
if len(values) > 1:
    raise SystemExit("duplicate VK_CALLBACK_CONFIRMATION_CODE entries in backend/.env")
print(values[-1] if values else "")
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
curl -fsS -I http://127.0.0.1:8082/ | head -n 8
"@
    Invoke-RemoteSh -Script $remote
  }

  if ($publishPublicFrontend) {
    Invoke-Step "Publish public frontend static root" {
      $remote = @"
set -e
deploy_id='fast-$deployId'
web_stage='$PublicWebRoot.stage.'`$deploy_id
web_failed='$PublicWebRoot.failed.'`$deploy_id
backup_root='$RemotePath/.deploy-backups/fast-$deployId'
web_backup="`$backup_root/public-web-root"
rm -rf "`$web_stage"
mkdir -p "`$(dirname '$PublicWebRoot')" "`$web_stage" "`$backup_root"
tar -xzf /tmp/shamrai-web-dist-fast.tar.gz -C "`$web_stage"
test -f "`$web_stage/index.html"

restore_public() {
  status="`$?"
  echo "Public frontend publish failed; restoring previous public web root." >&2
  if [ -e '$PublicWebRoot' ]; then
    rm -rf "`$web_failed"
    mv '$PublicWebRoot' "`$web_failed" || true
  fi
  if [ -d "`$web_backup" ]; then
    mv "`$web_backup" '$PublicWebRoot'
  fi
  nginx -t && systemctl reload nginx || true
  exit "`$status"
}

trap restore_public ERR
if [ -e '$PublicWebRoot' ]; then
  rm -rf "`$web_backup"
  mv '$PublicWebRoot' "`$web_backup"
fi
mv "`$web_stage" '$PublicWebRoot'
nginx -t
systemctl reload nginx
trap - ERR
"@
      Invoke-RemoteSh -Script $remote
    }

    Invoke-Step "Public frontend asset verification" {
      $expectedAssetArgs = ($script:frontendPublicAssets | ForEach-Object { ConvertTo-ShellSingleQuoted $_ }) -join " "
      if ([string]::IsNullOrWhiteSpace($expectedAssetArgs)) {
        throw "No expected public frontend assets were captured from local dist."
      }

      $remote = @"
set -e
html="`$(curl -fsS https://shamra1.pro/)"
for asset in $expectedAssetArgs; do
  printf '%s' "`$html" | grep -F "`$asset" >/dev/null
  curl -fsS -I "https://shamra1.pro/`$asset" >/dev/null
done
curl -fsS https://shamra1.pro/api/health >/dev/null
echo 'public_frontend_assets_match_dist'
"@
      try {
        Invoke-RemoteSh -Script $remote
      } catch {
        Write-Warning "Public asset verification failed; rolling back public web root from fast deploy backup $deployId."
        Invoke-PublicFrontendRollback
        throw
      }
    }

    Invoke-Step "Cleanup fast deploy temporary archives" {
      $remote = @"
set -e
rm -f /tmp/shamrai-web-dist-fast.tar.gz
rm -f /tmp/shamrai-frontend-fast.tar.gz /tmp/shamrai-backend-fast.tar.gz /tmp/shamrai-docker-compose.yml
rm -rf '$RemotePath/.deploy-stage/fast-$deployId'
"@
      Invoke-RemoteSh -Script $remote
    }
  }
} catch {
  if ($rollbackArmed) {
    Write-Warning "Fast redeploy failed after server files changed; attempting rollback from deploy snapshot $deployId."
    try {
      Invoke-FastRollback -RollbackServices $services
    } catch {
      Write-Warning "Fast redeploy rollback also failed. Manual server inspection is required."
    }
  }
  throw
}

Write-Host ""
Write-Host "Fast redeploy finished: $Target" -ForegroundColor Green
