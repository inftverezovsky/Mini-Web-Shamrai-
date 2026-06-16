param(
  [string]$Server = "root@82.147.67.245",
  [string]$HostKey = "ssh-ed25519 255 SHA256:xdVRtRaXWqK6eAIsE3VwD0o2H6GJDcCm65L1ZUBjuMw",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [string]$TegroShopId = $env:TEGRO_SHOP_ID,
  [string]$TegroApiKey = $env:TEGRO_API_KEY,
  [string]$TegroSecretKey = $env:TEGRO_SECRET_KEY,
  [string]$TegroReturnUrl = $env:TEGRO_RETURN_URL
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

$plink = Find-Tool @("C:\Program Files\PuTTY\plink.exe", "plink.exe")
if (-not $plink) {
  throw "PuTTY plink not found."
}

$password = $env:SHAMRAI_SSH_PASSWORD
if ([string]::IsNullOrWhiteSpace($password)) {
  throw "Set SHAMRAI_SSH_PASSWORD for this run. Do not store it in files."
}

foreach ($required in @(
  @{ Name = "TEGRO_SHOP_ID"; Value = $TegroShopId },
  @{ Name = "TEGRO_API_KEY"; Value = $TegroApiKey },
  @{ Name = "TEGRO_SECRET_KEY"; Value = $TegroSecretKey }
)) {
  if ([string]::IsNullOrWhiteSpace($required.Value)) {
    throw "Set $($required.Name) for this run. Do not store it in files."
  }
}

if ([string]::IsNullOrWhiteSpace($TegroReturnUrl)) {
  $TegroReturnUrl = "https://shamra1.pro/app"
}

$remotePathQuoted = ConvertTo-ShellSingleQuoted $RemotePath
$composeProjectQuoted = ConvertTo-ShellSingleQuoted $ComposeProject
$shopIdQuoted = ConvertTo-ShellSingleQuoted $TegroShopId
$apiKeyQuoted = ConvertTo-ShellSingleQuoted $TegroApiKey
$secretKeyQuoted = ConvertTo-ShellSingleQuoted $TegroSecretKey
$returnUrlQuoted = ConvertTo-ShellSingleQuoted $TegroReturnUrl

$remote = @"
set -e
CANON_PATH=$remotePathQuoted
CANON_PROJECT=$composeProjectQuoted
CANON_PORT='8082'
NEXT_TEGRO_SHOP_ID=$shopIdQuoted
NEXT_TEGRO_API_KEY=$apiKeyQuoted
NEXT_TEGRO_SECRET_KEY=$secretKeyQuoted
NEXT_TEGRO_RETURN_URL=$returnUrlQuoted

mask_value() {
  value="`$1"
  length="`$(printf '%s' "`$value" | wc -c | tr -d ' ')"
  if [ "`$length" -le 4 ]; then
    printf '<short> len=%s' "`$length"
    return
  fi
  prefix="`$(printf '%s' "`$value" | cut -c1-2)"
  suffix="`$(printf '%s' "`$value" | rev | cut -c1-2 | rev)"
  printf '%s******%s len=%s' "`$prefix" "`$suffix" "`$length"
}

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
  echo "Port `$CANON_PORT is owned by a non-canonical container. Refusing to patch Tegro runtime." >&2
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

python3 - "`$NEXT_TEGRO_SHOP_ID" "`$NEXT_TEGRO_API_KEY" "`$NEXT_TEGRO_SECRET_KEY" "`$NEXT_TEGRO_RETURN_URL" backend/.env <<'PY'
from pathlib import Path
import sys

values = {
    "TEGRO_SHOP_ID": sys.argv[1],
    "TEGRO_API_KEY": sys.argv[2],
    "TEGRO_SECRET_KEY": sys.argv[3],
    "TEGRO_RETURN_URL": sys.argv[4],
    "TEGRO_API_BASE_URL": "https://tegro.money/api",
}
path = Path(sys.argv[5])
lines = path.read_text().splitlines() if path.exists() else []
out = []
seen = set()
previous_lengths = {}
for line in lines:
    key = line.split("=", 1)[0] if "=" in line else None
    if key in values:
        previous_lengths[key] = len(line.split("=", 1)[1])
        out.append(f"{key}={values[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in values.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out).rstrip() + "\n")
print(
    "tegro_env_updated "
    + " ".join(f"{key}_previous_len={previous_lengths.get(key, 0)} new_len={len(value)}" for key, value in values.items())
)
PY

docker compose -p "`$CANON_PROJECT" up -d --force-recreate backend
curl -fsS "http://127.0.0.1:`$CANON_PORT/api/health" >/dev/null
payments_health="`$(curl -fsS "http://127.0.0.1:`$CANON_PORT/api/health/payments")"
PAYMENTS_HEALTH="`$payments_health" python3 - <<'PY'
import json
import os
import sys

payload = json.loads(os.environ["PAYMENTS_HEALTH"])
if not payload.get("tegro", {}).get("configured"):
    raise SystemExit("Tegro is not configured according to /api/health/payments")
print("tegro_payments_health_ok")
PY
echo "tegro_shop_id_mask `$(mask_value "`$NEXT_TEGRO_SHOP_ID")"
echo "tegro_api_key_mask `$(mask_value "`$NEXT_TEGRO_API_KEY")"
echo "tegro_secret_key_mask `$(mask_value "`$NEXT_TEGRO_SECRET_KEY")"
"@

$normalizedRemote = ((($remote -replace "`r`n", "`n") -replace "`r", "").TrimEnd("`n")) + "`n"
$encodedRemote = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($normalizedRemote))
$remoteCommand = "printf '%s' '$encodedRemote' | base64 -d | sh"
Invoke-NativeChecked $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey $remoteCommand
