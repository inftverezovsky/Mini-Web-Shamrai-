param(
  [string]$Server = "root@82.147.67.245",
  [string]$HostKey = "ssh-ed25519 255 SHA256:xdVRtRaXWqK6eAIsE3VwD0o2H6GJDcCm65L1ZUBjuMw",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [string]$SshKeyPath = $env:SHAMRAI_SSH_KEY_PATH,
  [string]$DriveFolderId = $env:SHAMRAI_GOOGLE_DRIVE_STATS_FOLDER_ID,
  [int]$UnitStakeRub = 10000
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($SshKeyPath) -and -not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
  $SshKeyPath = Join-Path $env:USERPROFILE ".ssh\codex_deploy_ed25519"
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

$ssh = Find-Tool @("ssh.exe")
$plink = Find-Tool @("C:\Program Files\PuTTY\plink.exe", "plink.exe")

$password = $env:SHAMRAI_SSH_PASSWORD
$usePassword = -not [string]::IsNullOrWhiteSpace($password)
$useKey = (-not $usePassword) -and (-not [string]::IsNullOrWhiteSpace($SshKeyPath)) -and (Test-Path -LiteralPath $SshKeyPath)
if ($usePassword -and -not $plink) {
  throw "PuTTY plink not found."
}
if ($useKey -and -not $ssh) {
  throw "OpenSSH ssh.exe not found."
}
if (-not $usePassword -and -not $useKey) {
  throw "Set SHAMRAI_SSH_PASSWORD for this run or provide a valid SshKeyPath. Do not store passwords in files."
}

$oauthClientId = $env:SHAMRAI_GOOGLE_OAUTH_CLIENT_ID
$oauthClientSecret = $env:SHAMRAI_GOOGLE_OAUTH_CLIENT_SECRET
$oauthRefreshToken = $env:SHAMRAI_GOOGLE_OAUTH_REFRESH_TOKEN
$oauthTokenUri = $env:SHAMRAI_GOOGLE_OAUTH_TOKEN_URI
if ([string]::IsNullOrWhiteSpace($oauthTokenUri)) {
  $oauthTokenUri = "https://oauth2.googleapis.com/token"
}

if ([string]::IsNullOrWhiteSpace($DriveFolderId)) {
  throw "Set SHAMRAI_GOOGLE_DRIVE_STATS_FOLDER_ID for this run. It can be a My Drive folder owned by the OAuth user."
}
if ([string]::IsNullOrWhiteSpace($oauthClientId)) {
  throw "Set SHAMRAI_GOOGLE_OAUTH_CLIENT_ID for this run."
}
if ([string]::IsNullOrWhiteSpace($oauthClientSecret)) {
  throw "Set SHAMRAI_GOOGLE_OAUTH_CLIENT_SECRET for this run."
}
if ([string]::IsNullOrWhiteSpace($oauthRefreshToken)) {
  throw "Set SHAMRAI_GOOGLE_OAUTH_REFRESH_TOKEN for this run."
}
if ($UnitStakeRub -le 0) {
  throw "UnitStakeRub must be positive."
}

$remotePathQuoted = ConvertTo-ShellSingleQuoted $RemotePath
$composeProjectQuoted = ConvertTo-ShellSingleQuoted $ComposeProject
$driveFolderIdQuoted = ConvertTo-ShellSingleQuoted $DriveFolderId
$oauthClientIdQuoted = ConvertTo-ShellSingleQuoted $oauthClientId
$oauthClientSecretQuoted = ConvertTo-ShellSingleQuoted $oauthClientSecret
$oauthRefreshTokenQuoted = ConvertTo-ShellSingleQuoted $oauthRefreshToken
$oauthTokenUriQuoted = ConvertTo-ShellSingleQuoted $oauthTokenUri
$unitStakeQuoted = ConvertTo-ShellSingleQuoted ([string]$UnitStakeRub)

$remote = @"
set -e
CANON_PATH=$remotePathQuoted
CANON_PROJECT=$composeProjectQuoted
CANON_PORT='8082'
DRIVE_FOLDER_ID=$driveFolderIdQuoted
GOOGLE_OAUTH_CLIENT_ID_VALUE=$oauthClientIdQuoted
GOOGLE_OAUTH_CLIENT_SECRET_VALUE=$oauthClientSecretQuoted
GOOGLE_OAUTH_REFRESH_TOKEN_VALUE=$oauthRefreshTokenQuoted
GOOGLE_OAUTH_TOKEN_URI_VALUE=$oauthTokenUriQuoted
UNIT_STAKE_RUB=$unitStakeQuoted

echo 'Shamrai server inventory:'
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.project.working_dir"}}' || true
echo 'Listening ports 80/443/8000/8081/8082:'
ss -ltnp | grep -E ':(80|443|8000|8081|8082)\b' || true

conflicts="`$(
  docker ps --format '{{.Names}}\t{{.Ports}}\t{{.Label "com.docker.compose.project"}}' | while IFS="`$(printf '\t')" read -r name ports project; do
    case "`$ports" in
      *":`$CANON_PORT->"*)
        if [ "`$project" != "`$CANON_PROJECT" ]; then
          printf '%s\t%s\t%s\n' "`$name" "`$ports" "`$project"
        fi
        ;;
    esac
  done
)"
if [ -n "`$conflicts" ]; then
  echo "Port `$CANON_PORT is owned by a non-canonical container. Refusing to patch runtime env." >&2
  echo "`$conflicts" >&2
  exit 20
