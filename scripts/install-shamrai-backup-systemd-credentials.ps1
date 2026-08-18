param(
  [string]$Workspace = "C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)",
  [string]$Server = "root@82.147.67.245",
  [int]$SshPort = 22,
  [string]$HostKeyFingerprint = "SHA256:xdVRtRaXWqK6eAIsE3VwD0o2H6GJDcCm65L1ZUBjuMw",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ManifestSigningPublicKeyBase64 = $env:SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64,
  [string]$SshKeyPath = $env:SHAMRAI_SSH_KEY_PATH,
  [string]$KnownHostsPath = "",
  [switch]$NoHostKeyScan,
  [switch]$BootstrapManifestSigningKey,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$manifestSignatureModule = Join-Path $PSScriptRoot "lib/BackupManifestSignature.psm1"
Import-Module -Name $manifestSignatureModule -Force -ErrorAction Stop

# S3 credentials are intentionally environment-only. Named parameters would put
# their plaintext values in PowerShell history and the process command line.
$AccessKeyId = [Environment]::GetEnvironmentVariable("SHAMRAI_BACKUP_S3_ACCESS_KEY_ID", "Process")
$SecretAccessKey = [Environment]::GetEnvironmentVariable("SHAMRAI_BACKUP_S3_SECRET_ACCESS_KEY", "Process")
$SessionToken = [Environment]::GetEnvironmentVariable("SHAMRAI_BACKUP_S3_SESSION_TOKEN", "Process")

function Find-Tool {
  param([string[]]$Candidates)
  foreach ($candidate in $Candidates) {
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
  }
  return $null
}

function Invoke-NativeChecked {
  param([string]$FilePath, [string[]]$Arguments)
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
  }
}

function Invoke-NativeOutputChecked {
  param([string]$FilePath, [string[]]$Arguments)
  $output = & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
  }
  return ($output -join "`n")
}

function Invoke-NativeInputChecked {
  param([string]$FilePath, [string[]]$Arguments, [string]$InputText)
  $InputText | & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
  }
}

function Set-Utf8NoBomLfContent {
  param([string]$Path, [string]$Content)
  $encoding = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, ($Content -replace "`r`n", "`n"), $encoding)
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

function Get-HomePath {
  if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) { return $env:USERPROFILE }
  if (-not [string]::IsNullOrWhiteSpace($HOME)) { return $HOME }
  throw "Unable to resolve the current user's home directory."
}

function Assert-CredentialValue {
  param([string]$Name, [string]$Value, [int]$MaximumLength, [bool]$Required)
  if ([string]::IsNullOrWhiteSpace($Value)) {
    if ($Required) { throw "$Name must be provided through the process environment." }
    return
  }
  if ($Value.Length -gt $MaximumLength -or $Value -match '[\r\n\x00-\x1f]') {
    throw "$Name has an invalid shape."
  }
}

function Get-SshHostName {
  param([string]$Target)
  $hostName = if ($Target.Contains("@")) { $Target.Split("@", 2)[1] } else { $Target }
  if ($hostName.Contains(":")) { $hostName = $hostName.Split(":", 2)[0] }
  return $hostName
}

function Initialize-KnownHosts {
  param([string]$HostName, [string]$Path, [bool]$SkipScan)
  $sshKeygen = Find-Tool @("ssh-keygen.exe", "ssh-keygen")
  if (-not $sshKeygen) { throw "ssh-keygen was not found. Install OpenSSH Client." }
  if ([string]::IsNullOrWhiteSpace($Path)) {
    $deployDirectory = Join-Path $Workspace ".deploy"
    New-Item -ItemType Directory -Force -Path $deployDirectory | Out-Null
    $Path = Join-Path $deployDirectory "shamrai-known-hosts"
  }
  if (-not $SkipScan) {
    $sshKeyscan = Find-Tool @("ssh-keyscan.exe", "ssh-keyscan")
    if (-not $sshKeyscan) { throw "ssh-keyscan was not found. Install OpenSSH Client." }
    $scan = Invoke-NativeOutputChecked $sshKeyscan @("-p", "$SshPort", "-t", "ed25519", $HostName)
    if ([string]::IsNullOrWhiteSpace($scan)) { throw "ssh-keyscan returned no host key." }
    Set-Utf8NoBomLfContent -Path $Path -Content ($scan.Trim() + "`n")
  }
  if (-not (Test-Path -LiteralPath $Path)) { throw "Known hosts file was not found: $Path" }
  $fingerprint = Invoke-NativeOutputChecked $sshKeygen @("-lf", $Path)
  if ($fingerprint -notmatch [regex]::Escape($HostKeyFingerprint)) {
    throw "SSH host key fingerprint mismatch."
  }
  return $Path
}

