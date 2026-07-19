param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$configureScript = Join-Path $repoRoot "scripts/configure-shamrai-backups.ps1"
$restoreScript = Join-Path $repoRoot "scripts/run-shamrai-restore-drill.ps1"
$powerShellExecutable = (Get-Process -Id $PID).Path
$syntheticServer = "deploy@example.invalid"
$syntheticRemotePath = "/srv/example-app"

$isolatedEnvironmentNames = @(
  "SHAMRAI_BACKUP_S3_BUCKET",
  "SHAMRAI_BACKUP_S3_PREFIX",
  "SHAMRAI_BACKUP_S3_ENDPOINT_URL",
  "SHAMRAI_BACKUP_CREDENTIAL_WRAPPER",
  "AWS_REGION",
  "AWS_ACCESS_KEY_ID",
  "AWS_SECRET_ACCESS_KEY",
  "AWS_SESSION_TOKEN"
)
$originalEnvironment = @{}
foreach ($name in $isolatedEnvironmentNames) {
  $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

try {
$env:SHAMRAI_BACKUP_S3_BUCKET = $null
$env:SHAMRAI_BACKUP_S3_PREFIX = $null
$env:SHAMRAI_BACKUP_S3_ENDPOINT_URL = $null
$env:SHAMRAI_BACKUP_CREDENTIAL_WRAPPER = $null
$env:AWS_REGION = $null
$env:AWS_ACCESS_KEY_ID = $null
$env:AWS_SECRET_ACCESS_KEY = $null
$env:AWS_SESSION_TOKEN = $null

function Invoke-ScriptCapture {
  param(
    [Parameter(Mandatory = $true)][string]$ScriptPath,
    [string[]]$Arguments = @()
  )

  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $output = & $powerShellExecutable -NoLogo -NoProfile -File $ScriptPath @Arguments 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  return [pscustomobject]@{
    ExitCode = $exitCode
    Output = ($output -join "`n")
  }
}

function Assert-Contains {
  param(
    [Parameter(Mandatory = $true)][string]$Actual,
    [Parameter(Mandatory = $true)][string]$Expected
  )

  if (-not $Actual.Contains($Expected)) {
    throw "Expected output to contain '$Expected'. Actual output:`n$Actual"
  }
}

function Assert-NotContains {
  param(
    [Parameter(Mandatory = $true)][string]$Actual,
    [Parameter(Mandatory = $true)][string]$Unexpected
  )

  if ($Actual.Contains($Unexpected)) {
    throw "Expected output not to contain '$Unexpected'. Actual output:`n$Actual"
  }
}

$localOnly = Invoke-ScriptCapture -ScriptPath $configureScript -Arguments @(
  "-DryRun",
  "-Server", $syntheticServer,
  "-RemotePath", $syntheticRemotePath,
  "-BackupAgeRecipient", "age1replacewithpublicrecipient"
)
if ($localOnly.ExitCode -ne 0) {
  throw "Local-only configure DryRun failed:`n$($localOnly.Output)"
}
Assert-Contains -Actual $localOnly.Output -Expected "off_host=disabled"
Assert-Contains -Actual $localOnly.Output -Expected "backup_schedule=direct"

$offHost = Invoke-ScriptCapture -ScriptPath $configureScript -Arguments @(
  "-DryRun",
  "-Server", $syntheticServer,
  "-RemotePath", $syntheticRemotePath,
  "-BackupAgeRecipient", "age1replacewithpublicrecipient",
  "-OffHostS3Bucket", "example-backups",
  "-OffHostS3Prefix", "shamrai"
)
if ($offHost.ExitCode -ne 0) {
  throw "Off-host configure DryRun failed:`n$($offHost.Output)"
}
Assert-Contains -Actual $offHost.Output -Expected "off_host=enabled"
Assert-Contains -Actual $offHost.Output -Expected "off_host_upload_order=encrypted_dump,manifest"
Assert-Contains -Actual $offHost.Output -Expected "s3://example-backups/shamrai/daily/<backup>.dump.age"
Assert-Contains -Actual $offHost.Output -Expected "off_host_schedule=blocked_missing_credential_wrapper"
Assert-NotContains -Actual $offHost.Output -Unexpected "AWS_SECRET_ACCESS_KEY"
Assert-NotContains -Actual $offHost.Output -Unexpected "AWS_SESSION_TOKEN"

$wrapperPath = "/usr/local/sbin/shamrai-backup-with-runtime-credentials"
$offHostWithWrapper = Invoke-ScriptCapture -ScriptPath $configureScript -Arguments @(
  "-DryRun",
  "-Server", $syntheticServer,
  "-RemotePath", $syntheticRemotePath,
  "-BackupAgeRecipient", "age1replacewithpublicrecipient",
  "-OffHostS3Bucket", "example-backups",
  "-OffHostS3Prefix", "shamrai",
  "-OffHostCredentialWrapper", $wrapperPath
)
if ($offHostWithWrapper.ExitCode -ne 0) {
  throw "Off-host configure DryRun with credential wrapper failed:`n$($offHostWithWrapper.Output)"
}
Assert-Contains -Actual $offHostWithWrapper.Output -Expected "off_host_schedule=credential_wrapper"
Assert-Contains -Actual $offHostWithWrapper.Output -Expected "off_host_credential_wrapper=$wrapperPath"

$scheduledWithoutWrapper = Invoke-ScriptCapture -ScriptPath $configureScript -Arguments @(
  "-Server", $syntheticServer,
  "-RemotePath", $syntheticRemotePath,
  "-BackupAgeRecipient", "age1testpublicrecipient",
  "-OffHostS3Bucket", "example-backups"
)
if ($scheduledWithoutWrapper.ExitCode -eq 0) {
  throw "Scheduled off-host configuration without credential wrapper was accepted."
}
Assert-Contains -Actual $scheduledWithoutWrapper.Output -Expected "OffHostCredentialWrapper"

$relativeWrapper = Invoke-ScriptCapture -ScriptPath $configureScript -Arguments @(
  "-DryRun",
  "-Server", $syntheticServer,
  "-RemotePath", $syntheticRemotePath,
  "-BackupAgeRecipient", "age1replacewithpublicrecipient",
  "-OffHostS3Bucket", "example-backups",
  "-OffHostCredentialWrapper", "relative/wrapper"
)
if ($relativeWrapper.ExitCode -eq 0) {
  throw "Relative off-host credential wrapper path was accepted."
}
Assert-Contains -Actual $relativeWrapper.Output -Expected "OffHostCredentialWrapper"

$selfWrapper = Invoke-ScriptCapture -ScriptPath $configureScript -Arguments @(
  "-DryRun",
  "-Server", $syntheticServer,
  "-RemotePath", $syntheticRemotePath,
  "-BackupAgeRecipient", "age1replacewithpublicrecipient",
  "-OffHostS3Bucket", "example-backups",
  "-OffHostCredentialWrapper", "$syntheticRemotePath/ops/backup-db.sh"
)
if ($selfWrapper.ExitCode -eq 0) {
  throw "Generated backup script was accepted as its own credential wrapper."
}
Assert-Contains -Actual $selfWrapper.Output -Expected "must not be the generated backup script"

$configureSource = Get-Content -Raw -LiteralPath $configureScript
$encryptIndex = $configureSource.IndexOf('age -r "$SHAMRAI_BACKUP_AGE_RECIPIENT" -o "$encrypted_tmp"')
$manifestIndex = $configureSource.IndexOf('python3 - "$manifest_tmp"')
$localCommitIndex = $configureSource.IndexOf('mv -f -- "$encrypted_tmp" "$encrypted_path"')
$plaintextRemovalIndex = $configureSource.IndexOf('rm -f "$dump_path" "$counts_path"')
$offHostCallIndex = $configureSource.LastIndexOf("upload_offhost_pair ")
$lockIndex = $configureSource.IndexOf('flock -n 9')
$timestampIndex = $configureSource.IndexOf('timestamp="$(date -u +%Y%m%dT%H%M%SZ)"')
$dailyPruneIndex = $configureSource.IndexOf('prune_dir "$daily_dir"')
$offHostFailureExitIndex = $configureSource.IndexOf('if [ "$offhost_status" -ne 0 ]')
$wrapperIdentityCheckIndex = $configureSource.IndexOf('[ "$OFFHOST_CREDENTIAL_WRAPPER" -ef "$OPS_DIR/backup-db.sh" ]')
$awsPreflightIndex = $configureSource.IndexOf('aws CLI is required before off-host backup scheduling can be installed.')
$installerWriteIndex = $configureSource.IndexOf('install -d -m 0700 "$OPS_DIR"')
if (
  $encryptIndex -lt 0 -or
  $manifestIndex -le $encryptIndex -or
  $localCommitIndex -le $manifestIndex -or
  $plaintextRemovalIndex -le $localCommitIndex -or
  $offHostCallIndex -le $plaintextRemovalIndex
) {
  throw "Backup generation order must be encrypt -> manifest -> local commit -> off-host upload."
}
if ($lockIndex -lt 0 -or $timestampIndex -lt 0 -or $lockIndex -ge $timestampIndex) {
  throw "Exclusive flock must be acquired before timestamp generation."
}
if (
  $offHostCallIndex -lt 0 -or
  $dailyPruneIndex -le $offHostCallIndex -or
  $offHostFailureExitIndex -le $dailyPruneIndex
) {
  throw "Local retention must run after upload attempt and before returning a saved off-host failure."
}
if (
  $wrapperIdentityCheckIndex -lt 0 -or
  $awsPreflightIndex -lt 0 -or
  $installerWriteIndex -lt 0 -or
  $wrapperIdentityCheckIndex -ge $installerWriteIndex -or
  $awsPreflightIndex -ge $installerWriteIndex
) {
  throw "Wrapper identity and aws CLI preflight checks must run before installer writes."
}
Assert-Contains -Actual $configureSource -Expected 'upload_offhost_pair \'
Assert-Contains -Actual $configureSource -Expected '|| offhost_status=$?'
Assert-Contains -Actual $configureSource -Expected 'cron_backup_command="$credential_wrapper_command -- $backup_script_command"'
Assert-Contains -Actual $configureSource -Expected '"$OFFHOST_CREDENTIAL_WRAPPER" -- "$OPS_DIR/backup-db.sh"'
if ($configureSource -match "printf 'AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)=") {
  throw "Static AWS credentials must never be written to backup.env."
}

$restoreTokens = $null
$restoreParseErrors = $null
$restoreAst = [System.Management.Automation.Language.Parser]::ParseFile(
  $restoreScript,
  [ref]$restoreTokens,
  [ref]$restoreParseErrors
)
if ($restoreParseErrors.Count -gt 0) {
  throw "Restore script did not parse cleanly."
}
$jsonPropertyFunction = $restoreAst.Find(
  {
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq "Get-JsonPropertyValue"
  },
  $true
)
if ($null -eq $jsonPropertyFunction) {
  throw "Get-JsonPropertyValue helper was not found."
}
Invoke-Expression $jsonPropertyFunction.Extent.Text
$legacyArtifact = [pscustomobject]@{ dump_sha256 = ("a" * 64) }
$legacyEncryptedHash = [string](Get-JsonPropertyValue -Object $legacyArtifact -Name "encrypted_sha256" -DefaultValue "")
if ($legacyEncryptedHash -ne "") {
  throw "Legacy schema v1 manifests must tolerate missing encrypted hash fields for VDS restores."
}

$vdsRestore = Invoke-ScriptCapture -ScriptPath $restoreScript -Arguments @(
  "-DryRun",
  "-Server", $syntheticServer,
  "-RemotePath", $syntheticRemotePath,
  "-ComposeProject", "example-app",
  "-HealthUrl", "http://127.0.0.1:18082/api/health"
)
if ($vdsRestore.ExitCode -ne 0) {
  throw "VDS restore DryRun failed:`n$($vdsRestore.Output)"
}
Assert-Contains -Actual $vdsRestore.Output -Expected "artifact_source=vds"
Assert-Contains -Actual $vdsRestore.Output -Expected "backup_selection=latest"

$artifactKey = "shamrai/daily/shamrai-db.20260717T000000Z.dump.age"
$offHostRestore = Invoke-ScriptCapture -ScriptPath $restoreScript -Arguments @(
  "-DryRun",
  "-Server", $syntheticServer,
  "-RemotePath", $syntheticRemotePath,
  "-ComposeProject", "example-app",
  "-HealthUrl", "http://127.0.0.1:18082/api/health",
  "-ArtifactSource", "S3",
  "-OffHostS3Bucket", "example-backups",
  "-OffHostS3Prefix", "shamrai",
  "-OffHostArtifactKey", $artifactKey
)
if ($offHostRestore.ExitCode -ne 0) {
  throw "Off-host restore DryRun failed:`n$($offHostRestore.Output)"
}
Assert-Contains -Actual $offHostRestore.Output -Expected "artifact_source=s3"
Assert-Contains -Actual $offHostRestore.Output -Expected "off_host_selection=explicit"
Assert-Contains -Actual $offHostRestore.Output -Expected "off_host_artifact=s3://example-backups/$artifactKey"
Assert-Contains -Actual $offHostRestore.Output -Expected "off_host_manifest=s3://example-backups/shamrai/daily/shamrai-db.20260717T000000Z.manifest.json"
Assert-NotContains -Actual $offHostRestore.Output -Unexpected "AWS_SECRET_ACCESS_KEY"
Assert-NotContains -Actual $offHostRestore.Output -Unexpected "AWS_SESSION_TOKEN"

$latestOffHostRestore = Invoke-ScriptCapture -ScriptPath $restoreScript -Arguments @(
  "-DryRun",
  "-Server", $syntheticServer,
  "-RemotePath", $syntheticRemotePath,
  "-ComposeProject", "example-app",
  "-HealthUrl", "http://127.0.0.1:18082/api/health",
  "-ArtifactSource", "S3",
  "-OffHostS3Bucket", "example-backups",
  "-OffHostS3Prefix", "shamrai"
)
if ($latestOffHostRestore.ExitCode -ne 0) {
  throw "Latest off-host restore DryRun failed:`n$($latestOffHostRestore.Output)"
}
Assert-Contains -Actual $latestOffHostRestore.Output -Expected "off_host_selection=latest"
Assert-Contains -Actual $latestOffHostRestore.Output -Expected "s3://example-backups/shamrai/daily/<latest>.manifest.json"

$unsafeRestore = Invoke-ScriptCapture -ScriptPath $restoreScript -Arguments @(
  "-DryRun",
  "-Server", $syntheticServer,
  "-RemotePath", $syntheticRemotePath,
  "-ComposeProject", "example-app",
  "-HealthUrl", "http://127.0.0.1:18082/api/health",
  "-ArtifactSource", "S3",
  "-OffHostS3Bucket", "example-backups",
  "-OffHostS3Prefix", "shamrai",
  "-OffHostArtifactKey", "shamrai/daily/../private.dump.age"
)
if ($unsafeRestore.ExitCode -eq 0) {
  throw "Traversal-like off-host artifact key was accepted:`n$($unsafeRestore.Output)"
}
Assert-Contains -Actual $unsafeRestore.Output -Expected "OffHostArtifactKey"

$insecureEndpoint = Invoke-ScriptCapture -ScriptPath $configureScript -Arguments @(
  "-DryRun",
  "-Server", $syntheticServer,
  "-RemotePath", $syntheticRemotePath,
  "-BackupAgeRecipient", "age1replacewithpublicrecipient",
  "-OffHostS3Bucket", "example-backups",
  "-OffHostS3EndpointUrl", "http://s3.example.invalid"
)
if ($insecureEndpoint.ExitCode -eq 0) {
  throw "Insecure off-host endpoint was accepted:`n$($insecureEndpoint.Output)"
}

Write-Host "backup_ops_dry_run_tests_ok"
} finally {
  foreach ($name in $isolatedEnvironmentNames) {
    [Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], "Process")
  }
}
