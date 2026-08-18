param(
  [string]$Workspace = "",
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
  [string]$OffHostS3Bucket = $env:SHAMRAI_BACKUP_S3_BUCKET,
  [string]$OffHostS3Prefix = $env:SHAMRAI_BACKUP_S3_PREFIX,
  [string]$OffHostS3EndpointUrl = $env:SHAMRAI_BACKUP_S3_ENDPOINT_URL,
  [string]$OffHostS3Region = $env:SHAMRAI_BACKUP_S3_REGION,
  [string]$ManifestSigningPublicKeyBase64 = $env:SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64,
  [string]$OffHostCredentialWrapper = $env:SHAMRAI_BACKUP_CREDENTIAL_WRAPPER,
  [string]$S3RetentionAttestationPath = "",
  [string]$SshKeyPath = $env:SHAMRAI_SSH_KEY_PATH,
  [string]$KnownHostsPath = "",
  [switch]$NoHostKeyScan,
  [switch]$RunBackupNow,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($Workspace)) {
  $Workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

$manifestSignatureModule = Join-Path $PSScriptRoot "lib/BackupManifestSignature.psm1"
Import-Module -Name $manifestSignatureModule -Force -ErrorAction Stop

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

function Assert-S3RetentionAttestation {
  param(
    [string]$Path,
    [string]$ExpectedBucket,
    [string]$ExpectedPrefix,
    [AllowEmptyString()][string]$ExpectedEndpointUrl,
    [string]$ExpectedRegion
  )

  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "S3 retention attestation was not found: $Path"
  }
  $payload = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
  $required = @(
    "schema_version", "status", "verified_at", "bucket", "prefix", "endpoint_url", "sts_endpoint_url", "region",
    "versioning_status", "object_lock_status",
    "default_retention_days", "current_expiration_days", "noncurrent_expiration_days",
    "bucket_deny_policy_status", "bucket_deny_policy_sha256", "bucket_deny_policy_required_rule_count",
    "writer_policy_sha256",
    "writer_access_key_id_sha256", "writer_identity_evidence", "writer_principal_type",
    "writer_caller_arn_sha256", "writer_stable_principal_arn_sha256",
    "writer_permissions_boundary_status", "writer_permissions_boundary_sha256",
    "writer_permissions_boundary_arn_sha256", "writer_permissions_boundary_version_id",
    "writer_permissions_boundary_required_rule_count",
    "writer_capability_probe", "writer_put_object_allowed",
    "writer_get_object_allowed", "writer_list_prefix_allowed", "writer_delete_object_denied",
    "writer_delete_object_version_denied", "writer_outside_prefix_put_denied",
    "writer_outside_prefix_get_denied", "writer_outside_prefix_list_denied",
    "writer_effective_permission_evidence", "writer_admin_capability_probe",
    "writer_put_object_retention_denied", "writer_put_object_legal_hold_denied",
    "writer_put_object_acl_denied", "writer_put_object_tagging_denied",
    "writer_delete_object_tagging_denied", "writer_abort_multipart_upload_denied",
    "writer_put_bucket_policy_denied", "writer_delete_bucket_policy_denied", "writer_delete_bucket_denied",
    "writer_put_bucket_lifecycle_configuration_denied",
    "writer_put_object_lock_configuration_denied", "writer_put_bucket_versioning_denied"
  )
  foreach ($name in $required) {
    if ($null -eq $payload.PSObject.Properties[$name]) {
      throw "S3 retention attestation is missing '$name'."
    }
  }
  $verifiedAt = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParse([string]$payload.verified_at, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeUniversal, [ref]$verifiedAt)) {
    throw "S3 retention attestation timestamp is invalid."
  }
  $now = [DateTimeOffset]::UtcNow
  if ($verifiedAt -gt $now.AddMinutes(10) -or $verifiedAt -lt $now.AddDays(-7)) {
    throw "S3 retention attestation must be refreshed within seven days."
  }
  if (
    [int]$payload.schema_version -ne 3 -or
    [string]$payload.status -ne "passed" -or
    [string]$payload.bucket -ne $ExpectedBucket -or
    [string]$payload.prefix -ne $ExpectedPrefix -or
    [string]$payload.endpoint_url -cne $ExpectedEndpointUrl -or
    [string]$payload.sts_endpoint_url -cne $ExpectedEndpointUrl -or
    [string]$payload.region -cne $ExpectedRegion -or
    [string]$payload.versioning_status -ne "Enabled" -or
    [string]$payload.object_lock_status -ne "Enabled" -or
    [int]$payload.default_retention_days -lt 35 -or
    [int]$payload.current_expiration_days -lt 35 -or
    [int]$payload.noncurrent_expiration_days -lt 90 -or
    [string]$payload.bucket_deny_policy_status -cne "passed" -or
    [string]$payload.bucket_deny_policy_sha256 -notmatch '^[0-9a-f]{64}$' -or
    [int]$payload.bucket_deny_policy_required_rule_count -ne 4 -or
    [string]$payload.writer_policy_sha256 -notmatch '^[0-9a-f]{64}$' -or
    [string]$payload.writer_access_key_id_sha256 -notmatch '^[0-9a-f]{64}$' -or
    [string]$payload.writer_identity_evidence -cne "aws_sts_and_iam_permissions_boundary_v1" -or
    [string]$payload.writer_principal_type -notin @("iam_user", "iam_role", "assumed_role_to_iam_role") -or
    [string]$payload.writer_caller_arn_sha256 -notmatch '^[0-9a-f]{64}$' -or
    [string]$payload.writer_stable_principal_arn_sha256 -notmatch '^[0-9a-f]{64}$' -or
    [string]$payload.writer_permissions_boundary_status -cne "passed" -or
    [string]$payload.writer_permissions_boundary_sha256 -notmatch '^[0-9a-f]{64}$' -or
    [string]$payload.writer_permissions_boundary_arn_sha256 -notmatch '^[0-9a-f]{64}$' -or
    [string]$payload.writer_permissions_boundary_version_id -notmatch '^v[1-9][0-9]*$' -or
    [int]$payload.writer_permissions_boundary_required_rule_count -ne 5 -or
    [string]$payload.writer_capability_probe -cne "passed" -or
    $payload.writer_put_object_allowed -ne $true -or
    $payload.writer_get_object_allowed -ne $true -or
    $payload.writer_list_prefix_allowed -ne $true -or
    $payload.writer_delete_object_denied -ne $true -or
    $payload.writer_delete_object_version_denied -ne $true -or
    $payload.writer_outside_prefix_put_denied -ne $true -or
    $payload.writer_outside_prefix_get_denied -ne $true -or
    $payload.writer_outside_prefix_list_denied -ne $true -or
    [string]$payload.writer_effective_permission_evidence -cne "live_aws_sts_iam_boundary_bucket_policy_v3+live_unconfounded_object_probes_v3" -or
    [string]$payload.writer_admin_capability_probe -cne "passed"
  ) {
    throw "S3 retention attestation does not satisfy the Shamrai WORM policy."
  }
  foreach ($adminEvidenceField in @(
    "writer_put_object_retention_denied", "writer_put_object_legal_hold_denied",
    "writer_put_object_acl_denied", "writer_put_object_tagging_denied",
    "writer_delete_object_tagging_denied", "writer_abort_multipart_upload_denied",
    "writer_put_bucket_policy_denied", "writer_delete_bucket_policy_denied", "writer_delete_bucket_denied",
    "writer_put_bucket_lifecycle_configuration_denied",
    "writer_put_object_lock_configuration_denied", "writer_put_bucket_versioning_denied"
  )) {
    $adminEvidenceValue = $payload.$adminEvidenceField
    if ($adminEvidenceValue -isnot [bool] -or -not [bool]$adminEvidenceValue) {
      throw "S3 retention attestation does not satisfy '$adminEvidenceField'."
    }
  }
}

