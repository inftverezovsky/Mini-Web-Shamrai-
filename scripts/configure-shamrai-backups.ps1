param(
  [string]$Workspace = "C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)",
  [string]$Server = "root@82.147.67.245",
  [int]$SshPort = 22,
  [string]$HostKeyFingerprint = "SHA256:xdVRtRaXWqK6eAIsE3VwD0o2H6GJDcCm65L1ZUBjuMw",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [string]$BackupAgeRecipient = $env:SHAMRAI_BACKUP_AGE_RECIPIENT,
  [int]$RetentionDaily = 14,
  [int]$RetentionWeekly = 8,
  [string]$BackupTime = "02:30",
  [string]$CronTimezone = "Europe/Moscow",
  [string]$SshKeyPath = $env:SHAMRAI_SSH_KEY_PATH,
  [string]$KnownHostsPath = "",
  [switch]$NoHostKeyScan,
  [switch]$RunBackupNow,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-HomePath {
  if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    return $env:USERPROFILE
  }
  if (-not [string]::IsNullOrWhiteSpace($HOME)) {
    return $HOME
  }
  throw "Unable to resolve the current user's home directory."
}

function Find-Tool {
  param([string[]]$Candidates)
  foreach ($candidate in $Candidates) {
    if (Test-Path -LiteralPath $candidate) {
      return $candidate
    }
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) {
      return $cmd.Source
    }
  }
  return $null
}

function Invoke-Step {
  param([string]$Title, [scriptblock]$Script)
  Write-Host ""
  Write-Host "==> $Title" -ForegroundColor Cyan
  & $Script
}

function Invoke-NativeChecked {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string[]]$Arguments = @()
  )

  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
  }
}

function Invoke-NativeOutputChecked {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string[]]$Arguments = @()
  )

  $output = & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
  }
  return ($output -join "`n")
}

function Set-Utf8NoBomLfContent {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Content
  )

  $encoding = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, ($Content -replace "`r`n", "`n"), $encoding)
}

function ConvertTo-ShellSingleQuoted {
  param([string]$Value)
  if ($null -eq $Value) {
    $Value = ""
  }
  $singleQuote = [char]39
  $doubleQuote = [char]34
  $escaped = $Value.Replace(
    "$singleQuote",
    "$singleQuote$doubleQuote$singleQuote$doubleQuote$singleQuote"
  )
  return "$singleQuote$escaped$singleQuote"
}

function Get-SshHostName {
  param([string]$Target)
  $hostPart = $Target
  if ($hostPart.Contains("@")) {
    $hostPart = $hostPart.Split("@", 2)[1]
  }
  if ($hostPart.Contains(":")) {
    $hostPart = $hostPart.Split(":", 2)[0]
  }
  return $hostPart
}

function Initialize-KnownHosts {
  param(
    [Parameter(Mandatory = $true)][string]$HostName,
    [Parameter(Mandatory = $true)][int]$Port,
    [Parameter(Mandatory = $true)][string]$ExpectedFingerprint,
    [string]$Path,
    [bool]$SkipScan
  )

  $sshKeygen = Find-Tool @("ssh-keygen.exe", "ssh-keygen")
  if (-not $sshKeygen) {
    throw "ssh-keygen was not found. Install OpenSSH Client."
  }

  if ([string]::IsNullOrWhiteSpace($Path)) {
    $deployDir = Join-Path $Workspace ".deploy"
    New-Item -ItemType Directory -Force -Path $deployDir | Out-Null
    $Path = Join-Path $deployDir "shamrai-known-hosts"
  }

  if (-not $SkipScan) {
    $sshKeyscan = Find-Tool @("ssh-keyscan.exe", "ssh-keyscan")
    if (-not $sshKeyscan) {
      throw "ssh-keyscan was not found. Install OpenSSH Client or pass -KnownHostsPath with -NoHostKeyScan."
    }
    $scanArgs = @("-p", "$Port", "-t", "ed25519", $HostName)
    $scanOutput = Invoke-NativeOutputChecked -FilePath $sshKeyscan -Arguments $scanArgs
    if ([string]::IsNullOrWhiteSpace($scanOutput)) {
      throw "ssh-keyscan returned no host key for $HostName."
    }
    Set-Utf8NoBomLfContent -Path $Path -Content ($scanOutput.Trim() + "`n")
  }

  if (-not (Test-Path -LiteralPath $Path)) {
    throw "Known hosts file was not found: $Path"
  }

  $fingerprintOutput = Invoke-NativeOutputChecked -FilePath $sshKeygen -Arguments @("-lf", $Path)
  if ($fingerprintOutput -notmatch [regex]::Escape($ExpectedFingerprint)) {
    throw "SSH host key fingerprint mismatch for $HostName. Expected $ExpectedFingerprint."
  }

  return $Path
}