if ($DryRun) {
  Write-Host "DRY_RUN install-shamrai-backup-systemd-credentials"
  Write-Host "server=$Server"
  Write-Host "remote_path=$RemotePath"
  Write-Host "credential_store=/etc/credstore.encrypted"
  Write-Host "aws_access_key_id=shamrai-backup-aws-access-key-id.cred"
  Write-Host "aws_secret_access_key=shamrai-backup-aws-secret-access-key.cred"
  Write-Host "aws_session_token=shamrai-backup-aws-session-token.cred (optional)"
  Write-Host "manifest_signing_private_key=shamrai-backup-manifest-signing-private-key.cred"
  Write-Host "manifest_signing_mode=$(if ($BootstrapManifestSigningKey) { 'bootstrap-or-reuse-on-vds' } else { 'validate-existing-on-vds' })"
  Write-Host "access_key_configured=$(-not [string]::IsNullOrWhiteSpace($AccessKeyId))"
  Write-Host "secret_key_configured=$(-not [string]::IsNullOrWhiteSpace($SecretAccessKey))"
  Write-Host "session_token_configured=$(-not [string]::IsNullOrWhiteSpace($SessionToken))"
  Write-Host "manifest_signing_public_key_configured=$(-not [string]::IsNullOrWhiteSpace($ManifestSigningPublicKeyBase64))"
  exit 0
}

if ($RemotePath -notmatch '^/[A-Za-z0-9._/-]+$' -or $RemotePath -match '(^|/)\.\.(/|$)' -or $RemotePath.Contains('//')) {
  throw "RemotePath must be a normalized absolute Unix path."
}
if ($Server -notmatch '^(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+$') {
  throw "Server must be a plain SSH host or user@host value."
}

if (-not $BootstrapManifestSigningKey) {
  Assert-CredentialValue -Name "SHAMRAI_BACKUP_S3_ACCESS_KEY_ID" -Value $AccessKeyId -MaximumLength 512 -Required $true
  Assert-CredentialValue -Name "SHAMRAI_BACKUP_S3_SECRET_ACCESS_KEY" -Value $SecretAccessKey -MaximumLength 4096 -Required $true
  Assert-CredentialValue -Name "SHAMRAI_BACKUP_S3_SESSION_TOKEN" -Value $SessionToken -MaximumLength 8192 -Required $false
  if ([string]::IsNullOrWhiteSpace($ManifestSigningPublicKeyBase64)) {
    throw "SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64 must be provided after VDS bootstrap."
  }
}
if (-not [string]::IsNullOrWhiteSpace($ManifestSigningPublicKeyBase64)) {
  ConvertFrom-ShamraiEd25519PublicKeyBase64 -Value $ManifestSigningPublicKeyBase64 | Out-Null
}

if ([string]::IsNullOrWhiteSpace($SshKeyPath)) {
  $candidate = Join-Path (Get-HomePath) ".ssh/codex_deploy_ed25519"
  if (Test-Path -LiteralPath $candidate) { $SshKeyPath = $candidate }
}
if ([string]::IsNullOrWhiteSpace($SshKeyPath) -or -not (Test-Path -LiteralPath $SshKeyPath)) {
  throw "A valid SSH key is required. Pass -SshKeyPath or set SHAMRAI_SSH_KEY_PATH."
}