fi

if [ ! -d "`$CANON_PATH" ]; then
  echo "Canonical path is missing: `$CANON_PATH" >&2
  exit 21
fi

cd "`$CANON_PATH"
if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
fi

export DRIVE_FOLDER_ID
export GOOGLE_OAUTH_CLIENT_ID_VALUE
export GOOGLE_OAUTH_CLIENT_SECRET_VALUE
export GOOGLE_OAUTH_REFRESH_TOKEN_VALUE
export GOOGLE_OAUTH_TOKEN_URI_VALUE
export UNIT_STAKE_RUB
python3 - backend/.env <<'PY'
from pathlib import Path
import os
import sys

env_path = sys.argv[1]
drive_folder_id = os.environ["DRIVE_FOLDER_ID"]
oauth_client_id = os.environ["GOOGLE_OAUTH_CLIENT_ID_VALUE"]
oauth_client_secret = os.environ["GOOGLE_OAUTH_CLIENT_SECRET_VALUE"]
oauth_refresh_token = os.environ["GOOGLE_OAUTH_REFRESH_TOKEN_VALUE"]
oauth_token_uri = os.environ["GOOGLE_OAUTH_TOKEN_URI_VALUE"]
unit_stake_rub = os.environ["UNIT_STAKE_RUB"]
path = Path(env_path)
lines = path.read_text().splitlines() if path.exists() else []
updates = {
    "GOOGLE_DRIVE_STATS_ENABLED": "true",
    "GOOGLE_DRIVE_AUTH_MODE": "oauth",
    "GOOGLE_DRIVE_STATS_FOLDER_ID": drive_folder_id,
    "GOOGLE_OAUTH_CLIENT_ID": oauth_client_id,
    "GOOGLE_OAUTH_CLIENT_SECRET": oauth_client_secret,
    "GOOGLE_OAUTH_REFRESH_TOKEN": oauth_refresh_token,
    "GOOGLE_OAUTH_TOKEN_URI": oauth_token_uri,
    "STATS_EXPORT_UNIT_STAKE_RUB": unit_stake_rub,
}
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out).rstrip() + "\n")
print("google_drive_oauth_stats_env_updated")
print(f"drive_folder_id={drive_folder_id}")
print(f"oauth_client_id_configured={bool(oauth_client_id)}")
print(f"oauth_client_secret_configured={bool(oauth_client_secret)}")
print(f"oauth_refresh_token_configured={bool(oauth_refresh_token)}")
print(f"unit_stake_rub={unit_stake_rub}")
PY

docker compose -p "`$CANON_PROJECT" up -d --force-recreate backend
curl -fsS "http://127.0.0.1:`$CANON_PORT/api/health" >/dev/null

docker compose -p "`$CANON_PROJECT" exec -T backend python - <<'PY'
from src.core.config import settings
print("drive_enabled=" + str(settings.GOOGLE_DRIVE_STATS_ENABLED))
print("drive_auth_mode=" + settings.GOOGLE_DRIVE_AUTH_MODE)
print("drive_folder_configured=" + str(bool(settings.GOOGLE_DRIVE_STATS_FOLDER_ID.strip())))
print("oauth_client_configured=" + str(bool(settings.GOOGLE_OAUTH_CLIENT_ID.strip())))
print("oauth_secret_configured=" + str(bool(settings.GOOGLE_OAUTH_CLIENT_SECRET.strip())))
print("oauth_refresh_configured=" + str(bool(settings.GOOGLE_OAUTH_REFRESH_TOKEN.strip())))
print("unit_stake_rub=" + str(settings.STATS_EXPORT_UNIT_STAKE_RUB))
PY

echo 'google_drive_oauth_stats_configured_ok'
"@

$previousOutputEncoding = [Console]::OutputEncoding
try {
  [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
  $normalizedRemote = ((($remote -replace "`r`n", "`n") -replace "`r", "").TrimEnd("`n")) + "`n"
  if ($usePassword) {
    $normalizedRemote | & $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey "sh -s"
  } else {
    $normalizedRemote | & $ssh -i $SshKeyPath -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new $Server "sh -s"
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Remote OAuth configuration failed with exit code $LASTEXITCODE."
  }
}
finally {
  [Console]::OutputEncoding = $previousOutputEncoding
}

Write-Host "Configured Google Drive OAuth stats export."