function Assert-NoUnresolvedTemplatePlaceholders {
  param(
    [Parameter(Mandatory = $true)][string]$Content,
    [Parameter(Mandatory = $true)][string[]]$Placeholders
  )

  $unresolved = @($Placeholders | Where-Object { $Content.Contains($_) })
  if ($unresolved.Count -gt 0) {
    throw "Remote installer still contains unresolved template placeholders: $($unresolved -join ', ')"
  }
}

function New-RemoteInstallerScript {
  param(
    [Parameter(Mandatory = $true)][string]$RemoteAppPath,
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][string]$AgeRecipient,
    [Parameter(Mandatory = $true)][int]$DailyKeep,
    [Parameter(Mandatory = $true)][int]$WeeklyKeep,
    [Parameter(Mandatory = $true)][int]$Hour,
    [Parameter(Mandatory = $true)][int]$Minute,
    [Parameter(Mandatory = $true)][string]$Timezone,
    [Parameter(Mandatory = $true)][bool]$BackupNow
  )

  $runNowValue = if ($BackupNow) { "1" } else { "0" }
  $template = @'
#!/usr/bin/env bash
set -Eeuo pipefail

REMOTE_PATH=__REMOTE_PATH__
COMPOSE_PROJECT=__COMPOSE_PROJECT__
BACKUP_AGE_RECIPIENT=__AGE_RECIPIENT__
RETENTION_DAILY=__RETENTION_DAILY__
RETENTION_WEEKLY=__RETENTION_WEEKLY__
BACKUP_HOUR=__BACKUP_HOUR__
BACKUP_MINUTE=__BACKUP_MINUTE__
CRON_TZ_VALUE=__CRON_TZ__
RUN_BACKUP_NOW=__RUN_BACKUP_NOW__

OPS_DIR="$REMOTE_PATH/ops"
BACKUP_DIR="$REMOTE_PATH/db-backups"
ENV_FILE="$OPS_DIR/backup.env"

echo "Shamrai backup configuration inventory:"
docker ps -a --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}' || true
echo "Compose labels:"
docker ps -aq | while read -r id; do
  [ -n "$id" ] || continue
  name="$(docker inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's#^/##' || true)"
  labels_json="$(docker inspect -f '{{json .Config.Labels}}' "$id" 2>/dev/null || echo '{}')"
  project="$(printf '%s' "$labels_json" | python3 -c "import json,sys; print((json.load(sys.stdin) or {}).get('com.docker.compose.project', ''))" 2>/dev/null || true)"
  workdir="$(printf '%s' "$labels_json" | python3 -c "import json,sys; print((json.load(sys.stdin) or {}).get('com.docker.compose.project.working_dir', ''))" 2>/dev/null || true)"
  printf '%s\tproject=%s\tworkdir=%s\n' "$name" "$project" "$workdir"
done
echo "Listening ports 80/443/8000/8081/8082:"
ss -ltnp | grep -E ':(80|443|8000|8081|8082)\b' || true
echo "Disk space:"
df -h "$REMOTE_PATH" "$(dirname "$REMOTE_PATH")" || true

if [ ! -d "$REMOTE_PATH" ]; then
  echo "Canonical Shamrai path is missing: $REMOTE_PATH" >&2
  exit 21