$ssh = Find-Tool @("ssh.exe", "ssh")
$scp = Find-Tool @("scp.exe", "scp")
if (-not $ssh -or -not $scp) { throw "OpenSSH ssh/scp was not found." }
$KnownHostsPath = Initialize-KnownHosts -HostName (Get-SshHostName $Server) -Path $KnownHostsPath -SkipScan ([bool]$NoHostKeyScan)

$deployDirectory = Join-Path $Workspace ".deploy"
New-Item -ItemType Directory -Force -Path $deployDirectory | Out-Null
$localInstaller = Join-Path $deployDirectory (
  "shamrai-install-backup-credentials.{0}.remote.sh" -f [Guid]::NewGuid().ToString("N")
)
$installer = @'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
REMOTE_PATH=__REMOTE_PATH_SHELL__
BOOTSTRAP_SIGNING_KEY=__BOOTSTRAP_SIGNING_KEY_SHELL__
EXPECTED_SIGNING_PUBLIC_KEY_B64=__EXPECTED_SIGNING_PUBLIC_KEY_SHELL__
CREDENTIAL_STORE=/etc/credstore.encrypted
ROTATION_LOCK=/run/shamrai-backup-credentials.lock
ACCESS_TARGET="$CREDENTIAL_STORE/shamrai-backup-aws-access-key-id.cred"
SECRET_TARGET="$CREDENTIAL_STORE/shamrai-backup-aws-secret-access-key.cred"
SESSION_TARGET="$CREDENTIAL_STORE/shamrai-backup-aws-session-token.cred"
SIGNING_TARGET="$CREDENTIAL_STORE/shamrai-backup-manifest-signing-private-key.cred"
SERVICE_FILE=/etc/systemd/system/shamrai-db-backup.service
rotation_dir=""
signing_bootstrap_started=0
s3_promotion_started=0
promotion_complete=0

echo "Shamrai credential installation inventory:"
docker ps -a --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}' || true
docker compose ls || true
ss -ltnp | grep -E ':(80|443|8000|8081|8082)\b' || true
df -h "$REMOTE_PATH" "$(dirname "$REMOTE_PATH")" || true
if [ ! -d "$REMOTE_PATH" ]; then
  echo "Canonical Shamrai path is missing: $REMOTE_PATH" >&2
  exit 21
fi
if ! command -v systemd-creds >/dev/null 2>&1; then
  echo "systemd-creds is required." >&2
  exit 22
fi
if ! command -v flock >/dev/null 2>&1; then
  echo "flock is required for atomic credential rotation." >&2
  exit 22
fi
if ! command -v openssl >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1 || ! command -v base64 >/dev/null 2>&1; then
  echo "OpenSSL, Python 3, and base64 are required for Ed25519 signing credential bootstrap and validation." >&2
  exit 22
fi

exec 9>"$ROTATION_LOCK"
if ! flock -n 9; then
  echo "Another Shamrai backup credential rotation is already running." >&2
  exit 24
fi

install -o root -g root -m 0700 -d "$CREDENTIAL_STORE"
credential_store_resolved="$(realpath -e -- "$CREDENTIAL_STORE")"
if [ "$credential_store_resolved" != "$CREDENTIAL_STORE" ] || [ -L "$CREDENTIAL_STORE" ]; then
  echo "Credential store must be a canonical non-symlink directory: $CREDENTIAL_STORE" >&2
  exit 25