function Get-OffHostS3Key {
  param(
    [string]$Prefix,
    [Parameter(Mandatory = $true)][string]$RelativeKey
  )

  if ([string]::IsNullOrWhiteSpace($Prefix)) {
    return $RelativeKey
  }
  return "$Prefix/$RelativeKey"
}

function Get-OffHostS3Uri {
  param(
    [Parameter(Mandatory = $true)][string]$Bucket,
    [Parameter(Mandatory = $true)][string]$Key
  )

  return "s3://$Bucket/$Key"
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
    [string]$S3Bucket,
    [string]$S3Prefix,
    [string]$S3EndpointUrl,
    [string]$S3Region,
    [string]$SigningPublicKeyBase64,
    [string]$CredentialWrapper,
    [Parameter(Mandatory = $true)][bool]$SystemdCredentials,
    [Parameter(Mandatory = $true)][bool]$BackupNow
  )

  $runNowValue = if ($BackupNow) { "1" } else { "0" }
  $systemdCredentialsValue = if ($SystemdCredentials) { "1" } else { "0" }
  $hourPadded = "{0:D2}" -f $Hour
  $minutePadded = "{0:D2}" -f $Minute
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
OFFHOST_S3_BUCKET=__OFFHOST_S3_BUCKET__
OFFHOST_S3_PREFIX=__OFFHOST_S3_PREFIX__
OFFHOST_S3_ENDPOINT_URL=__OFFHOST_S3_ENDPOINT_URL__
OFFHOST_S3_REGION=__OFFHOST_S3_REGION__
MANIFEST_SIGNING_PUBLIC_KEY_BASE64=__MANIFEST_SIGNING_PUBLIC_KEY_BASE64__
OFFHOST_CREDENTIAL_WRAPPER=__OFFHOST_CREDENTIAL_WRAPPER__
SYSTEMD_CREDENTIALS_MODE=__SYSTEMD_CREDENTIALS_MODE__
RUN_BACKUP_NOW=__RUN_BACKUP_NOW__

if [ ! -f "/usr/share/zoneinfo/$CRON_TZ_VALUE" ]; then
  echo "Configured IANA timezone is not installed: $CRON_TZ_VALUE" >&2
  exit 20
fi

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

if [ -n "$OFFHOST_S3_BUCKET" ] && ! command -v openssl >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends openssl
  else
    echo "OpenSSL is required for signed off-host backups and apt-get was not found." >&2
    exit 23
  fi
fi