fi

cd "$REMOTE_PATH"
docker compose -p "$COMPOSE_PROJECT" config -q
if ! docker compose -p "$COMPOSE_PROJECT" ps -q postgres >/dev/null 2>&1; then
  echo "Canonical postgres service is not present in compose project $COMPOSE_PROJECT." >&2
  exit 22
fi
docker compose -p "$COMPOSE_PROJECT" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

if ! command -v age >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends age
  else
    echo "age is required for encrypted backups and apt-get was not found." >&2
    exit 23
  fi
fi

install -d -m 0700 "$OPS_DIR" "$BACKUP_DIR" "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly"

cat > "$OPS_DIR/backup-db.sh" <<'BACKUP_SCRIPT'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$OPS_DIR/backup.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing backup config: $ENV_FILE" >&2
  exit 10
fi

# shellcheck disable=SC1090
. "$ENV_FILE"

: "${SHAMRAI_BACKUP_REMOTE_PATH:?missing SHAMRAI_BACKUP_REMOTE_PATH}"
: "${SHAMRAI_BACKUP_COMPOSE_PROJECT:?missing SHAMRAI_BACKUP_COMPOSE_PROJECT}"
: "${SHAMRAI_BACKUP_DIR:?missing SHAMRAI_BACKUP_DIR}"
: "${SHAMRAI_BACKUP_AGE_RECIPIENT:?missing SHAMRAI_BACKUP_AGE_RECIPIENT}"
: "${SHAMRAI_BACKUP_RETENTION_DAILY:?missing SHAMRAI_BACKUP_RETENTION_DAILY}"
: "${SHAMRAI_BACKUP_RETENTION_WEEKLY:?missing SHAMRAI_BACKUP_RETENTION_WEEKLY}"

if [ "$SHAMRAI_BACKUP_AGE_RECIPIENT" = "age1replacewithpublicrecipient" ]; then
  echo "Refusing to run backup with placeholder age recipient." >&2
  exit 11
fi

if ! command -v age >/dev/null 2>&1; then
  echo "age is required for encrypted backups." >&2
  exit 12
fi

daily_dir="$SHAMRAI_BACKUP_DIR/daily"
weekly_dir="$SHAMRAI_BACKUP_DIR/weekly"
mkdir -p "$daily_dir" "$weekly_dir"
chmod 0700 "$SHAMRAI_BACKUP_DIR" "$daily_dir" "$weekly_dir"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="shamrai-db.$timestamp"
tmp_dir="$(mktemp -d "$SHAMRAI_BACKUP_DIR/.tmp.$timestamp.XXXXXX")"
dump_path="$tmp_dir/$base.dump"
counts_path="$tmp_dir/$base.counts.tsv"
manifest_path="$daily_dir/$base.manifest.json"
encrypted_path="$daily_dir/$base.dump.age"

cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

cd "$SHAMRAI_BACKUP_REMOTE_PATH"

docker compose -p "$SHAMRAI_BACKUP_COMPOSE_PROJECT" exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$dump_path"

key_tables=(
  users
  subscription_plans
  subscriptions
  payment_attempts
  delivery_outbox
  bets
  user_bets
)

: > "$counts_path"
for table in "${key_tables[@]}"; do
  exists_sql="select to_regclass('public.${table}') is not null;"
  exists="$(printf '%s\n' "$exists_sql" | docker compose -p "$SHAMRAI_BACKUP_COMPOSE_PROJECT" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' | tr -d '[:space:]')"
  if [ "$exists" = "t" ]; then
    count_sql="select count(*) from public.${table};"
    count="$(printf '%s\n' "$count_sql" | docker compose -p "$SHAMRAI_BACKUP_COMPOSE_PROJECT" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' | tr -d '[:space:]')"
  else
    count="0"
  fi
  printf '%s\t%s\n' "$table" "$count" >> "$counts_path"
done

dump_sha256="$(sha256sum "$dump_path" | awk '{print $1}')"
dump_size_bytes="$(wc -c < "$dump_path" | tr -d '[:space:]')"

