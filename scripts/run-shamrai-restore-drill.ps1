param(
  [string]$Workspace = "",
  [string]$Server = "root@82.147.67.245",
  [int]$SshPort = 22,
  [string]$HostKeyFingerprint = "SHA256:xdVRtRaXWqK6eAIsE3VwD0o2H6GJDcCm65L1ZUBjuMw",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [string]$HealthUrl = "http://127.0.0.1:8082/api/health",
  [ValidateSet("Vds", "S3")][string]$ArtifactSource = "Vds",
  [string]$BackupFile = "",
  [string]$OffHostArtifactKey = "",
  [string]$OffHostS3Bucket = $env:SHAMRAI_BACKUP_S3_BUCKET,
  [string]$OffHostS3Prefix = $env:SHAMRAI_BACKUP_S3_PREFIX,
  [string]$OffHostS3EndpointUrl = $env:SHAMRAI_BACKUP_S3_ENDPOINT_URL,
  [string]$OffHostS3Region = $env:SHAMRAI_BACKUP_S3_REGION,
  [string]$ManifestSigningPublicKeyBase64 = $env:SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64,
  [string]$AgeIdentityPath = $env:SHAMRAI_BACKUP_AGE_IDENTITY_PATH,
  [string]$SshKeyPath = $env:SHAMRAI_SSH_KEY_PATH,
  [string]$KnownHostsPath = "",
  [string]$DrillProjectPrefix = "shamrai-restore-drill",
  [int]$DrillBackendPort = 18000,
  [int]$DrillFrontendPort = 18082,
  [int]$MaximumLatestAgeHours = 26,
  [string]$AttestationPath = "",
  [switch]$NoHostKeyScan,
  [switch]$KeepDrillProject,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$manifestSignatureModule = Join-Path $PSScriptRoot "lib/BackupManifestSignature.psm1"
Import-Module -Name $manifestSignatureModule -Force -ErrorAction Stop

$script:SensitiveLocalFiles = @()
$script:SensitiveLocalDirectories = @()
$script:RemoteStageToCleanup = ""
$script:SshTool = ""
$script:ScpTool = ""
$script:AwsTool = ""
$script:BackupCreatedAtUtc = $null
$script:ManifestSha256 = ""
$script:ManifestSignatureSha256 = ""
$script:ManifestSigningPublicKeySha256 = ""

trap {
  $originalError = $_
  foreach ($path in $script:SensitiveLocalFiles) {
    if (-not [string]::IsNullOrWhiteSpace($path)) {
      Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }
  }
  foreach ($path in $script:SensitiveLocalDirectories) {
    if (-not [string]::IsNullOrWhiteSpace($path)) {
      Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue
    }
  }

  if (
    -not $KeepDrillProject -and
    -not [string]::IsNullOrWhiteSpace($script:RemoteStageToCleanup) -and
    $script:RemoteStageToCleanup -match '^/tmp/shamrai-restore-drill\.[A-Za-z0-9]{6}$' -and
    -not [string]::IsNullOrWhiteSpace($script:SshTool) -and
    -not [string]::IsNullOrWhiteSpace($SshKeyPath) -and
    -not [string]::IsNullOrWhiteSpace($KnownHostsPath)
  ) {
    $cleanupCommand = "rm -rf -- $(ConvertTo-ShellSingleQuoted $script:RemoteStageToCleanup)"
    $cleanupArgs = @(
      "-i", $SshKeyPath,
      "-p", "$SshPort",
      "-o", "BatchMode=yes",
      "-o", "IdentitiesOnly=yes",
      "-o", "UserKnownHostsFile=$KnownHostsPath",
      "-o", "StrictHostKeyChecking=yes",
      $Server,
      $cleanupCommand
    )
    & $script:SshTool @cleanupArgs | Out-Null
  }

  throw $originalError
}

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

function Get-StringSha256Hex {
  param([Parameter(Mandatory = $true)][string]$Value)

  $hasher = [Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
    return ([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  } finally {
    $hasher.Dispose()
  }
}

function Set-OwnerOnlyLocalPath {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][bool]$Directory
  )

  $isWindowsPlatform = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
  if ($isWindowsPlatform) {
    $icacls = Find-Tool @("icacls.exe", "icacls")
    if (-not $icacls) {
      throw "icacls is required to protect decrypted restore material on Windows."
    }
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    $grant = if ($Directory) { "${identity}:(OI)(CI)F" } else { "${identity}:F" }
    Invoke-NativeChecked -FilePath $icacls -Arguments @($Path, "/inheritance:r", "/grant:r", $grant)
    return
  }

  $chmod = Find-Tool @("chmod")
  if (-not $chmod) {
    throw "chmod is required to protect decrypted restore material."
  }
  $mode = if ($Directory) { "0700" } else { "0600" }
  Invoke-NativeChecked -FilePath $chmod -Arguments @($mode, "--", $Path)
}

function Get-AwsArguments {
  param([string[]]$Arguments = @())

  $result = @()
  if (-not [string]::IsNullOrWhiteSpace($OffHostS3EndpointUrl)) {
    $result += @("--endpoint-url", $OffHostS3EndpointUrl)
  }
  if (-not [string]::IsNullOrWhiteSpace($OffHostS3Region)) {
    $result += @("--region", $OffHostS3Region)
  }
  $result += $Arguments
  return $result
}

function Invoke-AwsChecked {
  param([string[]]$Arguments = @())

  $awsArguments = @(Get-AwsArguments -Arguments $Arguments)
  Invoke-NativeChecked -FilePath $script:AwsTool -Arguments $awsArguments
}

function Invoke-AwsOutputChecked {
  param([string[]]$Arguments = @())

  $awsArguments = @(Get-AwsArguments -Arguments $Arguments)
  return Invoke-NativeOutputChecked -FilePath $script:AwsTool -Arguments $awsArguments
}

function Get-LatestOffHostArtifactPair {
  $dailyPrefix = Get-OffHostS3Key -Prefix $OffHostS3Prefix -RelativeKey "daily/"
  $rawKeys = Invoke-AwsOutputChecked -Arguments @(
    "s3api", "list-objects-v2",
    "--bucket", $OffHostS3Bucket,
    "--prefix", $dailyPrefix,
    "--query", "Contents[].Key",
    "--output", "json"
  )

  $decodedKeys = $null
  if (-not [string]::IsNullOrWhiteSpace($rawKeys)) {
    $decodedKeys = $rawKeys | ConvertFrom-Json
  }
  $signaturePattern = "^$([regex]::Escape($dailyPrefix))shamrai-db\.\d{8}T\d{6}Z\.manifest\.sig$"
  $signatureKey = @($decodedKeys) |
    Where-Object { $_ -is [string] -and $_ -match $signaturePattern } |
    Sort-Object -Descending |
    Select-Object -First 1
  if ([string]::IsNullOrWhiteSpace($signatureKey)) {
    throw "No signed off-host backup commit marker was found under the configured daily prefix."
  }

  return [pscustomobject]@{
    ArtifactKey = $signatureKey -replace '\.manifest\.sig$', '.dump.age'
    ManifestKey = $signatureKey -replace '\.manifest\.sig$', '.manifest.json'
    SignatureKey = $signatureKey
  }
}

function Copy-FromOffHostChecked {
  param(
    [Parameter(Mandatory = $true)][string]$ObjectKey,
    [Parameter(Mandatory = $true)][string]$LocalPath
  )

  $uri = Get-OffHostS3Uri -Bucket $OffHostS3Bucket -Key $ObjectKey
  Invoke-AwsChecked -Arguments @(
    "s3", "cp", $uri, $LocalPath,
    "--only-show-errors", "--no-progress"
  )
}

function Assert-OffHostObjectMetadata {
  param(
    [Parameter(Mandatory = $true)][string]$ObjectKey,
    [Parameter(Mandatory = $true)][string]$LocalPath
  )

  $rawMetadata = Invoke-AwsOutputChecked -Arguments @(
    "s3api", "head-object",
    "--bucket", $OffHostS3Bucket,
    "--key", $ObjectKey,
    "--output", "json"
  )
  $metadata = $rawMetadata | ConvertFrom-Json
  $expectedSize = 0L
  [long]::TryParse(
    [string](Get-JsonPropertyValue -Object $metadata -Name "ContentLength" -DefaultValue "0"),
    [ref]$expectedSize
  ) | Out-Null
  $customMetadata = Get-JsonPropertyValue -Object $metadata -Name "Metadata"
  $expectedHash = [string](Get-JsonPropertyValue -Object $customMetadata -Name "sha256" -DefaultValue "")
  if ($expectedSize -lt 1 -or $expectedHash -notmatch '^[0-9a-fA-F]{64}$') {
    throw "Off-host object is missing required size/hash integrity metadata: $ObjectKey"
  }

  $actualSize = (Get-Item -LiteralPath $LocalPath).Length
  $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $LocalPath).Hash.ToLowerInvariant()
  if ($actualSize -ne $expectedSize -or $actualHash -ne $expectedHash.ToLowerInvariant()) {
    throw "Downloaded off-host object failed integrity metadata verification: $ObjectKey"
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

function Get-JsonPropertyValue {
  param(
    [AllowNull()][object]$Object,
    [Parameter(Mandatory = $true)][string]$Name,
    [AllowNull()][object]$DefaultValue = $null
  )

  if ($null -eq $Object) {
    return $DefaultValue
  }
  $property = $Object.PSObject.Properties[$Name]
  if ($null -eq $property) {
    return $DefaultValue
  }
  return $property.Value
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
    throw "Remote restore drill script still contains unresolved template placeholders: $($unresolved -join ', ')"
  }
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

function Copy-FromRemoteChecked {
  param(
    [Parameter(Mandatory = $true)][string]$RemoteFilePath,
    [Parameter(Mandatory = $true)][string]$LocalPath
  )
  $source = "${Server}:$RemoteFilePath"
  $args = @(
    "-i", $SshKeyPath,
    "-P", "$SshPort",
    "-o", "BatchMode=yes",
    "-o", "IdentitiesOnly=yes",
    "-o", "UserKnownHostsFile=$KnownHostsPath",
    "-o", "StrictHostKeyChecking=yes",
    $source,
    $LocalPath
  )
  Invoke-NativeChecked -FilePath $script:ScpTool -Arguments $args
}

function New-BackupSelectionCommand {
  param(
    [Parameter(Mandatory = $true)][string]$RemoteAppPath,
    [Parameter(Mandatory = $true)][AllowEmptyString()][string]$RequestedBackup
  )

  $remoteAppPathQuoted = ConvertTo-ShellSingleQuoted $RemoteAppPath
  $requestedBackupQuoted = ConvertTo-ShellSingleQuoted $RequestedBackup
  return @"
set -Eeuo pipefail
REMOTE_PATH=$remoteAppPathQuoted
REQUESTED_BACKUP=$requestedBackupQuoted
if [ -n "`$REQUESTED_BACKUP" ]; then
  backup="`$REQUESTED_BACKUP"
  signature="`${backup%.dump.age}.manifest.sig"
else
  signature="`$(find "`$REMOTE_PATH/db-backups/daily" "`$REMOTE_PATH/db-backups/weekly" -maxdepth 1 -type f -name 'shamrai-db.*.manifest.sig' -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -n 1 | sed 's/^[^ ]* //')"
  backup="`${signature%.manifest.sig}.dump.age"
fi
if [ -z "`$signature" ] || [ -z "`$backup" ]; then
  echo "No signed Shamrai backup commit marker was found." >&2
  exit 30
fi
backup_name="`$(basename -- "`$backup")"
backup_dir="`$(dirname -- "`$backup")"
if [[ ! "`$backup_name" =~ ^shamrai-db\.[0-9]{8}T[0-9]{6}Z\.dump\.age$ ]] || {
  [ "`$backup_dir" != "`$REMOTE_PATH/db-backups/daily" ] &&
  [ "`$backup_dir" != "`$REMOTE_PATH/db-backups/weekly" ]
}; then
  echo "Requested backup path is outside the signed Shamrai backup directories or has an invalid name." >&2
  exit 30
fi
manifest="`${backup%.dump.age}.manifest.json"
for candidate in "`$backup" "`$manifest" "`$signature"; do
  if [ ! -s "`$candidate" ] || [ ! -f "`$candidate" ] || [ -L "`$candidate" ] || \
     [ "`$(realpath -e -- "`$candidate")" != "`$candidate" ]; then
    echo "Signed backup bundle member failed regular-file validation: `$candidate" >&2
    exit 31
  fi
done
if [ "`$(wc -c < "`$signature" | tr -d '[:space:]')" != "64" ]; then
  echo "Backup manifest signature is not a 64-byte Ed25519 signature." >&2
  exit 31
fi
printf '%s\n%s\n%s\n' "`$backup" "`$manifest" "`$signature"
"@
}

function New-RemoteRestoreDrillScript {
  param(
    [Parameter(Mandatory = $true)][string]$RemoteAppPath,
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][string]$DrillProject,
    [Parameter(Mandatory = $true)][int]$BackendPort,
    [Parameter(Mandatory = $true)][int]$FrontendPort,
    [Parameter(Mandatory = $true)][string]$CanonicalHealth,
    [Parameter(Mandatory = $true)][string]$ReleaseSha,
    [Parameter(Mandatory = $true)][string]$ReleaseBuildTime,
    [Parameter(Mandatory = $true)][bool]$Keep
  )

  $keepValue = if ($Keep) { "1" } else { "0" }
  $template = @'
#!/usr/bin/env bash
set -Eeuo pipefail

REMOTE_PATH=__REMOTE_PATH__
COMPOSE_PROJECT=__COMPOSE_PROJECT__
DRILL_PROJECT=__DRILL_PROJECT__
DRILL_BACKEND_PORT=__DRILL_BACKEND_PORT__
DRILL_FRONTEND_PORT=__DRILL_FRONTEND_PORT__
CANON_HEALTH_URL=__CANON_HEALTH_URL__
EXPECTED_RELEASE_SHA=__RELEASE_SHA__
EXPECTED_RELEASE_BUILD_TIME=__RELEASE_BUILD_TIME__
KEEP_DRILL_PROJECT=__KEEP_DRILL_PROJECT__

STAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DUMP_FILE="$STAGE_DIR/restore.dump"
MANIFEST_FILE="$STAGE_DIR/restore.manifest.json"
COMPOSE_FILE="$STAGE_DIR/docker-compose.restore-drill.yml"
EXPECTED_COUNTS="$STAGE_DIR/expected-counts.tsv"

cleanup() {
  status="$?"
  if [ "$KEEP_DRILL_PROJECT" != "1" ]; then
    docker compose -f "$COMPOSE_FILE" -p "$DRILL_PROJECT" down -v --remove-orphans >/dev/null 2>&1 || true
    docker image rm "shamrai-restore-backend:$EXPECTED_RELEASE_SHA" >/dev/null 2>&1 || true
    rm -rf "$STAGE_DIR"
  else
    echo "Keeping restore drill project and stage for debugging: project=$DRILL_PROJECT stage=$STAGE_DIR" >&2
  fi
  exit "$status"
}
trap cleanup EXIT

echo "Shamrai restore drill inventory:"
docker ps -a --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}' || true
echo "Listening ports 80/443/8000/8081/8082/drill:"
ss -ltnp | grep -E ':(80|443|8000|8081|8082|18000|18082)\b' || true
echo "Disk space:"
df -h "$REMOTE_PATH" /tmp || true

if [ ! -f "$DUMP_FILE" ]; then
  echo "Missing decrypted restore dump: $DUMP_FILE" >&2
  exit 40
fi
if [ ! -f "$MANIFEST_FILE" ]; then
  echo "Missing restore manifest: $MANIFEST_FILE" >&2
  exit 41
fi
if [ ! -d "$REMOTE_PATH" ]; then
  echo "Canonical Shamrai path is missing: $REMOTE_PATH" >&2
  exit 42
fi

cd "$REMOTE_PATH"
docker compose -p "$COMPOSE_PROJECT" config -q

postgres_image="$(docker inspect -f '{{.Config.Image}}' shamrai-postgres)"
redis_image="$(docker inspect -f '{{.Config.Image}}' shamrai-redis)"
frontend_image="$(docker inspect -f '{{.Config.Image}}' shamrai-frontend)"
candidate_backend_image="shamrai-restore-backend:$EXPECTED_RELEASE_SHA"

if [ ! -s "$STAGE_DIR/candidate-backend.tar.gz" ]; then
  echo "Candidate backend archive is missing from restore stage." >&2
  exit 43
fi
tar -xzf "$STAGE_DIR/candidate-backend.tar.gz" -C "$STAGE_DIR"
docker build --label "shamrai.restore.release_sha=$EXPECTED_RELEASE_SHA" -t "$candidate_backend_image" "$STAGE_DIR/backend"

cat > "$COMPOSE_FILE" <<EOF
services:
  postgres:
    image: ${postgres_image}
    environment:
      POSTGRES_DB: shamrai_restore
      POSTGRES_USER: shamrai_restore
      POSTGRES_PASSWORD: restore_drill_password
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U \$\${POSTGRES_USER} -d \$\${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 20
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - shamrai_restore

  redis:
    image: ${redis_image}
    command: ["redis-server", "--appendonly", "yes", "--maxmemory", "128mb", "--maxmemory-policy", "allkeys-lru"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 20
    volumes:
      - redis_data:/data
    networks:
      - shamrai_restore

  backend:
    image: ${candidate_backend_image}
    environment:
      APP_ENV: restore-drill
      DEBUG_MODE: "false"
      ALLOW_DEBUG_AUTH_BYPASS: "false"
      ENABLE_BACKGROUND_TASKS: "false"
      DATABASE_URL: postgresql://shamrai_restore:restore_drill_password@postgres:5432/shamrai_restore
      REDIS_URL: redis://redis:6379/0
      REDIS_CACHE_ENABLED: "true"
      SECURITY_RATE_LIMIT_MODE: enforce
      JWT_SECRET_KEY: restore-drill-jwt-secret-not-real-1234567890
      CORS_ALLOWED_ORIGINS: http://127.0.0.1:${DRILL_FRONTEND_PORT}
      TELEGRAM_BOT_TOKEN: ""
      TELEGRAM_WEBHOOK_SECRET_TOKEN: ""
      VK_GROUP_ACCESS_TOKEN: ""
      VK_CALLBACK_CONFIRMATION_CODE: ""
      VK_CALLBACK_SECRET: ""
      YOOKASSA_SHOP_ID: ""
      YOOKASSA_SECRET_KEY: ""
      TEGRO_SHOP_ID: ""
      TEGRO_API_KEY: ""
      TEGRO_SECRET_KEY: ""
      HTTPS_PROXY: ""
      SHAMRAI_GIT_SHA: ${EXPECTED_RELEASE_SHA}
      SHAMRAI_BUILD_TIME: ${EXPECTED_RELEASE_BUILD_TIME}
    read_only: true
    tmpfs:
      - /tmp:uid=10001,gid=10001,mode=1777
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test:
        [
          "CMD-SHELL",
          "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=3).read()\""
        ]
      interval: 10s
      timeout: 5s
      retries: 20
      start_period: 20s
    ports:
      - "127.0.0.1:${DRILL_BACKEND_PORT}:8000"
    networks:
      - shamrai_restore

  frontend:
    image: ${frontend_image}
    depends_on:
      backend:
        condition: service_healthy
    ports:
      - "127.0.0.1:${DRILL_FRONTEND_PORT}:8080"
    read_only: true
    tmpfs:
      - /var/cache/nginx:uid=101,gid=101,mode=0755
      - /var/run:uid=101,gid=101,mode=0755
      - /tmp:uid=101,gid=101,mode=1777
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8080/ >/dev/null 2>&1 || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 20
    networks:
      - shamrai_restore

volumes:
  postgres_data:
  redis_data:

networks:
  shamrai_restore:
    driver: bridge
EOF

docker compose -f "$COMPOSE_FILE" -p "$DRILL_PROJECT" down -v --remove-orphans >/dev/null 2>&1 || true
docker compose -f "$COMPOSE_FILE" -p "$DRILL_PROJECT" up -d postgres redis

for _ in $(seq 1 60); do
  if docker compose -f "$COMPOSE_FILE" -p "$DRILL_PROJECT" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker compose -f "$COMPOSE_FILE" -p "$DRILL_PROJECT" exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose -f "$COMPOSE_FILE" -p "$DRILL_PROJECT" exec -T redis redis-cli ping | grep -qx PONG

docker compose -f "$COMPOSE_FILE" -p "$DRILL_PROJECT" exec -T postgres sh -c 'pg_restore --no-owner --role="$POSTGRES_USER" --exit-on-error -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$DUMP_FILE"

# Prove that the restored database can be upgraded by the exact application
# image used for the drill before any API process is considered healthy.
docker compose -f "$COMPOSE_FILE" -p "$DRILL_PROJECT" run --rm backend alembic upgrade head
docker compose -f "$COMPOSE_FILE" -p "$DRILL_PROJECT" run --rm backend alembic current --check-heads

python3 - "$MANIFEST_FILE" "$EXPECTED_COUNTS" <<'PY'
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
out = Path(sys.argv[2])
rows: list[str] = []
for table, count in sorted((manifest.get("table_counts") or {}).items()):
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
        raise SystemExit(f"Unsafe table name in manifest: {table!r}")
    rows.append(f"{table}\t{int(count)}")
out.write_text("\n".join(rows) + "\n", encoding="utf-8")
PY

while IFS=$'\t' read -r table expected; do
  [ -n "$table" ] || continue
  exists_sql="select to_regclass('public.${table}') is not null;"
  exists="$(printf '%s\n' "$exists_sql" | docker compose -f "$COMPOSE_FILE" -p "$DRILL_PROJECT" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' | tr -d '[:space:]')"
  if [ "$exists" = "t" ]; then
    count_sql="select count(*) from public.${table};"
    actual="$(printf '%s\n' "$count_sql" | docker compose -f "$COMPOSE_FILE" -p "$DRILL_PROJECT" exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At' | tr -d '[:space:]')"
  else
    actual="0"
  fi
  if [ "$actual" != "$expected" ]; then
    echo "Restore count mismatch for $table: expected=$expected actual=$actual" >&2
    exit 50
  fi
done < "$EXPECTED_COUNTS"

docker compose -f "$COMPOSE_FILE" -p "$DRILL_PROJECT" up -d backend frontend
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${DRILL_BACKEND_PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS "http://127.0.0.1:${DRILL_BACKEND_PORT}/api/health" >/dev/null
curl -fsS "http://127.0.0.1:${DRILL_BACKEND_PORT}/api/ready" >/dev/null
version_sha="$(curl -fsS "http://127.0.0.1:${DRILL_BACKEND_PORT}/api/version" | python3 -c 'import json, re, sys; payload = json.load(sys.stdin); value = str(payload.get("git_sha") or ""); assert re.fullmatch(r"[0-9a-f]{40}", value), value; assert value == sys.argv[1], payload; assert payload.get("build_time") == sys.argv[2], payload; print(value)' "$EXPECTED_RELEASE_SHA" "$EXPECTED_RELEASE_BUILD_TIME")"
curl -fsS "http://127.0.0.1:${DRILL_FRONTEND_PORT}/" | grep -Eiq '<html|id="root"'

cd "$REMOTE_PATH"
docker compose -p "$COMPOSE_PROJECT" ps
curl -fsS "$CANON_HEALTH_URL" >/dev/null

echo "RESTORE_DRILL_VERSION_SHA=$version_sha"
echo "RESTORE_DRILL_OK project=$DRILL_PROJECT"
'@

  $result = $template.Replace("__REMOTE_PATH__", (ConvertTo-ShellSingleQuoted $RemoteAppPath))
  $result = $result.Replace("__COMPOSE_PROJECT__", (ConvertTo-ShellSingleQuoted $Project))
  $result = $result.Replace("__DRILL_PROJECT__", (ConvertTo-ShellSingleQuoted $DrillProject))
  $result = $result.Replace("__DRILL_BACKEND_PORT__", "$BackendPort")
  $result = $result.Replace("__DRILL_FRONTEND_PORT__", "$FrontendPort")
  $result = $result.Replace("__CANON_HEALTH_URL__", (ConvertTo-ShellSingleQuoted $CanonicalHealth))
  $result = $result.Replace("__RELEASE_SHA__", (ConvertTo-ShellSingleQuoted $ReleaseSha))
  $result = $result.Replace("__RELEASE_BUILD_TIME__", (ConvertTo-ShellSingleQuoted $ReleaseBuildTime))
  $result = $result.Replace("__KEEP_DRILL_PROJECT__", (ConvertTo-ShellSingleQuoted $keepValue))
  return $result
}

if ([string]::IsNullOrWhiteSpace($Workspace)) {
  $Workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

if ($DrillBackendPort -eq 8082 -or $DrillFrontendPort -eq 8082) {
  throw "Restore drill ports must not use canonical preview port 8082."
}
if ($MaximumLatestAgeHours -lt 1 -or $MaximumLatestAgeHours -gt 168) {
  throw "MaximumLatestAgeHours must be between 1 and 168."
}

$artifactSourceNormalized = $ArtifactSource.ToLowerInvariant()
$usingImplicitLatest = $artifactSourceNormalized -eq "s3" -and [string]::IsNullOrWhiteSpace($OffHostArtifactKey)
if ($artifactSourceNormalized -eq "s3") {
  if (-not [string]::IsNullOrWhiteSpace($BackupFile)) {
    throw "BackupFile is only valid with ArtifactSource Vds; use OffHostArtifactKey for S3."
  }
  if ([string]::IsNullOrWhiteSpace($OffHostS3Bucket)) {
    throw "OffHostS3Bucket is required when ArtifactSource is S3."
  }

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

  if (-not [string]::IsNullOrWhiteSpace($OffHostArtifactKey)) {
    $OffHostArtifactKey = $OffHostArtifactKey.Trim()
    $dailyPrefix = Get-OffHostS3Key -Prefix $OffHostS3Prefix -RelativeKey "daily/"
    $artifactPattern = "^$([regex]::Escape($dailyPrefix))shamrai-db\.\d{8}T\d{6}Z\.dump\.age$"
    if ($OffHostArtifactKey -notmatch $artifactPattern) {
      throw "OffHostArtifactKey must identify a generated daily .dump.age object under the configured prefix."
    }
  }
} elseif (-not [string]::IsNullOrWhiteSpace($OffHostArtifactKey)) {
  throw "OffHostArtifactKey requires ArtifactSource S3."
}

if ([string]::IsNullOrWhiteSpace($ManifestSigningPublicKeyBase64)) {
  if ($DryRun) {
    # RFC 8032 test-vector public key; public and used only for command generation.
    $ManifestSigningPublicKeyBase64 = "MCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="
  } else {
    throw "Set SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64 or pass -ManifestSigningPublicKeyBase64. Unsigned restores are not allowed."
  }
}
[byte[]]$manifestSigningPublicKeyDer = ConvertFrom-ShamraiEd25519PublicKeyBase64 -Value $ManifestSigningPublicKeyBase64

$drillId = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
$drillProject = "$DrillProjectPrefix-$drillId"

if ($DryRun) {
  $scriptPreview = New-RemoteRestoreDrillScript `
    -RemoteAppPath $RemotePath `
    -Project $ComposeProject `
    -DrillProject $drillProject `
    -BackendPort $DrillBackendPort `
    -FrontendPort $DrillFrontendPort `
    -CanonicalHealth $HealthUrl `
    -ReleaseSha ("0" * 40) `
    -ReleaseBuildTime "1970-01-01T00:00:00Z" `
    -Keep ([bool]$KeepDrillProject)
  Assert-NoUnresolvedTemplatePlaceholders -Content $scriptPreview -Placeholders @(
    "__REMOTE_PATH__",
    "__COMPOSE_PROJECT__",
    "__DRILL_PROJECT__",
    "__DRILL_BACKEND_PORT__",
    "__DRILL_FRONTEND_PORT__",
    "__CANON_HEALTH_URL__",
    "__RELEASE_SHA__",
    "__RELEASE_BUILD_TIME__",
    "__KEEP_DRILL_PROJECT__"
  )

  Write-Host "DRY_RUN run-shamrai-restore-drill"
  Write-Host "server=$Server"
  Write-Host "remote_path=$RemotePath"
  Write-Host "compose_project=$ComposeProject"
  Write-Host "drill_project=$drillProject"
  Write-Host "drill_backend_port=$DrillBackendPort"
  Write-Host "drill_frontend_port=$DrillFrontendPort"
  Write-Host "artifact_source=$artifactSourceNormalized"
  if ($artifactSourceNormalized -eq "s3") {
    $offHostSelection = "latest"
    if (-not [string]::IsNullOrWhiteSpace($OffHostArtifactKey)) {
      $offHostSelection = "explicit"
      $offHostManifestKey = $OffHostArtifactKey -replace '\.dump\.age$', '.manifest.json'
      $offHostSignatureKey = $OffHostArtifactKey -replace '\.dump\.age$', '.manifest.sig'
      Write-Host "off_host_artifact=$(Get-OffHostS3Uri -Bucket $OffHostS3Bucket -Key $OffHostArtifactKey)"
      Write-Host "off_host_manifest=$(Get-OffHostS3Uri -Bucket $OffHostS3Bucket -Key $offHostManifestKey)"
      Write-Host "off_host_signature=$(Get-OffHostS3Uri -Bucket $OffHostS3Bucket -Key $offHostSignatureKey)"
    } else {
      $artifactPreviewKey = Get-OffHostS3Key -Prefix $OffHostS3Prefix -RelativeKey "daily/<latest>.dump.age"
      $manifestPreviewKey = Get-OffHostS3Key -Prefix $OffHostS3Prefix -RelativeKey "daily/<latest>.manifest.json"
      $signaturePreviewKey = Get-OffHostS3Key -Prefix $OffHostS3Prefix -RelativeKey "daily/<latest>.manifest.sig"
      Write-Host "off_host_artifact=$(Get-OffHostS3Uri -Bucket $OffHostS3Bucket -Key $artifactPreviewKey)"
      Write-Host "off_host_manifest=$(Get-OffHostS3Uri -Bucket $OffHostS3Bucket -Key $manifestPreviewKey)"
      Write-Host "off_host_signature=$(Get-OffHostS3Uri -Bucket $OffHostS3Bucket -Key $signaturePreviewKey)"
    }
    Write-Host "off_host_selection=$offHostSelection"
    Write-Host "off_host_credentials=runtime_environment_only"
  } else {
    $backupSelection = "explicit"
    if ([string]::IsNullOrWhiteSpace($BackupFile)) {
      $backupSelection = "latest"
    }
    Write-Host "backup_selection=$backupSelection"
  }
  Write-Host "age_identity_required=true"
  Write-Host "manifest_signature_required=ed25519"
  Write-Host "maximum_latest_age_hours=$MaximumLatestAgeHours"
  Write-Host "canonical_health=$HealthUrl"
  exit 0
}

if ([string]::IsNullOrWhiteSpace($AgeIdentityPath)) {
  $AgeIdentityPath = Join-Path (Get-HomePath) ".config/age/shamrai-backup-identity.txt"
}
if (-not (Test-Path -LiteralPath $AgeIdentityPath)) {
  throw "Age identity file was not found: $AgeIdentityPath"
}

if ([string]::IsNullOrWhiteSpace($SshKeyPath)) {
  $defaultSshKeyPath = Join-Path (Get-HomePath) ".ssh/codex_deploy_ed25519"
  if (Test-Path -LiteralPath $defaultSshKeyPath) {
    $SshKeyPath = $defaultSshKeyPath
  }
}

if ([string]::IsNullOrWhiteSpace($SshKeyPath) -or -not (Test-Path -LiteralPath $SshKeyPath)) {
  throw "A valid SSH key is required. Pass -SshKeyPath or set SHAMRAI_SSH_KEY_PATH. Do not use passwords for restore drills."
}

$script:SshTool = Find-Tool @("ssh.exe", "ssh")
$script:ScpTool = Find-Tool @("scp.exe", "scp")
$ageTool = Find-Tool @("age.exe", "age")
$openSslTool = Find-Tool @(
  "openssl.exe",
  "openssl",
  "C:\Program Files\Git\usr\bin\openssl.exe",
  "C:\Program Files\Git\mingw64\bin\openssl.exe"
)
if (-not $script:SshTool -or -not $script:ScpTool) {
  throw "OpenSSH ssh/scp not found. Install OpenSSH Client."
}
if (-not $ageTool) {
  throw "age was not found. Install age before running restore drills."
}
if (-not $openSslTool) {
  throw "OpenSSL with Ed25519 pkeyutl support is required before running restore drills."
}
if ($artifactSourceNormalized -eq "s3") {
  if ([string]::IsNullOrWhiteSpace($env:AWS_ACCESS_KEY_ID) -or [string]::IsNullOrWhiteSpace($env:AWS_SECRET_ACCESS_KEY)) {
    throw "ArtifactSource S3 requires AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in the runtime environment."
  }
  $script:AwsTool = Find-Tool @("aws.exe", "aws")
  if (-not $script:AwsTool) {
    throw "aws CLI is required when ArtifactSource is S3."
  }
  $env:AWS_EC2_METADATA_DISABLED = "true"
  $env:AWS_PAGER = ""
}

$hostName = Get-SshHostName -Target $Server
$KnownHostsPath = Initialize-KnownHosts `
  -HostName $hostName `
  -Port $SshPort `
  -ExpectedFingerprint $HostKeyFingerprint `
  -Path $KnownHostsPath `
  -SkipScan ([bool]$NoHostKeyScan)

$deployDir = Join-Path $Workspace ".deploy"
$localStage = Join-Path ([IO.Path]::GetTempPath()) (
  "shamrai-restore-drill-{0}-{1}" -f $drillId, [Guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $localStage | Out-Null
Set-OwnerOnlyLocalPath -Path $localStage -Directory $true
$script:SensitiveLocalDirectories += $localStage
$publicKeyLocal = Join-Path $localStage "manifest-signing-public.der"
[IO.File]::WriteAllBytes($publicKeyLocal, $manifestSigningPublicKeyDer)
Set-OwnerOnlyLocalPath -Path $publicKeyLocal -Directory $false

$gitTool = Find-Tool @("git.exe", "git")
if (-not $gitTool) {
  throw "Git is required to build the candidate restore image from an exact commit."
}
$gitStatus = @(& $gitTool -C $Workspace status --porcelain=v1 --untracked-files=normal)
if ($LASTEXITCODE -ne 0 -or $gitStatus.Count -ne 0) {
  throw "Restore drill requires a clean worktree so the candidate image matches an exact commit SHA."
}
$releaseSha = ((& $gitTool -C $Workspace rev-parse --verify HEAD) -join "").Trim().ToLowerInvariant()
$releaseEpoch = ((& $gitTool -C $Workspace show -s --format=%ct $releaseSha) -join "").Trim()
if ($releaseSha -notmatch '^[0-9a-f]{40}$' -or $releaseEpoch -notmatch '^\d+$') {
  throw "Unable to resolve exact release identity for restore drill."
}
$releaseBuildTime = [DateTimeOffset]::FromUnixTimeSeconds([int64]$releaseEpoch).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
$candidateBackendArchive = Join-Path $localStage "candidate-backend.tar.gz"
Invoke-NativeChecked -FilePath $gitTool -Arguments @(
  "-C", $Workspace,
  "archive", "--format=tar.gz", "--output=$candidateBackendArchive", $releaseSha, "backend"
)

$script:remoteBackup = ""
$script:remoteManifest = ""
$script:remoteSignature = ""
$script:offHostArtifactKey = ""
$script:offHostManifestKey = ""
$script:offHostSignatureKey = ""
if ($artifactSourceNormalized -eq "s3") {
  Invoke-Step "Select committed off-host backup" {
    if ([string]::IsNullOrWhiteSpace($OffHostArtifactKey)) {
      $pair = Get-LatestOffHostArtifactPair
      $script:offHostArtifactKey = $pair.ArtifactKey
      $script:offHostManifestKey = $pair.ManifestKey
      $script:offHostSignatureKey = $pair.SignatureKey
    } else {
      $script:offHostArtifactKey = $OffHostArtifactKey
      $script:offHostManifestKey = $OffHostArtifactKey -replace '\.dump\.age$', '.manifest.json'
      $script:offHostSignatureKey = $OffHostArtifactKey -replace '\.dump\.age$', '.manifest.sig'
    }
  }
} else {
  Invoke-Step "Locate signed backup commit marker" {
    $selection = Invoke-RemoteOutputChecked -Command (New-BackupSelectionCommand -RemoteAppPath $RemotePath -RequestedBackup $BackupFile)
    $lines = @($selection -split "`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if ($lines.Count -ne 3) {
      throw "Backup selection did not return exactly one encrypted artifact, manifest, and signature."
    }
    $script:remoteBackup = $lines[0].Trim()
    $script:remoteManifest = $lines[1].Trim()
    $script:remoteSignature = $lines[2].Trim()
  }
}

$encryptedLocal = Join-Path $localStage "backup.dump.age"
$manifestLocal = Join-Path $localStage "backup.manifest.json"
$signatureLocal = Join-Path $localStage "backup.manifest.sig"
$dumpLocal = Join-Path $localStage "restore.dump"
$script:SensitiveLocalFiles += $dumpLocal

Invoke-Step "Fetch signed manifest commit marker" {
  if ($artifactSourceNormalized -eq "s3") {
    Copy-FromOffHostChecked -ObjectKey $script:offHostManifestKey -LocalPath $manifestLocal
    Copy-FromOffHostChecked -ObjectKey $script:offHostSignatureKey -LocalPath $signatureLocal
    Assert-OffHostObjectMetadata -ObjectKey $script:offHostManifestKey -LocalPath $manifestLocal
    Assert-OffHostObjectMetadata -ObjectKey $script:offHostSignatureKey -LocalPath $signatureLocal
  } else {
    Copy-FromRemoteChecked -RemoteFilePath $script:remoteManifest -LocalPath $manifestLocal
    Copy-FromRemoteChecked -RemoteFilePath $script:remoteSignature -LocalPath $signatureLocal
  }
}

Invoke-Step "Authenticate signed backup candidate" {
  $expectedEncryptedFile = if ($artifactSourceNormalized -eq "s3") {
    ($script:offHostArtifactKey -split '/')[-1]
  } else {
    ($script:remoteBackup -split '/')[-1]
  }
  $expectedManifestFile = if ($artifactSourceNormalized -eq "s3") {
    ($script:offHostManifestKey -split '/')[-1]
  } else {
    ($script:remoteManifest -split '/')[-1]
  }
  $expectedSignatureFile = if ($artifactSourceNormalized -eq "s3") {
    ($script:offHostSignatureKey -split '/')[-1]
  } else {
    ($script:remoteSignature -split '/')[-1]
  }
  $signatureArguments = @{
    ManifestPath = $manifestLocal
    SignaturePath = $signatureLocal
    PublicKeyDerPath = $publicKeyLocal
    OpenSslPath = $openSslTool
    ExpectedManifestFileName = $expectedManifestFile
    ExpectedSignatureFileName = $expectedSignatureFile
    ExpectedEncryptedFileName = $expectedEncryptedFile
  }
  if ($artifactSourceNormalized -eq "s3") {
    $signatureArguments.ExpectedS3Bucket = $OffHostS3Bucket
    $signatureArguments.ExpectedS3Prefix = $OffHostS3Prefix
    $signatureArguments.ExpectedArtifactKey = $script:offHostArtifactKey
    $signatureArguments.ExpectedManifestKey = $script:offHostManifestKey
    $signatureArguments.ExpectedSignatureKey = $script:offHostSignatureKey
  }
  $script:VerifiedManifestBundle = Assert-ShamraiSignedBackupManifest @signatureArguments
  $script:ManifestSha256 = $script:VerifiedManifestBundle.ManifestSha256
  $script:ManifestSignatureSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $signatureLocal).Hash.ToLowerInvariant()
  $script:ManifestSigningPublicKeySha256 = $script:VerifiedManifestBundle.PublicKeySha256
  $createdAt = $script:VerifiedManifestBundle.CreatedAt
  $nowUtc = [DateTimeOffset]::UtcNow
  if ($createdAt -gt $nowUtc.AddMinutes(10)) {
    throw "Backup manifest created_at is unreasonably far in the future."
  }
  if ($usingImplicitLatest -and $createdAt -lt $nowUtc.AddHours(-$MaximumLatestAgeHours)) {
    throw "Newest committed S3 backup is older than $MaximumLatestAgeHours hours."
  }
  $script:BackupCreatedAtUtc = $createdAt
  Write-Host "signature_verified=true"
  if ($artifactSourceNormalized -eq "s3") {
    Write-Host "artifact_key=$script:offHostArtifactKey"
    Write-Host "manifest_key=$script:offHostManifestKey"
    Write-Host "signature_key=$script:offHostSignatureKey"
  } else {
    Write-Host "backup=$script:remoteBackup"
    Write-Host "manifest=$script:remoteManifest"
    Write-Host "signature=$script:remoteSignature"
  }
}

Invoke-Step "Fetch authenticated encrypted backup" {
  if ($artifactSourceNormalized -eq "s3") {
    Copy-FromOffHostChecked -ObjectKey $script:offHostArtifactKey -LocalPath $encryptedLocal
    Assert-OffHostObjectMetadata -ObjectKey $script:offHostArtifactKey -LocalPath $encryptedLocal
  } else {
    Copy-FromRemoteChecked -RemoteFilePath $script:remoteBackup -LocalPath $encryptedLocal
  }
}

Invoke-Step "Decrypt backup locally" {
  $manifest = $script:VerifiedManifestBundle.Manifest
  $artifact = Get-JsonPropertyValue -Object $manifest -Name "artifact"
  if ($null -eq $artifact) {
    throw "Backup manifest is missing artifact metadata."
  }
  $expectedEncryptedHash = [string](Get-JsonPropertyValue -Object $artifact -Name "encrypted_sha256" -DefaultValue "")
  $expectedEncryptedSize = 0L
  [long]::TryParse(
    [string](Get-JsonPropertyValue -Object $artifact -Name "encrypted_size_bytes" -DefaultValue "0"),
    [ref]$expectedEncryptedSize
  ) | Out-Null
  if ($expectedEncryptedHash -notmatch '^[0-9a-fA-F]{64}$' -or $expectedEncryptedSize -lt 1) {
    throw "Signed restore requires encrypted artifact hash and size fields."
  }

  $actualEncryptedHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $encryptedLocal).Hash.ToLowerInvariant()
  $actualEncryptedSize = (Get-Item -LiteralPath $encryptedLocal).Length
  if ($actualEncryptedHash -ne $expectedEncryptedHash.ToLowerInvariant() -or $actualEncryptedSize -ne $expectedEncryptedSize) {
    throw "Encrypted backup hash/size does not match the signed manifest."
  }

  Invoke-NativeChecked -FilePath $ageTool -Arguments @("-d", "-i", $AgeIdentityPath, "-o", $dumpLocal, $encryptedLocal)
  Set-OwnerOnlyLocalPath -Path $dumpLocal -Directory $false
  $expectedDumpHash = [string](Get-JsonPropertyValue -Object $artifact -Name "dump_sha256" -DefaultValue "")
  if ($expectedDumpHash -notmatch '^[0-9a-fA-F]{64}$') {
    throw "Backup manifest contains an invalid decrypted dump hash."
  }
  $actualDumpHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $dumpLocal).Hash.ToLowerInvariant()
  if ($actualDumpHash -ne $expectedDumpHash.ToLowerInvariant()) {
    throw "Decrypted dump hash does not match the manifest."
  }
}

$remoteStage = ""
Invoke-Step "Create isolated remote restore stage" {
  $stageOutput = Invoke-RemoteOutputChecked -Command "stage=`$(mktemp -d /tmp/shamrai-restore-drill.XXXXXX); chmod 0700 `"`$stage`"; realpath -e -- `"`$stage`""
  $stageLines = @($stageOutput -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^/tmp/shamrai-restore-drill\.[A-Za-z0-9]{6}$' })
  if ($stageLines.Count -ne 1) {
    throw "Remote mktemp did not return one validated restore stage directory."
  }
  $script:remoteStage = $stageLines[0].Trim()
  $script:RemoteStageToCleanup = $script:remoteStage
  Write-Host "remote_stage=$script:remoteStage"
}

$restoreScript = New-RemoteRestoreDrillScript `
  -RemoteAppPath $RemotePath `
  -Project $ComposeProject `
  -DrillProject $drillProject `
  -BackendPort $DrillBackendPort `
  -FrontendPort $DrillFrontendPort `
  -CanonicalHealth $HealthUrl `
  -ReleaseSha $releaseSha `
  -ReleaseBuildTime $releaseBuildTime `
  -Keep ([bool]$KeepDrillProject)

Assert-NoUnresolvedTemplatePlaceholders -Content $restoreScript -Placeholders @(
  "__REMOTE_PATH__",
  "__COMPOSE_PROJECT__",
  "__DRILL_PROJECT__",
  "__DRILL_BACKEND_PORT__",
  "__DRILL_FRONTEND_PORT__",
  "__CANON_HEALTH_URL__",
  "__RELEASE_SHA__",
  "__RELEASE_BUILD_TIME__",
  "__KEEP_DRILL_PROJECT__"
)

$localRestoreScript = Join-Path $localStage "restore-drill.remote.sh"
Set-Utf8NoBomLfContent -Path $localRestoreScript -Content $restoreScript

Invoke-Step "Upload restore drill inputs" {
  Copy-ToRemoteChecked -LocalPath $dumpLocal -RemoteFilePath "$script:remoteStage/restore.dump"
  Copy-ToRemoteChecked -LocalPath $manifestLocal -RemoteFilePath "$script:remoteStage/restore.manifest.json"
  Copy-ToRemoteChecked -LocalPath $candidateBackendArchive -RemoteFilePath "$script:remoteStage/candidate-backend.tar.gz"
  Copy-ToRemoteChecked -LocalPath $localRestoreScript -RemoteFilePath "$script:remoteStage/restore-drill.sh"
}

Invoke-Step "Run isolated restore drill" {
  $script:drillOutput = Invoke-RemoteOutputChecked -Command "chmod 0700 $(ConvertTo-ShellSingleQuoted "$script:remoteStage/restore-drill.sh") && bash $(ConvertTo-ShellSingleQuoted "$script:remoteStage/restore-drill.sh")"
  Write-Host $script:drillOutput
  $shaMatch = [regex]::Match($script:drillOutput, '(?m)^RESTORE_DRILL_VERSION_SHA=([0-9a-f]{40})$')
  if (-not $shaMatch.Success -or $script:drillOutput -notmatch '(?m)^RESTORE_DRILL_OK\s') {
    throw "Restore drill did not return a valid version/result attestation."
  }
  $script:DrillVersionSha = $shaMatch.Groups[1].Value
}

Remove-Item -LiteralPath $localStage -Recurse -Force
$script:SensitiveLocalDirectories = @($script:SensitiveLocalDirectories | Where-Object { $_ -ne $localStage })
$script:RemoteStageToCleanup = ""

if ($artifactSourceNormalized -eq "s3") {
  if ([string]::IsNullOrWhiteSpace($AttestationPath)) {
    $AttestationPath = Join-Path $deployDir "shamrai-restore-attestation.json"
  }
  $attestation = [ordered]@{
    schema_version = 1
    status = "passed"
    repository = [string]$env:GITHUB_REPOSITORY
    workflow_run_id = if ([string]$env:GITHUB_RUN_ID -match '^\d+$') { [int64]$env:GITHUB_RUN_ID } else { 0 }
    workflow_run_attempt = if ([string]$env:GITHUB_RUN_ATTEMPT -match '^\d+$') { [int]$env:GITHUB_RUN_ATTEMPT } else { 0 }
    git_sha = ([string]$env:GITHUB_SHA).ToLowerInvariant()
    artifact_source = "s3"
    bucket = $OffHostS3Bucket
    prefix = $OffHostS3Prefix
    endpoint_url = $OffHostS3EndpointUrl
    region = $OffHostS3Region
    writer_access_key_id_sha256 = Get-StringSha256Hex -Value $env:AWS_ACCESS_KEY_ID
    artifact_key = $script:offHostArtifactKey
    manifest_key = $script:offHostManifestKey
    signature_key = $script:offHostSignatureKey
    manifest_sha256 = $script:ManifestSha256
    signature_sha256 = $script:ManifestSignatureSha256
    signing_public_key_sha256 = $script:ManifestSigningPublicKeySha256
    signature_algorithm = "Ed25519"
    signature_verified = $true
    backup_created_at = $script:BackupCreatedAtUtc.ToUniversalTime().ToString("o")
    verified_at = [DateTimeOffset]::UtcNow.ToString("o")
    backend_git_sha = $script:DrillVersionSha
  }
  Set-Utf8NoBomLfContent -Path $AttestationPath -Content (($attestation | ConvertTo-Json -Depth 5) + "`n")
  Write-Host "restore_attestation=$AttestationPath"
}

Write-Host ""
Write-Host "shamrai_restore_drill_ok" -ForegroundColor Green