fi
store_owner="$(stat -c '%u' -- "$CREDENTIAL_STORE")"
store_mode="$(stat -c '%a' -- "$CREDENTIAL_STORE")"
if [ "$store_owner" != "0" ] || (( (8#$store_mode & 0077) != 0 )); then
  echo "Credential store must be root-owned and inaccessible to group/world." >&2
  exit 25
fi

rotation_dir="$(mktemp -d "$CREDENTIAL_STORE/.shamrai-rotation.XXXXXX")"
new_dir="$rotation_dir/new"
previous_dir="$rotation_dir/previous"
install -o root -g root -m 0700 -d "$new_dir" "$previous_dir"

validate_existing_credential() {
  local target_path="$1"
  local owner_id
  local mode
  if [ ! -e "$target_path" ] && [ ! -L "$target_path" ]; then
    return 0
  fi
  if [ ! -s "$target_path" ] || [ -L "$target_path" ] || [ ! -f "$target_path" ]; then
    echo "Existing credential must be a non-empty regular non-symlink file: $target_path" >&2
    return 1
  fi
  owner_id="$(stat -c '%u' -- "$target_path")" || return 1
  mode="$(stat -c '%a' -- "$target_path")" || return 1
  if [ "$owner_id" != "0" ] || (( (8#$mode & 0077) != 0 )); then
    echo "Existing credential must be root-owned and mode 0600 or stricter: $target_path" >&2
    return 1
  fi
}

stage_credential() {
  local credential_name="$1"
  local staged_path="$2"
  local encoded_value="$3"
  printf '%s' "$encoded_value" | base64 --decode | \
    systemd-creds encrypt --name="$credential_name" - "$staged_path"
  systemd-creds decrypt --name="$credential_name" "$staged_path" - >/dev/null
  chown root:root "$staged_path"
  chmod 0600 "$staged_path"
}

validate_public_key_base64() {
  local public_key_b64="$1"
  python3 - "$public_key_b64" <<'PY'
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
}

derive_signing_public_key_base64() {
  local credential_path="$1"
  systemd-creds decrypt --name=manifest_signing_private_key "$credential_path" - |
    openssl pkey -pubout -outform DER 2>/dev/null |
    base64 -w0
}

backup_existing_credential() {
  local target_path="$1"
  local backup_name="$2"
  validate_existing_credential "$target_path"
  if [ -e "$target_path" ]; then
    install -o root -g root -m 0600 "$target_path" "$previous_dir/$backup_name"
  fi
}

restore_previous_credential() {
  local target_path="$1"
  local backup_name="$2"
  if [ -f "$previous_dir/$backup_name" ]; then
    install -o root -g root -m 0600 "$previous_dir/$backup_name" "$target_path"
  else
    rm -f -- "$target_path"
  fi
}

sync_session_credential_unit() {
  local include_session=0
  local include_signing=0
  local unit_tmp=""
  if [ ! -e "$SERVICE_FILE" ] && [ ! -L "$SERVICE_FILE" ]; then
    return 0
  fi
  if [ -L "$SERVICE_FILE" ] || [ ! -f "$SERVICE_FILE" ] || [ "$(stat -c '%u' -- "$SERVICE_FILE")" != "0" ]; then
    echo "Backup service unit must be a root-owned regular non-symlink file: $SERVICE_FILE" >&2
    return 1
  fi
  if ! grep -Fxq 'LoadCredentialEncrypted=aws_secret_access_key:/etc/credstore.encrypted/shamrai-backup-aws-secret-access-key.cred' "$SERVICE_FILE"; then
    echo "Backup service unit does not contain the expected secret credential line." >&2
    return 1
  fi
  if [ -s "$SESSION_TARGET" ]; then
    include_session=1
  fi
  if [ -s "$SIGNING_TARGET" ]; then
    include_signing=1
  fi
  unit_tmp="$(mktemp /etc/systemd/system/.shamrai-db-backup.service.XXXXXX)"
  if ! awk -v include_session="$include_session" -v include_signing="$include_signing" '
    /^LoadCredentialEncrypted=aws_session_token:/ { next }
    /^LoadCredentialEncrypted=manifest_signing_private_key:/ { next }
    { print }
    $0 == "LoadCredentialEncrypted=aws_secret_access_key:/etc/credstore.encrypted/shamrai-backup-aws-secret-access-key.cred" {
      if (include_signing == "1") {
        print "LoadCredentialEncrypted=manifest_signing_private_key:/etc/credstore.encrypted/shamrai-backup-manifest-signing-private-key.cred"
      }
      if (include_session == "1") {
        print "LoadCredentialEncrypted=aws_session_token:/etc/credstore.encrypted/shamrai-backup-aws-session-token.cred"
      }
    }
  ' "$SERVICE_FILE" > "$unit_tmp"; then
    rm -f -- "$unit_tmp"
    return 1
  fi
  install -o root -g root -m 0644 "$unit_tmp" "$SERVICE_FILE"
  rm -f -- "$unit_tmp"
  systemctl daemon-reload
}

cleanup_rotation() {
  local status="$?"
  local rollback_status=0
  trap - EXIT INT TERM
  if [ "$signing_bootstrap_started" = "1" ] && [ "$promotion_complete" != "1" ]; then
    echo "Signing-key bootstrap failed; removing the incomplete encrypted credential." >&2
    rm -f -- "$SIGNING_TARGET" || rollback_status=1
  fi
  if [ "$s3_promotion_started" = "1" ] && [ "$promotion_complete" != "1" ]; then
    echo "S3 credential promotion failed; restoring the complete previous S3 credential set." >&2
    set +e
    restore_previous_credential "$ACCESS_TARGET" access.cred || rollback_status=1
    restore_previous_credential "$SECRET_TARGET" secret.cred || rollback_status=1
    restore_previous_credential "$SESSION_TARGET" session.cred || rollback_status=1
    sync_session_credential_unit || rollback_status=1
    set -e
  fi
  if [ "$rollback_status" -ne 0 ]; then
    echo "CRITICAL: credential rollback was incomplete; backup scheduling requires manual repair." >&2
    status=26
  fi
  if [ -n "$rotation_dir" ] && [[ "$rotation_dir" == "$CREDENTIAL_STORE"/.shamrai-rotation.* ]]; then
    rm -rf -- "$rotation_dir"
  fi
  exit "$status"
}
trap cleanup_rotation EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -n "$EXPECTED_SIGNING_PUBLIC_KEY_B64" ]; then
  validate_public_key_base64 "$EXPECTED_SIGNING_PUBLIC_KEY_B64"
fi

if [ "$BOOTSTRAP_SIGNING_KEY" = "1" ]; then
  if [ -e "$SIGNING_TARGET" ] || [ -L "$SIGNING_TARGET" ]; then
    validate_existing_credential "$SIGNING_TARGET"
    signing_public_key_b64="$(derive_signing_public_key_base64 "$SIGNING_TARGET")"
    validate_public_key_base64 "$signing_public_key_b64"
    if [ -n "$EXPECTED_SIGNING_PUBLIC_KEY_B64" ] && \
       [ "$EXPECTED_SIGNING_PUBLIC_KEY_B64" != "$signing_public_key_b64" ]; then
      echo "Existing VDS signing credential does not match the configured public trust anchor." >&2
      exit 27
    fi
    echo "SHAMRAI_BACKUP_MANIFEST_SIGNING_KEY_REUSED=1"
    printf 'SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64=%s\n' "$signing_public_key_b64"
    echo "SHAMRAI_BACKUP_MANIFEST_SIGNING_BOOTSTRAP_COMPLETE=1"
    exit 0
  fi

  if [ -n "$EXPECTED_SIGNING_PUBLIC_KEY_B64" ]; then
    echo "No signing credential exists. Unset the public trust anchor and rerun explicit bootstrap so the VDS-generated public key can be enrolled." >&2
    exit 27
  fi

  # The plaintext Ed25519 key exists only in this root-only VDS pipeline. It is
  # encrypted directly into the rotation directory and is never written to disk.
  openssl genpkey -algorithm ED25519 |
    systemd-creds encrypt --name=manifest_signing_private_key - "$new_dir/signing.cred"
  chown root:root "$new_dir/signing.cred"
  chmod 0600 "$new_dir/signing.cred"
  validate_existing_credential "$new_dir/signing.cred"
  signing_public_key_b64="$(derive_signing_public_key_base64 "$new_dir/signing.cred")"
  validate_public_key_base64 "$signing_public_key_b64"

  # Re-check under the rotation lock immediately before the only allowed first install.
  if [ -e "$SIGNING_TARGET" ] || [ -L "$SIGNING_TARGET" ]; then
    echo "Signing credential appeared during bootstrap; refusing implicit replacement." >&2
    exit 27
  fi
  signing_bootstrap_started=1
  install -o root -g root -m 0600 "$new_dir/signing.cred" "$SIGNING_TARGET"
  installed_public_key_b64="$(derive_signing_public_key_base64 "$SIGNING_TARGET")"
  if [ "$installed_public_key_b64" != "$signing_public_key_b64" ]; then
    echo "Installed signing credential failed public-key verification." >&2
    exit 27
  fi
  promotion_complete=1
  echo "SHAMRAI_BACKUP_MANIFEST_SIGNING_KEY_CREATED=1"
  printf 'SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64=%s\n' "$signing_public_key_b64"
  echo "SHAMRAI_BACKUP_MANIFEST_SIGNING_BOOTSTRAP_COMPLETE=1"
  exit 0
fi

if [ ! -e "$SIGNING_TARGET" ] && [ ! -L "$SIGNING_TARGET" ]; then
  echo "Manifest signing credential is absent. Run the installer once with -BootstrapManifestSigningKey before installing S3 credentials." >&2
  exit 27
fi
validate_existing_credential "$SIGNING_TARGET"
signing_public_key_b64="$(derive_signing_public_key_base64 "$SIGNING_TARGET")"
validate_public_key_base64 "$signing_public_key_b64"
if [ "$EXPECTED_SIGNING_PUBLIC_KEY_B64" != "$signing_public_key_b64" ]; then
  echo "Existing VDS signing credential does not match the configured public trust anchor." >&2
  exit 27
fi

IFS= read -r access_key_b64
IFS= read -r secret_key_b64
IFS= read -r session_token_b64 || true
if [ -z "$access_key_b64" ] || [ -z "$secret_key_b64" ]; then
  echo "Required S3 credential input was empty." >&2
  exit 23
fi

stage_credential aws_access_key_id "$new_dir/access.cred" "$access_key_b64"
stage_credential aws_secret_access_key "$new_dir/secret.cred" "$secret_key_b64"
if [ -n "$session_token_b64" ]; then
  stage_credential aws_session_token "$new_dir/session.cred" "$session_token_b64"
fi

backup_existing_credential "$ACCESS_TARGET" access.cred
backup_existing_credential "$SECRET_TARGET" secret.cred
backup_existing_credential "$SESSION_TARGET" session.cred

s3_promotion_started=1
install -o root -g root -m 0600 "$new_dir/access.cred" "$ACCESS_TARGET"
install -o root -g root -m 0600 "$new_dir/secret.cred" "$SECRET_TARGET"
if [ -f "$new_dir/session.cred" ]; then
  install -o root -g root -m 0600 "$new_dir/session.cred" "$SESSION_TARGET"
else
  rm -f -- "$SESSION_TARGET"
fi
sync_session_credential_unit
promotion_complete=1
echo "SHAMRAI_BACKUP_SYSTEMD_CREDENTIALS_INSTALLED=1"
echo "SHAMRAI_BACKUP_MANIFEST_SIGNING_KEY_REUSED=1"
'@
$installer = $installer.Replace("__REMOTE_PATH_SHELL__", (ConvertTo-ShellSingleQuoted $RemotePath))
$installer = $installer.Replace(
  "__BOOTSTRAP_SIGNING_KEY_SHELL__",
  (ConvertTo-ShellSingleQuoted $(if ($BootstrapManifestSigningKey) { "1" } else { "0" }))
)
$installer = $installer.Replace(
  "__EXPECTED_SIGNING_PUBLIC_KEY_SHELL__",
  (ConvertTo-ShellSingleQuoted $(if ($null -eq $ManifestSigningPublicKeyBase64) { "" } else { $ManifestSigningPublicKeyBase64 }))
)
Set-Utf8NoBomLfContent -Path $localInstaller -Content $installer

$sshOptions = @(
  "-i", $SshKeyPath,
  "-p", "$SshPort",
  "-o", "BatchMode=yes",
  "-o", "IdentitiesOnly=yes",
  "-o", "UserKnownHostsFile=$KnownHostsPath",
  "-o", "StrictHostKeyChecking=yes"
)
$scpOptions = @(
  "-i", $SshKeyPath,
  "-P", "$SshPort",
  "-o", "BatchMode=yes",
  "-o", "IdentitiesOnly=yes",
  "-o", "UserKnownHostsFile=$KnownHostsPath",
  "-o", "StrictHostKeyChecking=yes"
)
$payloadLines = @()
if (-not $BootstrapManifestSigningKey) {
  $utf8 = [System.Text.Encoding]::UTF8
  $payloadLines = @(
    [Convert]::ToBase64String($utf8.GetBytes($AccessKeyId)),
    [Convert]::ToBase64String($utf8.GetBytes($SecretAccessKey)),
    $(if ([string]::IsNullOrWhiteSpace($SessionToken)) { "" } else { [Convert]::ToBase64String($utf8.GetBytes($SessionToken)) })
  )
}

$remoteStage = ""
$operationError = $null
$cleanupError = $null
try {
  $stageCommand = @'
set -Eeuo pipefail
stage="$(mktemp -d /root/.shamrai-backup-credentials.XXXXXX)"
chmod 0700 "$stage"
printf 'SHAMRAI_CREDENTIAL_STAGE=%s\n' "$stage"
'@
  $stageOutput = Invoke-NativeOutputChecked $ssh ($sshOptions + @($Server, $stageCommand))
  $stageMatches = @(
    $stageOutput -split "`n" |
      ForEach-Object { $_.Trim() } |
      Where-Object { $_ -match '^SHAMRAI_CREDENTIAL_STAGE=/root/\.shamrai-backup-credentials\.[A-Za-z0-9]{6}$' }
  )
  if ($stageMatches.Count -ne 1) {
    throw "Remote mktemp did not return exactly one validated credential stage."
  }
  $remoteStage = $stageMatches[0].Substring("SHAMRAI_CREDENTIAL_STAGE=".Length)
  $remoteInstaller = "$remoteStage/install.sh"

  Invoke-NativeChecked $scp ($scpOptions + @($localInstaller, "${Server}:$remoteInstaller"))
  $remoteCommand = "chmod 0700 $(ConvertTo-ShellSingleQuoted $remoteInstaller) && bash $(ConvertTo-ShellSingleQuoted $remoteInstaller)"
  if ($BootstrapManifestSigningKey) {
    $bootstrapOutput = Invoke-NativeOutputChecked $ssh ($sshOptions + @($Server, $remoteCommand))
    $publicKeyLines = @(
      $bootstrapOutput -split "`n" |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -match '^SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64=[A-Za-z0-9+/]+={0,2}$' }
    )
    if ($publicKeyLines.Count -ne 1) {
      throw "VDS signing-key bootstrap did not return exactly one public trust anchor."
    }
    $bootstrappedPublicKey = $publicKeyLines[0].Substring(
      "SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64=".Length
    )
    ConvertFrom-ShamraiEd25519PublicKeyBase64 -Value $bootstrappedPublicKey | Out-Null
    Write-Host "SHAMRAI_BACKUP_MANIFEST_SIGNING_BOOTSTRAP_COMPLETE=1"
    Write-Host $publicKeyLines[0]
    Write-Host "Store this public trust anchor in the approved configuration, then rerun without -BootstrapManifestSigningKey."
  } else {
    Invoke-NativeInputChecked $ssh ($sshOptions + @($Server, $remoteCommand)) (($payloadLines -join "`n") + "`n")
  }
} catch {
  $operationError = $_
} finally {
  Remove-Item -LiteralPath $localInstaller -Force -ErrorAction SilentlyContinue
  if ($remoteStage -match '^/root/\.shamrai-backup-credentials\.[A-Za-z0-9]{6}$') {
    try {
      $cleanupCommand = "rm -rf -- $(ConvertTo-ShellSingleQuoted $remoteStage)"
      Invoke-NativeChecked $ssh ($sshOptions + @($Server, $cleanupCommand))
    } catch {
      $cleanupError = $_
    }
  }
}

if ($null -ne $operationError) {
  throw $operationError
}
if ($null -ne $cleanupError) {
  throw $cleanupError
}
if ($BootstrapManifestSigningKey) {
  Write-Host "shamrai_backup_manifest_signing_bootstrap_ok" -ForegroundColor Green
} else {
  Write-Host "shamrai_backup_systemd_credentials_ok" -ForegroundColor Green
}