python3 - "$manifest_path" "$timestamp" "$dump_sha256" "$dump_size_bytes" "$counts_path" "$SHAMRAI_BACKUP_COMPOSE_PROJECT" "$SHAMRAI_BACKUP_REMOTE_PATH" "$base" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
timestamp = sys.argv[2]
dump_sha256 = sys.argv[3]
dump_size_bytes = int(sys.argv[4])
counts_path = Path(sys.argv[5])
compose_project = sys.argv[6]
remote_path = sys.argv[7]
base = sys.argv[8]

table_counts: dict[str, int] = {}
for line in counts_path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    table, count = line.split("\t", 1)
    table_counts[table] = int(count)

payload = {
    "schema_version": 1,
    "created_at": timestamp,
    "source": {
        "compose_project": compose_project,
        "remote_path": remote_path,
        "database": "postgres",
    },
    "artifact": {
        "base_name": base,
        "encrypted_file": f"{base}.dump.age",
        "dump_sha256": dump_sha256,
        "dump_size_bytes": dump_size_bytes,
        "format": "pg_dump custom",
        "encryption": "age",
    },
    "table_counts": table_counts,
}

manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

age -r "$SHAMRAI_BACKUP_AGE_RECIPIENT" -o "$encrypted_path" "$dump_path"
chmod 0600 "$encrypted_path" "$manifest_path"
ln -sfn "daily/$base.dump.age" "$SHAMRAI_BACKUP_DIR/latest.dump.age"
ln -sfn "daily/$base.manifest.json" "$SHAMRAI_BACKUP_DIR/latest.manifest.json"

if [ "$(date +%u)" = "7" ]; then
  cp -p "$encrypted_path" "$weekly_dir/$base.dump.age"
  cp -p "$manifest_path" "$weekly_dir/$base.manifest.json"
fi

prune_dir() {
  local dir="$1"
  local keep="$2"
  local file
  mapfile -t stale_files < <(
    find "$dir" -maxdepth 1 -type f -name 'shamrai-db.*.dump.age' -printf '%T@ %p\n' |
      sort -rn |
      awk -v keep="$keep" 'NR > keep { sub(/^[^ ]+ /, ""); print }'
  )
  for file in "${stale_files[@]}"; do
    rm -f "$file" "${file%.dump.age}.manifest.json"
  done
}

prune_dir "$daily_dir" "$SHAMRAI_BACKUP_RETENTION_DAILY"
prune_dir "$weekly_dir" "$SHAMRAI_BACKUP_RETENTION_WEEKLY"

echo "BACKUP_CREATED=$encrypted_path"
echo "BACKUP_MANIFEST=$manifest_path"
BACKUP_SCRIPT

chmod 0700 "$OPS_DIR/backup-db.sh"

{
  printf 'SHAMRAI_BACKUP_REMOTE_PATH=%q\n' "$REMOTE_PATH"
  printf 'SHAMRAI_BACKUP_COMPOSE_PROJECT=%q\n' "$COMPOSE_PROJECT"
  printf 'SHAMRAI_BACKUP_DIR=%q\n' "$BACKUP_DIR"
  printf 'SHAMRAI_BACKUP_AGE_RECIPIENT=%q\n' "$BACKUP_AGE_RECIPIENT"
  printf 'SHAMRAI_BACKUP_RETENTION_DAILY=%q\n' "$RETENTION_DAILY"
  printf 'SHAMRAI_BACKUP_RETENTION_WEEKLY=%q\n' "$RETENTION_WEEKLY"
} > "$ENV_FILE.tmp"
install -m 0600 "$ENV_FILE.tmp" "$ENV_FILE"
rm -f "$ENV_FILE.tmp"

cat > /etc/cron.d/shamrai-db-backup.tmp <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
CRON_TZ=$CRON_TZ_VALUE
$BACKUP_MINUTE $BACKUP_HOUR * * * root $OPS_DIR/backup-db.sh >> $OPS_DIR/backup.log 2>&1
EOF
install -m 0644 /etc/cron.d/shamrai-db-backup.tmp /etc/cron.d/shamrai-db-backup
rm -f /etc/cron.d/shamrai-db-backup.tmp