validate_root_owned_credential_wrapper() {
  local wrapper_path="$1"
  local resolved_path
  local current_path
  local owner_id
  local mode

  if [ "${wrapper_path#/}" = "$wrapper_path" ] || [ ! -f "$wrapper_path" ] || [ ! -x "$wrapper_path" ] || [ -L "$wrapper_path" ]; then
    echo "Off-host credential wrapper must be an absolute, non-symlink executable file: $wrapper_path" >&2
    return 1
  fi
  resolved_path="$(realpath -e -- "$wrapper_path")" || return 1
  if [ "$resolved_path" != "$wrapper_path" ]; then
    echo "Off-host credential wrapper path must not contain symlink components: $wrapper_path" >&2
    return 1
  fi

  current_path="$wrapper_path"
  while :; do
    owner_id="$(stat -c '%u' -- "$current_path")" || return 1
    mode="$(stat -c '%a' -- "$current_path")" || return 1
    if [ "$owner_id" != "0" ] || (( (8#$mode & 0022) != 0 )); then
      echo "Off-host credential wrapper and every parent must be root-owned and not group/world writable: $current_path" >&2
      return 1
    fi
    [ "$current_path" = "/" ] && break
    current_path="$(dirname "$current_path")"
  done
}

validate_encrypted_systemd_credential() {
  local credential_path="$1"
  local owner_id
  local mode
  if [ ! -s "$credential_path" ] || [ -L "$credential_path" ]; then
    echo "Encrypted systemd credential is missing or empty: $credential_path" >&2
    return 1
  fi
  owner_id="$(stat -c '%u' -- "$credential_path")" || return 1
  mode="$(stat -c '%a' -- "$credential_path")" || return 1
  if [ "$owner_id" != "0" ] || (( (8#$mode & 0077) != 0 )); then
    echo "Encrypted systemd credential must be root-owned and mode 0600: $credential_path" >&2
    return 1
  fi
}

validate_manifest_signing_key_pair() {
  local credential_path="$1"
  local derived_public_key

  python3 - "$MANIFEST_SIGNING_PUBLIC_KEY_BASE64" <<'PY'
import base64
import sys

value = sys.argv[1]
try:
    decoded = base64.b64decode(value, validate=True)
except Exception as exc:
    raise SystemExit("Manifest signing public key is not canonical base64.") from exc
if base64.b64encode(decoded).decode("ascii") != value:
    raise SystemExit("Manifest signing public key is not canonical base64.")
if len(decoded) != 44 or decoded[:12].hex() != "302a300506032b6570032100":
    raise SystemExit("Manifest signing public key is not Ed25519 SPKI DER.")
PY

  derived_public_key="$({
    systemd-creds decrypt --name=manifest_signing_private_key "$credential_path" - |
      openssl pkey -pubout -outform DER 2>/dev/null |
      base64 -w0
  })" || return 1
  if [ "$derived_public_key" != "$MANIFEST_SIGNING_PUBLIC_KEY_BASE64" ]; then
    echo "Encrypted manifest signing private key does not match the configured public key." >&2
    return 1
  fi
}

validate_root_owned_directory_chain() {
  local directory_path="$1"
  local resolved_path
  local current_path
  local owner_id
  local mode

  [ -d "$directory_path" ] && [ ! -L "$directory_path" ] || return 1
  resolved_path="$(realpath -e -- "$directory_path")" || return 1
  [ "$resolved_path" = "$directory_path" ] || return 1
  current_path="$directory_path"
  while :; do
    owner_id="$(stat -c '%u' -- "$current_path")" || return 1
    mode="$(stat -c '%a' -- "$current_path")" || return 1
    if [ "$owner_id" != "0" ] || (( (8#$mode & 0022) != 0 )); then
      echo "Backup directory chain must be root-owned and not group/world writable: $current_path" >&2
      return 1
    fi
    [ "$current_path" = "/" ] && break
    current_path="$(dirname "$current_path")"
  done
}

if [ -n "$OFFHOST_S3_BUCKET" ]; then
  if ! command -v openssl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
    echo "OpenSSL and Python 3 are required for signed off-host backup manifests." >&2
    exit 28
  fi
  if ! command -v aws >/dev/null 2>&1; then
    echo "aws CLI is required before off-host backup scheduling can be installed." >&2
    exit 28
  fi
  if [ "$SYSTEMD_CREDENTIALS_MODE" = "1" ]; then
    if ! command -v systemctl >/dev/null 2>&1 || ! command -v systemd-creds >/dev/null 2>&1; then
      echo "systemd and systemd-creds are required for encrypted off-host credentials." >&2
      exit 24
    fi
    validate_encrypted_systemd_credential /etc/credstore.encrypted/shamrai-backup-aws-access-key-id.cred || exit 25
    validate_encrypted_systemd_credential /etc/credstore.encrypted/shamrai-backup-aws-secret-access-key.cred || exit 25
    validate_encrypted_systemd_credential /etc/credstore.encrypted/shamrai-backup-manifest-signing-private-key.cred || exit 25
    validate_manifest_signing_key_pair /etc/credstore.encrypted/shamrai-backup-manifest-signing-private-key.cred || exit 25
  else
    if [ -z "$OFFHOST_CREDENTIAL_WRAPPER" ]; then
      echo "Off-host S3 scheduling requires systemd credentials or an explicit credential wrapper path." >&2
      exit 24
    fi
    validate_root_owned_credential_wrapper "$OFFHOST_CREDENTIAL_WRAPPER" || exit 25
    if [ "$OFFHOST_CREDENTIAL_WRAPPER" = "$OPS_DIR/backup-db.sh" ] || {
      [ -e "$OPS_DIR/backup-db.sh" ] && [ "$OFFHOST_CREDENTIAL_WRAPPER" -ef "$OPS_DIR/backup-db.sh" ]
    }; then
      echo "Off-host credential wrapper must not be the generated backup script itself." >&2
      exit 27
    fi
  fi
elif [ -n "$OFFHOST_CREDENTIAL_WRAPPER" ]; then
  echo "Off-host credential wrapper requires off-host S3 configuration." >&2
  exit 26
fi

if [ -L "$OPS_DIR" ] || [ -L "$BACKUP_DIR" ]; then
  echo "Backup ops/data directories must not be symbolic links." >&2
  exit 29
fi
validate_root_owned_directory_chain "$REMOTE_PATH" || exit 29
install -d -o root -g root -m 0700 "$OPS_DIR"
install -d -o root -g root -m 0700 "$BACKUP_DIR" "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly"
validate_root_owned_directory_chain "$OPS_DIR" || exit 29
validate_root_owned_directory_chain "$BACKUP_DIR" || exit 29

backup_script_tmp="$(mktemp "$OPS_DIR/.backup-db.sh.XXXXXX")"
cat > "$backup_script_tmp" <<'BACKUP_SCRIPT'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$OPS_DIR/backup.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing backup config: $ENV_FILE" >&2
  exit 10
fi
if [ -L "$ENV_FILE" ] || [ "$(realpath -e -- "$ENV_FILE")" != "$ENV_FILE" ] || \
   [ "$(stat -c '%u' -- "$ENV_FILE")" != "0" ] || \
   (( (8#$(stat -c '%a' -- "$ENV_FILE") & 0077) != 0 )); then
  echo "Backup config must be a root-owned, non-symlink file with mode 0600: $ENV_FILE" >&2
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
: "${SHAMRAI_BACKUP_TIMEZONE:?missing SHAMRAI_BACKUP_TIMEZONE}"
: "${SHAMRAI_BACKUP_S3_BUCKET:=}"
: "${SHAMRAI_BACKUP_S3_PREFIX:=}"
: "${SHAMRAI_BACKUP_S3_ENDPOINT_URL:=}"
: "${SHAMRAI_BACKUP_S3_REGION:=}"
: "${SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64:=}"

if [ "$SHAMRAI_BACKUP_AGE_RECIPIENT" = "age1replacewithpublicrecipient" ]; then
  echo "Refusing to run backup with placeholder age recipient." >&2
  exit 11
fi

if ! command -v age >/dev/null 2>&1; then
  echo "age is required for encrypted backups." >&2
  exit 12
fi
if ! command -v flock >/dev/null 2>&1; then
  echo "flock is required to prevent concurrent backup writers." >&2
  exit 14
fi

if [ -n "$SHAMRAI_BACKUP_S3_BUCKET" ]; then
  : "${SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64:?missing SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64}"
  : "${SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE:?missing SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE}"
  if ! command -v openssl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then
    echo "OpenSSL and Python 3 are required for signed off-host backup manifests." >&2
    exit 12
  fi
  case "$SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE" in
    /run/credentials/*/manifest_signing_private_key) ;;
    *)
      echo "Manifest signing private key must come from the systemd credential directory." >&2
      exit 19
      ;;
  esac
  if [ ! -s "$SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE" ] || \
     [ ! -f "$SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE" ] || \
     [ -L "$SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE" ] || \
     [ "$(realpath -e -- "$SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE")" != "$SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE" ] || \
     [ "$(stat -c '%u' -- "$SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE")" != "0" ] || \
     (( (8#$(stat -c '%a' -- "$SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE") & 0077) != 0 )); then
    echo "Manifest signing private key credential failed path, ownership, or mode validation." >&2
    exit 19
  fi
fi

exec 9>"$OPS_DIR/backup.lock"
if ! flock -n 9; then
  echo "Another Shamrai backup process already holds the exclusive lock." >&2
  if command -v logger >/dev/null 2>&1; then
    logger -p user.err -t shamrai-backup "concurrent backup rejected"
  fi
  exit 15
fi

offhost_key() {
  local relative_key="$1"
  if [ -n "$SHAMRAI_BACKUP_S3_PREFIX" ]; then
    printf '%s/%s' "$SHAMRAI_BACKUP_S3_PREFIX" "$relative_key"
  else
    printf '%s' "$relative_key"
  fi
}

prepare_manifest_signing_key() {
  local public_der_path="$1"
  local derived_public_der_path="$2"

  python3 - "$public_der_path" "$SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64" <<'PY'
import base64
import sys
from pathlib import Path

path = Path(sys.argv[1])
value = sys.argv[2]
try:
    decoded = base64.b64decode(value, validate=True)
except Exception as exc:
    raise SystemExit("Manifest signing public key is not canonical base64.") from exc
if base64.b64encode(decoded).decode("ascii") != value:
    raise SystemExit("Manifest signing public key is not canonical base64.")
if len(decoded) != 44 or decoded[:12].hex() != "302a300506032b6570032100":
    raise SystemExit("Manifest signing public key is not Ed25519 SPKI DER.")
path.write_bytes(decoded)
PY
  openssl pkey \
    -in "$SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE" \
    -pubout -outform DER -out "$derived_public_der_path"
  if ! cmp -s -- "$public_der_path" "$derived_public_der_path"; then
    echo "Manifest signing private key does not match the configured public key." >&2
    return 1
  fi
  signing_public_key_sha256="$(sha256sum "$public_der_path" | awk '{print $1}')"
}

verify_offhost_object() {
  local object_key="$1"
  local expected_size="$2"
  local expected_hash="$3"
  shift 3
  local -a aws_global=("$@")
  local actual_size
  local actual_hash

  actual_size="$(aws "${aws_global[@]}" s3api head-object --bucket "$SHAMRAI_BACKUP_S3_BUCKET" --key "$object_key" --query 'ContentLength' --output text)" || return $?
  actual_hash="$(aws "${aws_global[@]}" s3api head-object --bucket "$SHAMRAI_BACKUP_S3_BUCKET" --key "$object_key" --query 'Metadata.sha256' --output text)" || return $?
  if [ "$actual_size" != "$expected_size" ] || [ "${actual_hash,,}" != "${expected_hash,,}" ]; then
    echo "Off-host integrity metadata mismatch for key $object_key." >&2
    return 1
  fi
}

upload_offhost_bundle() {
  local encrypted_file="$1"
  local manifest_file="$2"
  local signature_file="$3"
  local encrypted_hash="$4"
  local encrypted_size="$5"
  local manifest_hash="$6"
  local manifest_size="$7"
  local signature_hash="$8"
  local signature_size="$9"

  [ -n "$SHAMRAI_BACKUP_S3_BUCKET" ] || return 0
  if [ ! -s "$encrypted_file" ] || [ ! -s "$manifest_file" ] || [ ! -s "$signature_file" ]; then
    echo "Off-host upload requires a non-empty encrypted dump, manifest, and Ed25519 signature." >&2
    return 1
  fi

  if [ -z "${AWS_ACCESS_KEY_ID:-}" ] || [ -z "${AWS_SECRET_ACCESS_KEY:-}" ]; then
    echo "Off-host upload requires runtime AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY." >&2
    return 16
  fi
  if ! command -v aws >/dev/null 2>&1; then
    echo "aws CLI is required when off-host S3 upload is enabled." >&2
    return 1
  fi

  export AWS_EC2_METADATA_DISABLED=true
  export AWS_PAGER=""
  local -a aws_global=()
  if [ -n "$SHAMRAI_BACKUP_S3_ENDPOINT_URL" ]; then
    aws_global+=(--endpoint-url "$SHAMRAI_BACKUP_S3_ENDPOINT_URL")
  fi
  if [ -n "$SHAMRAI_BACKUP_S3_REGION" ]; then
    aws_global+=(--region "$SHAMRAI_BACKUP_S3_REGION")
  fi

  local artifact_key
  local manifest_key
  local signature_key
  artifact_key="$(offhost_key "daily/$(basename "$encrypted_file")")"
  manifest_key="$(offhost_key "daily/$(basename "$manifest_file")")"
  signature_key="$(offhost_key "daily/$(basename "$signature_file")")"

  # The signature is the commit marker: verify ciphertext and manifest first.
  aws "${aws_global[@]}" s3 cp "$encrypted_file" "s3://$SHAMRAI_BACKUP_S3_BUCKET/$artifact_key" \
    --only-show-errors --no-progress --content-type application/octet-stream --metadata "sha256=$encrypted_hash" || return $?
  verify_offhost_object "$artifact_key" "$encrypted_size" "$encrypted_hash" "${aws_global[@]}" || return $?

  aws "${aws_global[@]}" s3 cp "$manifest_file" "s3://$SHAMRAI_BACKUP_S3_BUCKET/$manifest_key" \
    --only-show-errors --no-progress --content-type application/json --metadata "sha256=$manifest_hash" || return $?
  verify_offhost_object "$manifest_key" "$manifest_size" "$manifest_hash" "${aws_global[@]}" || return $?

  aws "${aws_global[@]}" s3 cp "$signature_file" "s3://$SHAMRAI_BACKUP_S3_BUCKET/$signature_key" \
    --only-show-errors --no-progress --content-type application/octet-stream --metadata "sha256=$signature_hash" || return $?
  verify_offhost_object "$signature_key" "$signature_size" "$signature_hash" "${aws_global[@]}" || return $?
  echo "BACKUP_OFFHOST_UPLOADED=$artifact_key"
}

daily_dir="$SHAMRAI_BACKUP_DIR/daily"
weekly_dir="$SHAMRAI_BACKUP_DIR/weekly"
mkdir -p "$daily_dir" "$weekly_dir"
chmod 0700 "$SHAMRAI_BACKUP_DIR" "$daily_dir" "$weekly_dir"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="shamrai-db.$timestamp"
tmp_dir="$(mktemp -d "$SHAMRAI_BACKUP_DIR/.tmp.$timestamp.XXXXXX")"
dump_path="$tmp_dir/$base.dump"
counts_path="$tmp_dir/$base.counts.tsv"
manifest_tmp="$tmp_dir/$base.manifest.json"
signature_tmp="$tmp_dir/$base.manifest.sig"
encrypted_tmp="$tmp_dir/$base.dump.age"
public_der_tmp="$tmp_dir/manifest-signing-public.der"
derived_public_der_tmp="$tmp_dir/manifest-signing-derived-public.der"
manifest_path="$daily_dir/$base.manifest.json"
signature_path="$daily_dir/$base.manifest.sig"
encrypted_path="$daily_dir/$base.dump.age"
signing_public_key_sha256=""
snapshot_pid=""
snapshot_read_fd=""
snapshot_write_fd=""

close_consistent_snapshot() {
  if [ -n "$snapshot_pid" ]; then
    printf 'ROLLBACK;\n\\q\n' >&"$snapshot_write_fd" 2>/dev/null || true
    wait "$snapshot_pid" 2>/dev/null || true
    exec {snapshot_read_fd}<&- 2>/dev/null || true
    exec {snapshot_write_fd}>&- 2>/dev/null || true
    snapshot_pid=""
  fi
}

cleanup() {
  local status="$?"
  trap - EXIT
  close_consistent_snapshot
  rm -rf "$tmp_dir"
  if [ "$status" -ne 0 ] && command -v logger >/dev/null 2>&1; then
    logger -p user.err -t shamrai-backup "backup pipeline failed (exit=$status)"
  fi
  exit "$status"
}
trap cleanup EXIT

if [ -n "$SHAMRAI_BACKUP_S3_BUCKET" ]; then
  prepare_manifest_signing_key "$public_der_tmp" "$derived_public_der_tmp"
fi

cd "$SHAMRAI_BACKUP_REMOTE_PATH"

coproc SNAPSHOT_HOLDER {
  docker compose -p "$SHAMRAI_BACKUP_COMPOSE_PROJECT" exec -T postgres \
    sh -c 'exec psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
}
snapshot_pid="$SNAPSHOT_HOLDER_PID"
snapshot_read_fd="${SNAPSHOT_HOLDER[0]}"
snapshot_write_fd="${SNAPSHOT_HOLDER[1]}"
printf 'BEGIN ISOLATION LEVEL REPEATABLE READ;\nSELECT pg_export_snapshot();\n' >&"$snapshot_write_fd"
IFS= read -r snapshot_id <&"$snapshot_read_fd"
if [[ ! "$snapshot_id" =~ ^[0-9A-F]+-[0-9A-F]+-[0-9]+$ ]]; then
  echo "PostgreSQL returned an invalid exported snapshot identifier." >&2
  exit 17
fi

docker compose -p "$SHAMRAI_BACKUP_COMPOSE_PROJECT" exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc --snapshot="$1"' sh "$snapshot_id" > "$dump_path"

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
  snapshot_prefix="begin isolation level repeatable read; set transaction snapshot '$snapshot_id';"
  exists_sql="$snapshot_prefix select to_regclass('public.${table}') is not null;"
  exists="$(printf '%s\n' "$exists_sql" | docker compose -p "$SHAMRAI_BACKUP_COMPOSE_PROJECT" exec -T postgres sh -c 'psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | tr -d '[:space:]')"
  if [ "$exists" = "t" ]; then
    count_sql="$snapshot_prefix select count(*) from public.${table};"
    count="$(printf '%s\n' "$count_sql" | docker compose -p "$SHAMRAI_BACKUP_COMPOSE_PROJECT" exec -T postgres sh -c 'psql -X -qAt -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | tr -d '[:space:]')"
  else
    count="0"
  fi
  printf '%s\t%s\n' "$table" "$count" >> "$counts_path"
done
close_consistent_snapshot

dump_sha256="$(sha256sum "$dump_path" | awk '{print $1}')"
dump_size_bytes="$(wc -c < "$dump_path" | tr -d '[:space:]')"

age -r "$SHAMRAI_BACKUP_AGE_RECIPIENT" -o "$encrypted_tmp" "$dump_path"
encrypted_sha256="$(sha256sum "$encrypted_tmp" | awk '{print $1}')"
encrypted_size_bytes="$(wc -c < "$encrypted_tmp" | tr -d '[:space:]')"

artifact_object_key="$(offhost_key "daily/$base.dump.age")"
manifest_object_key="$(offhost_key "daily/$base.manifest.json")"
signature_object_key="$(offhost_key "daily/$base.manifest.sig")"
python3 - "$manifest_tmp" "$timestamp" "$dump_sha256" "$dump_size_bytes" "$encrypted_sha256" "$encrypted_size_bytes" "$counts_path" "$SHAMRAI_BACKUP_COMPOSE_PROJECT" "$SHAMRAI_BACKUP_REMOTE_PATH" "$base" "$signing_public_key_sha256" "$SHAMRAI_BACKUP_S3_BUCKET" "$SHAMRAI_BACKUP_S3_PREFIX" "$artifact_object_key" "$manifest_object_key" "$signature_object_key" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
timestamp = sys.argv[2]
dump_sha256 = sys.argv[3]
dump_size_bytes = int(sys.argv[4])
encrypted_sha256 = sys.argv[5]
encrypted_size_bytes = int(sys.argv[6])
counts_path = Path(sys.argv[7])
compose_project = sys.argv[8]
remote_path = sys.argv[9]
base = sys.argv[10]
signing_public_key_sha256 = sys.argv[11]
s3_bucket = sys.argv[12]
s3_prefix = sys.argv[13]
artifact_object_key = sys.argv[14]
manifest_object_key = sys.argv[15]
signature_object_key = sys.argv[16]

table_counts: dict[str, int] = {}
for line in counts_path.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    table, count = line.split("\t", 1)
    table_counts[table] = int(count)

payload = {
    "schema_version": 3 if signing_public_key_sha256 else 2,
    "manifest_type": "shamrai_postgres_backup",
    "created_at": timestamp,
    "backup_id": base,
    "source": {
        "compose_project": compose_project,
        "remote_path": remote_path,
        "database": "postgres",
    },
    "artifact": {
        "base_name": base,
        "encrypted_file": f"{base}.dump.age",
        "encrypted_sha256": encrypted_sha256,
        "encrypted_size_bytes": encrypted_size_bytes,
        "dump_sha256": dump_sha256,
        "dump_size_bytes": dump_size_bytes,
        "format": "pg_dump custom",
        "encryption": "age",
    },
    "table_counts": table_counts,
}
if signing_public_key_sha256:
    payload["storage"] = {
        "bucket": s3_bucket,
        "prefix": s3_prefix,
        "artifact_key": artifact_object_key,
        "manifest_key": manifest_object_key,
        "signature_key": signature_object_key,
    }
    payload["signature"] = {
        "algorithm": "Ed25519",
        "public_key_sha256": signing_public_key_sha256,
    }

manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

if [ ! -s "$encrypted_tmp" ] || [ ! -s "$manifest_tmp" ]; then
  echo "Encrypted backup or manifest was not created." >&2
  exit 13
fi
python3 -m json.tool "$manifest_tmp" >/dev/null

if [ -n "$SHAMRAI_BACKUP_S3_BUCKET" ]; then
  openssl pkeyutl -sign -rawin \
    -inkey "$SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE" \
    -in "$manifest_tmp" -out "$signature_tmp"
  if [ "$(wc -c < "$signature_tmp" | tr -d '[:space:]')" != "64" ]; then
    echo "Ed25519 manifest signature was not exactly 64 bytes." >&2
    exit 13
  fi
  openssl pkeyutl -verify -rawin -pubin -keyform DER \
    -inkey "$public_der_tmp" -in "$manifest_tmp" -sigfile "$signature_tmp" >/dev/null
fi

chmod 0600 "$encrypted_tmp" "$manifest_tmp"
[ ! -e "$signature_tmp" ] || chmod 0600 "$signature_tmp"
mv -f -- "$encrypted_tmp" "$encrypted_path"
mv -f -- "$manifest_tmp" "$manifest_path"
if [ -n "$SHAMRAI_BACKUP_S3_BUCKET" ]; then
  mv -f -- "$signature_tmp" "$signature_path"
fi
rm -f "$dump_path" "$counts_path"
ln -sfn "daily/$base.dump.age" "$SHAMRAI_BACKUP_DIR/latest.dump.age"
ln -sfn "daily/$base.manifest.json" "$SHAMRAI_BACKUP_DIR/latest.manifest.json"
if [ -n "$SHAMRAI_BACKUP_S3_BUCKET" ]; then
  ln -sfn "daily/$base.manifest.sig" "$SHAMRAI_BACKUP_DIR/latest.manifest.sig"
fi

if [ "$(TZ="$SHAMRAI_BACKUP_TIMEZONE" date +%u)" = "7" ]; then
  cp -p "$encrypted_path" "$weekly_dir/$base.dump.age"
  cp -p "$manifest_path" "$weekly_dir/$base.manifest.json"
  if [ -n "$SHAMRAI_BACKUP_S3_BUCKET" ]; then
    cp -p "$signature_path" "$weekly_dir/$base.manifest.sig"
  fi
fi

manifest_sha256="$(sha256sum "$manifest_path" | awk '{print $1}')"
manifest_size_bytes="$(wc -c < "$manifest_path" | tr -d '[:space:]')"
signature_sha256=""
signature_size_bytes="0"
if [ -s "$signature_path" ]; then
  signature_sha256="$(sha256sum "$signature_path" | awk '{print $1}')"
  signature_size_bytes="$(wc -c < "$signature_path" | tr -d '[:space:]')"
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
    rm -f "$file" "${file%.dump.age}.manifest.json" "${file%.dump.age}.manifest.sig"
  done
}

offhost_status=0
upload_offhost_bundle \
  "$encrypted_path" \
  "$manifest_path" \
  "$signature_path" \
  "$encrypted_sha256" \
  "$encrypted_size_bytes" \
  "$manifest_sha256" \
  "$manifest_size_bytes" \
  "$signature_sha256" \
  "$signature_size_bytes" || offhost_status=$?

prune_dir "$daily_dir" "$SHAMRAI_BACKUP_RETENTION_DAILY"
prune_dir "$weekly_dir" "$SHAMRAI_BACKUP_RETENTION_WEEKLY"

if [ "$offhost_status" -ne 0 ]; then
  echo "Local backup retention completed after off-host upload failure (exit=$offhost_status)." >&2
  exit "$offhost_status"
fi

echo "BACKUP_CREATED=$encrypted_path"
echo "BACKUP_MANIFEST=$manifest_path"
[ ! -s "$signature_path" ] || echo "BACKUP_MANIFEST_SIGNATURE=$signature_path"
BACKUP_SCRIPT

install -o root -g root -m 0700 "$backup_script_tmp" "$OPS_DIR/backup-db.sh"
rm -f -- "$backup_script_tmp"

env_tmp="$(mktemp "$OPS_DIR/.backup.env.XXXXXX")"
{
  printf 'SHAMRAI_BACKUP_REMOTE_PATH=%q\n' "$REMOTE_PATH"
  printf 'SHAMRAI_BACKUP_COMPOSE_PROJECT=%q\n' "$COMPOSE_PROJECT"
  printf 'SHAMRAI_BACKUP_DIR=%q\n' "$BACKUP_DIR"
  printf 'SHAMRAI_BACKUP_AGE_RECIPIENT=%q\n' "$BACKUP_AGE_RECIPIENT"
  printf 'SHAMRAI_BACKUP_RETENTION_DAILY=%q\n' "$RETENTION_DAILY"
  printf 'SHAMRAI_BACKUP_RETENTION_WEEKLY=%q\n' "$RETENTION_WEEKLY"
  printf 'SHAMRAI_BACKUP_TIMEZONE=%q\n' "$CRON_TZ_VALUE"
  printf 'SHAMRAI_BACKUP_S3_BUCKET=%q\n' "$OFFHOST_S3_BUCKET"
  printf 'SHAMRAI_BACKUP_S3_PREFIX=%q\n' "$OFFHOST_S3_PREFIX"
  printf 'SHAMRAI_BACKUP_S3_ENDPOINT_URL=%q\n' "$OFFHOST_S3_ENDPOINT_URL"
  printf 'SHAMRAI_BACKUP_S3_REGION=%q\n' "$OFFHOST_S3_REGION"
  printf 'SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64=%q\n' "$MANIFEST_SIGNING_PUBLIC_KEY_BASE64"
} > "$env_tmp"
install -o root -g root -m 0600 "$env_tmp" "$ENV_FILE"
rm -f -- "$env_tmp"

if [ "$SYSTEMD_CREDENTIALS_MODE" = "1" ]; then
  cat > /usr/local/sbin/shamrai-backup-with-systemd-credentials.tmp <<'CREDENTIAL_WRAPPER'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
: "${CREDENTIALS_DIRECTORY:?systemd credential directory is unavailable}"
export AWS_ACCESS_KEY_ID="$(<"$CREDENTIALS_DIRECTORY/aws_access_key_id")"
export AWS_SECRET_ACCESS_KEY="$(<"$CREDENTIALS_DIRECTORY/aws_secret_access_key")"
export SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE="$CREDENTIALS_DIRECTORY/manifest_signing_private_key"
if [ -s "$CREDENTIALS_DIRECTORY/aws_session_token" ]; then
  export AWS_SESSION_TOKEN="$(<"$CREDENTIALS_DIRECTORY/aws_session_token")"
fi
exec "$@"
CREDENTIAL_WRAPPER
  install -o root -g root -m 0700 \
    /usr/local/sbin/shamrai-backup-with-systemd-credentials.tmp \
    /usr/local/sbin/shamrai-backup-with-systemd-credentials
  rm -f /usr/local/sbin/shamrai-backup-with-systemd-credentials.tmp

  session_credential_line=""
  if [ -s /etc/credstore.encrypted/shamrai-backup-aws-session-token.cred ]; then
    session_credential_line='LoadCredentialEncrypted=aws_session_token:/etc/credstore.encrypted/shamrai-backup-aws-session-token.cred'
  fi
  cat > /etc/systemd/system/shamrai-db-backup.service.tmp <<EOF
[Unit]
Description=Encrypted Shamrai PostgreSQL backup
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
UMask=0077
LoadCredentialEncrypted=aws_access_key_id:/etc/credstore.encrypted/shamrai-backup-aws-access-key-id.cred
LoadCredentialEncrypted=aws_secret_access_key:/etc/credstore.encrypted/shamrai-backup-aws-secret-access-key.cred
LoadCredentialEncrypted=manifest_signing_private_key:/etc/credstore.encrypted/shamrai-backup-manifest-signing-private-key.cred
$session_credential_line
ExecStart=/usr/local/sbin/shamrai-backup-with-systemd-credentials $OPS_DIR/backup-db.sh
NoNewPrivileges=true
PrivateTmp=true
EOF
  install -o root -g root -m 0644 \
    /etc/systemd/system/shamrai-db-backup.service.tmp \
    /etc/systemd/system/shamrai-db-backup.service
  rm -f /etc/systemd/system/shamrai-db-backup.service.tmp

  cat > /etc/systemd/system/shamrai-db-backup.timer.tmp <<'EOF'
[Unit]
Description=Nightly encrypted Shamrai PostgreSQL backup

[Timer]
OnCalendar=*-*-* __BACKUP_HOUR_PADDED__:__BACKUP_MINUTE_PADDED__:00 __CRON_TZ_RAW__
Persistent=true
RandomizedDelaySec=0
Unit=shamrai-db-backup.service

[Install]
WantedBy=timers.target
EOF
  install -o root -g root -m 0644 \
    /etc/systemd/system/shamrai-db-backup.timer.tmp \
    /etc/systemd/system/shamrai-db-backup.timer
  rm -f /etc/systemd/system/shamrai-db-backup.timer.tmp /etc/cron.d/shamrai-db-backup
  systemctl daemon-reload
  systemctl enable --now shamrai-db-backup.timer
else
  if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now shamrai-db-backup.timer 2>/dev/null || true
  fi
  rm -f /etc/systemd/system/shamrai-db-backup.service /etc/systemd/system/shamrai-db-backup.timer
  printf -v backup_script_command '%q' "$OPS_DIR/backup-db.sh"
  printf -v backup_log_command '%q' "$OPS_DIR/backup.log"
  cron_backup_command="$backup_script_command"
  if [ -n "$OFFHOST_S3_BUCKET" ]; then
    printf -v credential_wrapper_command '%q' "$OFFHOST_CREDENTIAL_WRAPPER"
    cron_backup_command="$credential_wrapper_command -- $backup_script_command"
  fi

  cat > /etc/cron.d/shamrai-db-backup.tmp <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
CRON_TZ=$CRON_TZ_VALUE
$BACKUP_MINUTE $BACKUP_HOUR * * * root $cron_backup_command >> $backup_log_command 2>&1
EOF
  install -m 0644 /etc/cron.d/shamrai-db-backup.tmp /etc/cron.d/shamrai-db-backup
  rm -f /etc/cron.d/shamrai-db-backup.tmp

  if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
    systemctl reload cron 2>/dev/null || systemctl reload crond 2>/dev/null || true
  fi
fi

if [ "$RUN_BACKUP_NOW" = "1" ]; then
  if [ "$SYSTEMD_CREDENTIALS_MODE" = "1" ]; then
    systemctl start shamrai-db-backup.service
  elif [ -n "$OFFHOST_S3_BUCKET" ]; then
    "$OFFHOST_CREDENTIAL_WRAPPER" -- "$OPS_DIR/backup-db.sh"
  else
    "$OPS_DIR/backup-db.sh"
  fi
fi

echo "SHAMRAI_BACKUP_CONFIGURED=$ENV_FILE"
if [ "$SYSTEMD_CREDENTIALS_MODE" = "1" ]; then
  echo "SHAMRAI_BACKUP_TIMER=shamrai-db-backup.timer"
else
  echo "SHAMRAI_BACKUP_CRON=/etc/cron.d/shamrai-db-backup"
fi
'@

  $result = $template.Replace("__REMOTE_PATH__", (ConvertTo-ShellSingleQuoted $RemoteAppPath))
  $result = $result.Replace("__COMPOSE_PROJECT__", (ConvertTo-ShellSingleQuoted $Project))
  $result = $result.Replace("__AGE_RECIPIENT__", (ConvertTo-ShellSingleQuoted $AgeRecipient))
  $result = $result.Replace("__RETENTION_DAILY__", "$DailyKeep")
  $result = $result.Replace("__RETENTION_WEEKLY__", "$WeeklyKeep")
  $result = $result.Replace("__BACKUP_HOUR__", "$Hour")
  $result = $result.Replace("__BACKUP_MINUTE__", "$Minute")
  $result = $result.Replace("__CRON_TZ__", (ConvertTo-ShellSingleQuoted $Timezone))
  $result = $result.Replace("__OFFHOST_S3_BUCKET__", (ConvertTo-ShellSingleQuoted $S3Bucket))
  $result = $result.Replace("__OFFHOST_S3_PREFIX__", (ConvertTo-ShellSingleQuoted $S3Prefix))
  $result = $result.Replace("__OFFHOST_S3_ENDPOINT_URL__", (ConvertTo-ShellSingleQuoted $S3EndpointUrl))
  $result = $result.Replace("__OFFHOST_S3_REGION__", (ConvertTo-ShellSingleQuoted $S3Region))
  $result = $result.Replace("__MANIFEST_SIGNING_PUBLIC_KEY_BASE64__", (ConvertTo-ShellSingleQuoted $SigningPublicKeyBase64))
  $result = $result.Replace("__OFFHOST_CREDENTIAL_WRAPPER__", (ConvertTo-ShellSingleQuoted $CredentialWrapper))
  $result = $result.Replace("__SYSTEMD_CREDENTIALS_MODE__", (ConvertTo-ShellSingleQuoted $systemdCredentialsValue))
  $result = $result.Replace("__BACKUP_HOUR_PADDED__", $hourPadded)
  $result = $result.Replace("__BACKUP_MINUTE_PADDED__", $minutePadded)
  $result = $result.Replace("__CRON_TZ_RAW__", $Timezone)
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

function Invoke-RemoteOutputChecked {
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
  return Invoke-NativeOutputChecked -FilePath $script:SshTool -Arguments $args
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
if (
  [string]::IsNullOrWhiteSpace($CronTimezone) -or
  $CronTimezone -notmatch '^[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)+$' -or
  $CronTimezone -match '(^|/)\.\.?(/|$)'
) {
  throw "CronTimezone must be a normalized IANA timezone such as Europe/Moscow."
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

$offHostEnabled = -not [string]::IsNullOrWhiteSpace($OffHostS3Bucket)
if ($offHostEnabled) {
  $OffHostS3Bucket = $OffHostS3Bucket.Trim()
  if (
    $OffHostS3Bucket.Length -lt 3 -or
    $OffHostS3Bucket.Length -gt 63 -or
    $OffHostS3Bucket -notmatch '^[a-z0-9][a-z0-9.-]*[a-z0-9]$' -or
    $OffHostS3Bucket.Contains("..") -or
    $OffHostS3Bucket -match '^\d{1,3}(\.\d{1,3}){3}$'
  ) {
    throw "OffHostS3Bucket must be a valid S3-compatible bucket name."
  }

  if ([string]::IsNullOrWhiteSpace($OffHostS3Prefix)) {
    $OffHostS3Prefix = "shamrai"
  } else {
    $OffHostS3Prefix = $OffHostS3Prefix.Trim().Trim("/")
  }
  if (
    $OffHostS3Prefix.Length -gt 256 -or
    $OffHostS3Prefix -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*$' -or
    $OffHostS3Prefix -match '(^|/)\.\.?(/|$)' -or
    $OffHostS3Prefix.Contains("//")
  ) {
    throw "OffHostS3Prefix must contain only safe key characters and no traversal segments."
  }

  if (-not [string]::IsNullOrWhiteSpace($OffHostS3EndpointUrl)) {
    $endpointUri = $null
    if (
      -not [Uri]::TryCreate($OffHostS3EndpointUrl, [UriKind]::Absolute, [ref]$endpointUri) -or
      $endpointUri.Scheme -ne "https" -or
      -not [string]::IsNullOrWhiteSpace($endpointUri.UserInfo) -or
      -not [string]::IsNullOrWhiteSpace($endpointUri.Query) -or
      -not [string]::IsNullOrWhiteSpace($endpointUri.Fragment)
    ) {
      throw "OffHostS3EndpointUrl must be an absolute HTTPS URL without credentials, query, or fragment."
    }
    $OffHostS3EndpointUrl = $endpointUri.AbsoluteUri.TrimEnd("/")
  } else {
    $OffHostS3EndpointUrl = ""
  }

  if ([string]::IsNullOrWhiteSpace($OffHostS3Region)) {
    $OffHostS3Region = "us-east-1"
  }
  if ($OffHostS3Region -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,62}$') {
    throw "OffHostS3Region contains unsupported characters."
  }

  if (-not [string]::IsNullOrWhiteSpace($OffHostCredentialWrapper)) {
    throw "OffHostCredentialWrapper is disabled for S3 release backups; install encrypted systemd credentials instead."
  }
  $OffHostCredentialWrapper = ""
  if ([string]::IsNullOrWhiteSpace($ManifestSigningPublicKeyBase64)) {
    if ($DryRun) {
      # RFC 8032 test-vector public key; public and used only for command generation.
      $ManifestSigningPublicKeyBase64 = "MCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="
    } else {
      throw "Set SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64 or pass -ManifestSigningPublicKeyBase64 for S3 backups."
    }
  }
  ConvertFrom-ShamraiEd25519PublicKeyBase64 -Value $ManifestSigningPublicKeyBase64 | Out-Null
} else {
  if (-not [string]::IsNullOrWhiteSpace($OffHostCredentialWrapper)) {
    throw "OffHostCredentialWrapper requires OffHostS3Bucket. Local-only backups use the backup script directly."
  }
  $OffHostS3Bucket = ""
  $OffHostS3Prefix = ""
  $OffHostS3EndpointUrl = ""
  $OffHostS3Region = ""
  $OffHostCredentialWrapper = ""
  $ManifestSigningPublicKeyBase64 = ""
}

$useSystemdCredentials = $offHostEnabled

if ($offHostEnabled) {
  if ([string]::IsNullOrWhiteSpace($S3RetentionAttestationPath)) {
    $S3RetentionAttestationPath = Join-Path $Workspace ".deploy\s3-retention-attestation.json"
  }
  if (-not $DryRun) {
    Assert-S3RetentionAttestation `
      -Path $S3RetentionAttestationPath `
      -ExpectedBucket $OffHostS3Bucket `
      -ExpectedPrefix $OffHostS3Prefix `
      -ExpectedEndpointUrl $OffHostS3EndpointUrl `
      -ExpectedRegion $OffHostS3Region
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
  -S3Bucket $OffHostS3Bucket `
  -S3Prefix $OffHostS3Prefix `
  -S3EndpointUrl $OffHostS3EndpointUrl `
  -S3Region $OffHostS3Region `
  -SigningPublicKeyBase64 $ManifestSigningPublicKeyBase64 `
  -CredentialWrapper $OffHostCredentialWrapper `
  -SystemdCredentials ([bool]$useSystemdCredentials) `
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
  "__OFFHOST_S3_BUCKET__",
  "__OFFHOST_S3_PREFIX__",
  "__OFFHOST_S3_ENDPOINT_URL__",
  "__OFFHOST_S3_REGION__",
  "__MANIFEST_SIGNING_PUBLIC_KEY_BASE64__",
  "__OFFHOST_CREDENTIAL_WRAPPER__",
  "__SYSTEMD_CREDENTIALS_MODE__",
  "__BACKUP_HOUR_PADDED__",
  "__BACKUP_MINUTE_PADDED__",
  "__CRON_TZ_RAW__",
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
  Write-Host "age_recipient_configured=$(-not [string]::IsNullOrWhiteSpace($BackupAgeRecipient))"
  if ($offHostEnabled) {
    $artifactPreviewKey = Get-OffHostS3Key -Prefix $OffHostS3Prefix -RelativeKey "daily/<backup>.dump.age"
    $manifestPreviewKey = Get-OffHostS3Key -Prefix $OffHostS3Prefix -RelativeKey "daily/<backup>.manifest.json"
    $signaturePreviewKey = Get-OffHostS3Key -Prefix $OffHostS3Prefix -RelativeKey "daily/<backup>.manifest.sig"
    Write-Host "off_host=enabled"
    Write-Host "off_host_upload_order=encrypted_dump,manifest,signature"
    Write-Host "off_host_artifact_uri=$(Get-OffHostS3Uri -Bucket $OffHostS3Bucket -Key $artifactPreviewKey)"
    Write-Host "off_host_manifest_uri=$(Get-OffHostS3Uri -Bucket $OffHostS3Bucket -Key $manifestPreviewKey)"
    Write-Host "off_host_signature_uri=$(Get-OffHostS3Uri -Bucket $OffHostS3Bucket -Key $signaturePreviewKey)"
    Write-Host "manifest_signing=ed25519_systemd_credential"
    Write-Host "off_host_credentials=runtime_environment_only"
    Write-Host "s3_retention_gate=required_before_apply"
    if ($useSystemdCredentials) {
      Write-Host "off_host_schedule=systemd_credentials"
      Write-Host "systemd_service=shamrai-db-backup.service"
      Write-Host "systemd_timer=shamrai-db-backup.timer"
      Write-Host "credential_store=/etc/credstore.encrypted"
    } else {
      Write-Host "off_host_schedule=credential_wrapper"
      Write-Host "off_host_credential_wrapper=$OffHostCredentialWrapper"
    }
  } else {
    Write-Host "off_host=disabled"
    Write-Host "backup_schedule=direct"
    Write-Host "cron_file=/etc/cron.d/shamrai-db-backup"
  }
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
$remoteStage = ""
$remoteInstaller = ""

Set-Utf8NoBomLfContent -Path $localInstaller -Content $installerContent

try {
  Invoke-Step "Create root-private remote backup setup stage" {
    $stageOutput = Invoke-RemoteOutputChecked -Command "stage=`$(mktemp -d /tmp/shamrai-configure-backups.XXXXXX); chmod 0700 `"`$stage`"; realpath -e -- `"`$stage`""
    $stageCandidates = @($stageOutput -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^/tmp/shamrai-configure-backups\.[A-Za-z0-9]{6}$' })
    if ($stageCandidates.Count -ne 1) {
      throw "Remote mktemp did not return one validated backup setup directory."
    }
    $remoteStage = $stageCandidates[0]
    $remoteInstaller = "$remoteStage/install.sh"
  }

  Invoke-Step "Upload backup installer" {
    Copy-ToRemoteChecked -LocalPath $localInstaller -RemoteFilePath $remoteInstaller
  }

  Invoke-Step "Configure encrypted nightly backups" {
    $command = "chmod 0700 $(ConvertTo-ShellSingleQuoted $remoteInstaller) && bash $(ConvertTo-ShellSingleQuoted $remoteInstaller)"
    Invoke-RemoteChecked -Command $command
  }
} finally {
  if ($remoteStage -match '^/tmp/shamrai-configure-backups\.[A-Za-z0-9]{6}$') {
    Invoke-RemoteChecked -Command "rm -rf -- $(ConvertTo-ShellSingleQuoted $remoteStage)"
  }
}

Write-Host ""
Write-Host "shamrai_backup_configuration_ok" -ForegroundColor Green