if command -v systemctl >/dev/null 2>&1; then
  systemctl reload cron 2>/dev/null || systemctl reload crond 2>/dev/null || true
fi

if [ "$RUN_BACKUP_NOW" = "1" ]; then
  "$OPS_DIR/backup-db.sh"
fi

echo "SHAMRAI_BACKUP_CONFIGURED=$ENV_FILE"
echo "SHAMRAI_BACKUP_CRON=/etc/cron.d/shamrai-db-backup"
'@

  $result = $template.Replace("__REMOTE_PATH__", (ConvertTo-ShellSingleQuoted $RemoteAppPath))
  $result = $result.Replace("__COMPOSE_PROJECT__", (ConvertTo-ShellSingleQuoted $Project))
  $result = $result.Replace("__AGE_RECIPIENT__", (ConvertTo-ShellSingleQuoted $AgeRecipient))
  $result = $result.Replace("__RETENTION_DAILY__", "$DailyKeep")
  $result = $result.Replace("__RETENTION_WEEKLY__", "$WeeklyKeep")
  $result = $result.Replace("__BACKUP_HOUR__", "$Hour")
  $result = $result.Replace("__BACKUP_MINUTE__", "$Minute")
  $result = $result.Replace("__CRON_TZ__", (ConvertTo-ShellSingleQuoted $Timezone))
  $result = $result.Replace("__RUN_BACKUP_NOW__", (ConvertTo-ShellSingleQuoted $runNowValue))
  return $result
}

function Invoke-RemoteChecked {
  param([Parameter(Mandatory = $true)][string]$Command)
  $args = @(
    "-i", $SshKeyPath,
    "-p", "$SshPort",
    "-o", "BatchMode=yes",
    "-o", "IdentitiesOnly=yes",
    "-o", "UserKnownHostsFile=$KnownHostsPath",
    "-o", "StrictHostKeyChecking=yes",
    $Server,
    $Command
  )
  Invoke-NativeChecked -FilePath $script:SshTool -Arguments $args
}

function Copy-ToRemoteChecked {
  param(
    [Parameter(Mandatory = $true)][string]$LocalPath,
    [Parameter(Mandatory = $true)][string]$RemoteFilePath
  )
  $target = "${Server}:$RemoteFilePath"
  $args = @(
    "-i", $SshKeyPath,
    "-P", "$SshPort",
    "-o", "BatchMode=yes",
    "-o", "IdentitiesOnly=yes",
    "-o", "UserKnownHostsFile=$KnownHostsPath",
    "-o", "StrictHostKeyChecking=yes",
    $LocalPath,
    $target
  )
  Invoke-NativeChecked -FilePath $script:ScpTool -Arguments $args
}

if ($RetentionDaily -lt 1 -or $RetentionWeekly -lt 1) {
  throw "Retention values must be positive. Got daily=$RetentionDaily weekly=$RetentionWeekly."
}

if ($BackupTime -notmatch '^([01][0-9]|2[0-3]):([0-5][0-9])$') {
  throw "BackupTime must use HH:mm in 24-hour format, for example 02:30."
}

$backupHour = [int]$Matches[1]
$backupMinute = [int]$Matches[2]

if ([string]::IsNullOrWhiteSpace($BackupAgeRecipient)) {
  if ($DryRun) {
    $BackupAgeRecipient = "age1replacewithpublicrecipient"
  } else {
    throw "Set SHAMRAI_BACKUP_AGE_RECIPIENT or pass -BackupAgeRecipient. This is the public age recipient, not a secret."
  }
}

if (-not $DryRun) {
  if ($BackupAgeRecipient -eq "age1replacewithpublicrecipient" -or $BackupAgeRecipient -notmatch '^age1[0-9a-z]+$') {
    throw "BackupAgeRecipient must be a real age public recipient that starts with age1."
  }
}

$installerContent = New-RemoteInstallerScript `
  -RemoteAppPath $RemotePath `
  -Project $ComposeProject `
  -AgeRecipient $BackupAgeRecipient `
  -DailyKeep $RetentionDaily `
  -WeeklyKeep $RetentionWeekly `
  -Hour $backupHour `
  -Minute $backupMinute `
  -Timezone $CronTimezone `
  -BackupNow ([bool]$RunBackupNow)

Assert-NoUnresolvedTemplatePlaceholders -Content $installerContent -Placeholders @(
  "__REMOTE_PATH__",
  "__COMPOSE_PROJECT__",
  "__AGE_RECIPIENT__",
  "__RETENTION_DAILY__",
  "__RETENTION_WEEKLY__",
  "__BACKUP_HOUR__",
  "__BACKUP_MINUTE__",
  "__CRON_TZ__",
  "__RUN_BACKUP_NOW__"
)

if ($DryRun) {
  Write-Host "DRY_RUN configure-shamrai-backups"
  Write-Host "server=$Server"
  Write-Host "remote_path=$RemotePath"
  Write-Host "compose_project=$ComposeProject"
  Write-Host "cron=$BackupTime $CronTimezone"
  Write-Host "retention=daily:$RetentionDaily weekly:$RetentionWeekly"
  Write-Host "backup_env=$RemotePath/ops/backup.env"
  Write-Host "backup_script=$RemotePath/ops/backup-db.sh"
  Write-Host "cron_file=/etc/cron.d/shamrai-db-backup"
  Write-Host "age_recipient_configured=$(-not [string]::IsNullOrWhiteSpace($BackupAgeRecipient))"
  exit 0
}

if ([string]::IsNullOrWhiteSpace($SshKeyPath)) {
  $defaultSshKeyPath = Join-Path (Get-HomePath) ".ssh/codex_deploy_ed25519"
  if (Test-Path -LiteralPath $defaultSshKeyPath) {
    $SshKeyPath = $defaultSshKeyPath
  }
}

if ([string]::IsNullOrWhiteSpace($SshKeyPath) -or -not (Test-Path -LiteralPath $SshKeyPath)) {
  throw "A valid SSH key is required. Pass -SshKeyPath or set SHAMRAI_SSH_KEY_PATH. Do not use passwords for backup ops."
}

$script:SshTool = Find-Tool @("ssh.exe", "ssh")
$script:ScpTool = Find-Tool @("scp.exe", "scp")
if (-not $script:SshTool -or -not $script:ScpTool) {
  throw "OpenSSH ssh/scp not found. Install OpenSSH Client."
}

$hostName = Get-SshHostName -Target $Server
$KnownHostsPath = Initialize-KnownHosts `
  -HostName $hostName `
  -Port $SshPort `
  -ExpectedFingerprint $HostKeyFingerprint `
  -Path $KnownHostsPath `
  -SkipScan ([bool]$NoHostKeyScan)

$deployDir = Join-Path $Workspace ".deploy"
New-Item -ItemType Directory -Force -Path $deployDir | Out-Null
$localInstaller = Join-Path $deployDir "shamrai-configure-backups.remote.sh"
$remoteInstaller = "/tmp/shamrai-configure-backups.$PID.sh"

Set-Utf8NoBomLfContent -Path $localInstaller -Content $installerContent

Invoke-Step "Upload backup installer" {
  Copy-ToRemoteChecked -LocalPath $localInstaller -RemoteFilePath $remoteInstaller
}

Invoke-Step "Configure encrypted nightly backups" {
  $command = "chmod 0700 $(ConvertTo-ShellSingleQuoted $remoteInstaller) && bash $(ConvertTo-ShellSingleQuoted $remoteInstaller); status=`$?; rm -f $(ConvertTo-ShellSingleQuoted $remoteInstaller); exit `$status"
  Invoke-RemoteChecked -Command $command
}

Write-Host ""
Write-Host "shamrai_backup_configuration_ok" -ForegroundColor Green
