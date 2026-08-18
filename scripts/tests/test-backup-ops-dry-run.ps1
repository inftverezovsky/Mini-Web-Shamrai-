param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$configureScript = Join-Path $repoRoot "scripts/configure-shamrai-backups.ps1"
$restoreScript = Join-Path $repoRoot "scripts/run-shamrai-restore-drill.ps1"
$credentialScript = Join-Path $repoRoot "scripts/install-shamrai-backup-systemd-credentials.ps1"
$manifestSignatureModule = Join-Path $repoRoot "scripts/lib/BackupManifestSignature.psm1"
$powerShellExecutable = (Get-Process -Id $PID).Path
$syntheticServer = "deploy@example.invalid"
$syntheticRemotePath = "/srv/example-app"

$isolatedEnvironmentNames = @(
  "SHAMRAI_BACKUP_S3_BUCKET",
  "SHAMRAI_BACKUP_S3_PREFIX",
  "SHAMRAI_BACKUP_S3_ENDPOINT_URL",
  "SHAMRAI_BACKUP_CREDENTIAL_WRAPPER",
  "SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64",
  "SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_PEM",
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
$env:SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64 = $null
$env:SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_PEM = $null
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

function Assert-Throws {
  param(
    [Parameter(Mandatory = $true)][scriptblock]$Script,
    [Parameter(Mandatory = $true)][string]$Description
  )

  try {
    & $Script
  } catch {
    return
  }
  throw "Expected failure was not raised: $Description"
}

function Invoke-TestNativeChecked {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(Mandatory = $true)][string[]]$Arguments
  )

  & $FilePath @Arguments 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Synthetic signing command failed: $FilePath $($Arguments[0])"
  }
}

function Find-TestOpenSsl {
  foreach ($candidate in @(
    "openssl.exe",
    "openssl",
    "C:\Program Files\Git\usr\bin\openssl.exe",
    "C:\Program Files\Git\mingw64\bin\openssl.exe"
  )) {
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
  }
  throw "OpenSSL is required for manifest signing tests."
}

function Find-TestBash {
  foreach ($candidate in @(
    "bash",
    "C:\Program Files\Git\bin\bash.exe",
    "C:\Program Files\Git\usr\bin\bash.exe"
  )) {
    if (Test-Path -LiteralPath $candidate) { return $candidate }
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
  }
  throw "Bash is required for generated backup-script syntax tests."
}

function Test-GeneratedBashSyntax {
  param(
    [Parameter(Mandatory = $true)][string]$BashPath,
    [Parameter(Mandatory = $true)][string]$Content,
    [Parameter(Mandatory = $true)][string]$Description
  )

  $temporaryPath = Join-Path ([IO.Path]::GetTempPath()) (
    "shamrai-generated-bash-{0}.sh" -f [Guid]::NewGuid().ToString("N")
  )
  try {
    [IO.File]::WriteAllText(
      $temporaryPath,
      ($Content -replace "`r`n", "`n"),
      (New-Object Text.UTF8Encoding($false))
    )
    if (
      [Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT -and
      (Get-Command wsl.exe -ErrorAction SilentlyContinue) -and
      $temporaryPath -match '^([A-Za-z]):\\(.*)$'
    ) {
      $wslPath = "/mnt/$($Matches[1].ToLowerInvariant())/$($Matches[2].Replace('\', '/'))"
      $bashOutput = @(& wsl.exe bash -n -- $wslPath 2>&1)
    } else {
      $bashOutput = @(& $BashPath -n -- $temporaryPath 2>&1)
    }
    if ($LASTEXITCODE -ne 0) {
      throw "Generated Bash failed syntax validation: $Description`n$($bashOutput -join "`n")"
    }
  } finally {
    Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
  }
}

function Import-TestFunctionFromAst {
  param(
    [Parameter(Mandatory = $true)]$Ast,
    [Parameter(Mandatory = $true)][string]$Name
  )

  $functionAst = $Ast.Find(
    {
      param($node)
      $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $Name
    },
    $true
  )
  if ($null -eq $functionAst) {
    throw "Generated-script function was not found: $Name"
  }
  return $functionAst.Extent.Text
}

$credentialDryRun = Invoke-ScriptCapture -ScriptPath $credentialScript -Arguments @(
  "-DryRun",
  "-Server", $syntheticServer
)
if ($credentialDryRun.ExitCode -ne 0) {
  throw "Systemd credential DryRun failed:`n$($credentialDryRun.Output)"
}
Assert-Contains -Actual $credentialDryRun.Output -Expected "credential_store=/etc/credstore.encrypted"
Assert-Contains -Actual $credentialDryRun.Output -Expected "aws_access_key_id=shamrai-backup-aws-access-key-id.cred"
Assert-Contains -Actual $credentialDryRun.Output -Expected "aws_secret_access_key=shamrai-backup-aws-secret-access-key.cred"
Assert-Contains -Actual $credentialDryRun.Output -Expected "manifest_signing_private_key=shamrai-backup-manifest-signing-private-key.cred"
Assert-Contains -Actual $credentialDryRun.Output -Expected "manifest_signing_mode=validate-existing-on-vds"
Assert-NotContains -Actual $credentialDryRun.Output -Unexpected "AWS_SECRET_ACCESS_KEY="

$credentialBootstrapDryRun = Invoke-ScriptCapture -ScriptPath $credentialScript -Arguments @(
  "-DryRun",
  "-BootstrapManifestSigningKey",
  "-Server", $syntheticServer
)
if ($credentialBootstrapDryRun.ExitCode -ne 0) {
  throw "Systemd signing-key bootstrap DryRun failed:`n$($credentialBootstrapDryRun.Output)"
}
Assert-Contains -Actual $credentialBootstrapDryRun.Output -Expected "manifest_signing_mode=bootstrap-or-reuse-on-vds"
Assert-Contains -Actual $credentialBootstrapDryRun.Output -Expected "access_key_configured=False"
Assert-Contains -Actual $credentialBootstrapDryRun.Output -Expected "secret_key_configured=False"

$credentialSource = Get-Content -Raw -LiteralPath $credentialScript
$credentialTokens = $null
$credentialParseErrors = $null
$credentialAst = [System.Management.Automation.Language.Parser]::ParseFile(
  $credentialScript,
  [ref]$credentialTokens,
  [ref]$credentialParseErrors
)
if ($credentialParseErrors.Count -gt 0) {
  throw "Credential installer did not parse cleanly."
}
$credentialParameterNames = @(
  $credentialAst.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath }
)
foreach ($forbiddenCredentialParameter in @("AccessKeyId", "SecretAccessKey", "SessionToken", "ManifestSigningPrivateKeyPem")) {
  if ($forbiddenCredentialParameter -in $credentialParameterNames) {
    throw "Credential installer exposes $forbiddenCredentialParameter as a command-line parameter."
  }
}
Assert-Contains -Actual $credentialSource -Expected '[Environment]::GetEnvironmentVariable("SHAMRAI_BACKUP_S3_ACCESS_KEY_ID", "Process")'
Assert-Contains -Actual $credentialSource -Expected 'stage="$(mktemp -d /root/.shamrai-backup-credentials.XXXXXX)"'
Assert-Contains -Actual $credentialSource -Expected 'flock -n 9'
Assert-Contains -Actual $credentialSource -Expected 'rotation_dir="$(mktemp -d "$CREDENTIAL_STORE/.shamrai-rotation.XXXXXX")"'
Assert-Contains -Actual $credentialSource -Expected 'systemd-creds encrypt --name="$credential_name" - "$staged_path"'
Assert-Contains -Actual $credentialSource -Expected 'openssl genpkey -algorithm ED25519 |'
Assert-Contains -Actual $credentialSource -Expected 'systemd-creds encrypt --name=manifest_signing_private_key - "$new_dir/signing.cred"'
Assert-Contains -Actual $credentialSource -Expected 'Signing credential appeared during bootstrap; refusing implicit replacement.'
Assert-Contains -Actual $credentialSource -Expected 'S3 credential promotion failed; restoring the complete previous S3 credential set.'
Assert-Contains -Actual $credentialSource -Expected 'CRITICAL: credential rollback was incomplete; backup scheduling requires manual repair.'
Assert-Contains -Actual $credentialSource -Expected 'restore_previous_credential "$SESSION_TARGET" session.cred'
Assert-Contains -Actual $credentialSource -Expected 'rm -f -- "$SESSION_TARGET"'
Assert-Contains -Actual $credentialSource -Expected 'sync_session_credential_unit'
Assert-Contains -Actual $credentialSource -Expected 'systemctl daemon-reload'
Assert-Contains -Actual $credentialSource -Expected '/^LoadCredentialEncrypted=aws_session_token:/ { next }'
Assert-Contains -Actual $credentialSource -Expected '/^LoadCredentialEncrypted=manifest_signing_private_key:/ { next }'
Assert-Contains -Actual $credentialSource -Expected 'systemd-creds decrypt --name=manifest_signing_private_key'
Assert-Contains -Actual $credentialSource -Expected 'openssl pkey -pubout -outform DER'
Assert-Contains -Actual $credentialSource -Expected 'SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64=%s'
Assert-Contains -Actual $credentialSource -Expected 'shamrai-backup-manifest-signing-private-key.cred'
Assert-Contains -Actual $credentialSource -Expected 'SHAMRAI_CREDENTIAL_STAGE=/root/\.shamrai-backup-credentials\.'
Assert-Contains -Actual $credentialSource -Expected "Invoke-NativeInputChecked"
Assert-NotContains -Actual $credentialSource -Unexpected "SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_PEM"
Assert-NotContains -Actual $credentialSource -Unexpected 'stage_credential manifest_signing_private_key'
Assert-NotContains -Actual $credentialSource -Unexpected 'signing_private_key_b64'
Assert-NotContains -Actual $credentialSource -Unexpected '/tmp/shamrai-install-backup-credentials.'
Assert-NotContains -Actual $credentialSource -Unexpected '*.cred.tmp'

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
Assert-Contains -Actual $offHost.Output -Expected "off_host_upload_order=encrypted_dump,manifest,signature"
Assert-Contains -Actual $offHost.Output -Expected "s3://example-backups/shamrai/daily/<backup>.dump.age"
Assert-Contains -Actual $offHost.Output -Expected "off_host_schedule=systemd_credentials"
Assert-Contains -Actual $offHost.Output -Expected "systemd_service=shamrai-db-backup.service"
Assert-Contains -Actual $offHost.Output -Expected "credential_store=/etc/credstore.encrypted"
Assert-Contains -Actual $offHost.Output -Expected "s3://example-backups/shamrai/daily/<backup>.manifest.sig"
Assert-Contains -Actual $offHost.Output -Expected "manifest_signing=ed25519_systemd_credential"
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
if ($offHostWithWrapper.ExitCode -eq 0) {
  throw "Off-host credential wrapper bypass was accepted:`n$($offHostWithWrapper.Output)"
}
Assert-Contains -Actual $offHostWithWrapper.Output -Expected "disabled for S3 release backups"

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
Assert-Contains -Actual $selfWrapper.Output -Expected "disabled for S3 release backups"

$configureSource = Get-Content -Raw -LiteralPath $configureScript
$encryptIndex = $configureSource.IndexOf('age -r "$SHAMRAI_BACKUP_AGE_RECIPIENT" -o "$encrypted_tmp"')
$manifestIndex = $configureSource.IndexOf('python3 - "$manifest_tmp"')
$signatureIndex = $configureSource.IndexOf('openssl pkeyutl -sign -rawin')
$localCommitIndex = $configureSource.IndexOf('mv -f -- "$encrypted_tmp" "$encrypted_path"')
$signatureCommitIndex = $configureSource.IndexOf('mv -f -- "$signature_tmp" "$signature_path"')
$plaintextRemovalIndex = $configureSource.IndexOf('rm -f "$dump_path" "$counts_path"')
$offHostCallIndex = $configureSource.LastIndexOf("upload_offhost_bundle ")
$lockIndex = $configureSource.IndexOf('flock -n 9')
$timestampIndex = $configureSource.IndexOf('timestamp="$(date -u +%Y%m%dT%H%M%SZ)"')
$dailyPruneIndex = $configureSource.IndexOf('prune_dir "$daily_dir"')
$offHostFailureExitIndex = $configureSource.IndexOf('if [ "$offhost_status" -ne 0 ]')
$wrapperIdentityCheckIndex = $configureSource.IndexOf('[ "$OFFHOST_CREDENTIAL_WRAPPER" -ef "$OPS_DIR/backup-db.sh" ]')
$awsPreflightIndex = $configureSource.IndexOf('aws CLI is required before off-host backup scheduling can be installed.')
$systemdCredentialPreflightIndex = $configureSource.IndexOf('Encrypted systemd credential is missing or empty:')
$installerWriteIndex = $configureSource.IndexOf('install -d -o root -g root -m 0700 "$OPS_DIR"')
if (
  $encryptIndex -lt 0 -or
  $manifestIndex -le $encryptIndex -or
  $signatureIndex -le $manifestIndex -or
  $localCommitIndex -le $signatureIndex -or
  $signatureCommitIndex -le $localCommitIndex -or
  $plaintextRemovalIndex -le $signatureCommitIndex -or
  $offHostCallIndex -le $plaintextRemovalIndex
) {
  throw "Backup generation order must be encrypt -> manifest -> sign -> local signature commit -> off-host upload."
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
  $systemdCredentialPreflightIndex -lt 0 -or
  $installerWriteIndex -lt 0 -or
  $wrapperIdentityCheckIndex -ge $installerWriteIndex -or
  $awsPreflightIndex -ge $installerWriteIndex -or
  $systemdCredentialPreflightIndex -ge $installerWriteIndex
) {
  throw "Credential and aws CLI preflight checks must run before installer writes."
}
Assert-Contains -Actual $configureSource -Expected 'upload_offhost_bundle \'
Assert-Contains -Actual $configureSource -Expected '|| offhost_status=$?'
Assert-Contains -Actual $configureSource -Expected 'cron_backup_command="$credential_wrapper_command -- $backup_script_command"'
Assert-Contains -Actual $configureSource -Expected '"$OFFHOST_CREDENTIAL_WRAPPER" -- "$OPS_DIR/backup-db.sh"'
Assert-Contains -Actual $configureSource -Expected 'LoadCredentialEncrypted=aws_access_key_id:/etc/credstore.encrypted/shamrai-backup-aws-access-key-id.cred'
Assert-Contains -Actual $configureSource -Expected 'LoadCredentialEncrypted=aws_secret_access_key:/etc/credstore.encrypted/shamrai-backup-aws-secret-access-key.cred'
Assert-Contains -Actual $configureSource -Expected 'LoadCredentialEncrypted=manifest_signing_private_key:/etc/credstore.encrypted/shamrai-backup-manifest-signing-private-key.cred'
Assert-Contains -Actual $configureSource -Expected 'SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY_FILE="$CREDENTIALS_DIRECTORY/manifest_signing_private_key"'
Assert-Contains -Actual $configureSource -Expected '"schema_version": 3 if signing_public_key_sha256 else 2'
Assert-Contains -Actual $configureSource -Expected '"algorithm": "Ed25519"'
Assert-Contains -Actual $configureSource -Expected 'openssl pkeyutl -verify -rawin -pubin -keyform DER'
Assert-Contains -Actual $configureSource -Expected '"${file%.dump.age}.manifest.sig"'
Assert-Contains -Actual $configureSource -Expected 'OnCalendar=*-*-* __BACKUP_HOUR_PADDED__:__BACKUP_MINUTE_PADDED__:00 __CRON_TZ_RAW__'
Assert-Contains -Actual $configureSource -Expected 'Assert-S3RetentionAttestation'
Assert-Contains -Actual $configureSource -Expected 'coproc SNAPSHOT_HOLDER'
Assert-Contains -Actual $configureSource -Expected 'SELECT pg_export_snapshot()'
Assert-Contains -Actual $configureSource -Expected '--snapshot="$1"'
Assert-Contains -Actual $configureSource -Expected 'TZ="$SHAMRAI_BACKUP_TIMEZONE" date +%u'
Assert-Contains -Actual $configureSource -Expected 'validate_root_owned_directory_chain "$REMOTE_PATH"'
Assert-Contains -Actual $configureSource -Expected 'backup_script_tmp="$(mktemp "$OPS_DIR/.backup-db.sh.XXXXXX")"'
Assert-Contains -Actual $configureSource -Expected '^/tmp/shamrai-configure-backups\.[A-Za-z0-9]{6}$'
$uploadEncryptedIndex = $configureSource.IndexOf('s3 cp "$encrypted_file"')
$uploadManifestIndex = $configureSource.IndexOf('s3 cp "$manifest_file"')
$uploadSignatureIndex = $configureSource.IndexOf('s3 cp "$signature_file"')
if (
  $uploadEncryptedIndex -lt 0 -or
  $uploadManifestIndex -le $uploadEncryptedIndex -or
  $uploadSignatureIndex -le $uploadManifestIndex
) {
  throw "Off-host upload order must be encrypted dump -> manifest -> signature commit marker."
}
if ($configureSource -match "printf 'AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)=") {
  throw "Static AWS credentials must never be written to backup.env."
}
if ($configureSource -match "printf 'SHAMRAI_BACKUP_MANIFEST_SIGNING_PRIVATE_KEY") {
  throw "Manifest signing private key must never be written to backup.env."
}

$testBash = Find-TestBash
$configureTokens = $null
$configureParseErrors = $null
$configureAst = [System.Management.Automation.Language.Parser]::ParseFile(
  $configureScript,
  [ref]$configureTokens,
  [ref]$configureParseErrors
)
if ($configureParseErrors.Count -gt 0) {
  throw "Backup configure script did not parse cleanly."
}
Invoke-Expression (Import-TestFunctionFromAst -Ast $configureAst -Name "ConvertTo-ShellSingleQuoted")
Invoke-Expression (Import-TestFunctionFromAst -Ast $configureAst -Name "New-RemoteInstallerScript")
$generatedConfigureInstaller = New-RemoteInstallerScript `
  -RemoteAppPath "/srv/example-app" `
  -Project "example-app" `
  -AgeRecipient "age1synthetic" `
  -DailyKeep 14 `
  -WeeklyKeep 8 `
  -Hour 2 `
  -Minute 30 `
  -Timezone "Europe/Moscow" `
  -S3Bucket "example-backups" `
  -S3Prefix "shamrai" `
  -S3EndpointUrl "https://s3.example.invalid" `
  -S3Region "us-east-1" `
  -SigningPublicKeyBase64 "MCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo=" `
  -CredentialWrapper "" `
  -SystemdCredentials $true `
  -BackupNow $false
Test-GeneratedBashSyntax -BashPath $testBash -Content $generatedConfigureInstaller -Description "backup configuration installer"

$credentialInstallerAssignment = $credentialAst.Find(
  {
    param($node)
    $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
      $node.Left.Extent.Text -eq '$installer' -and
      $node.Right.Extent.Text.TrimStart().StartsWith("@'")
  },
  $true
)
if ($null -eq $credentialInstallerAssignment) {
  throw "Credential installer Bash template was not found."
}
$credentialInstallerString = $credentialInstallerAssignment.Right.Find(
  {
    param($node)
    $node -is [System.Management.Automation.Language.StringConstantExpressionAst]
  },
  $true
)
if ($null -eq $credentialInstallerString) {
  throw "Credential installer Bash here-string value was not found."
}
$credentialInstallerTemplate = [string]$credentialInstallerString.SafeGetValue()
Test-GeneratedBashSyntax `
  -BashPath $testBash `
  -Content $credentialInstallerTemplate `
  -Description "systemd credential installer template"
$generatedBootstrapInstaller = $credentialInstallerTemplate.Replace(
  "__REMOTE_PATH_SHELL__", "'/srv/example-app'"
).Replace(
  "__BOOTSTRAP_SIGNING_KEY_SHELL__", "'1'"
).Replace(
  "__EXPECTED_SIGNING_PUBLIC_KEY_SHELL__", "''"
)
$generatedNormalCredentialInstaller = $credentialInstallerTemplate.Replace(
  "__REMOTE_PATH_SHELL__", "'/srv/example-app'"
).Replace(
  "__BOOTSTRAP_SIGNING_KEY_SHELL__", "'0'"
).Replace(
  "__EXPECTED_SIGNING_PUBLIC_KEY_SHELL__", "'MCowBQYDK2VwAyEA11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo='"
)
foreach ($generatedCredentialInstaller in @($generatedBootstrapInstaller, $generatedNormalCredentialInstaller)) {
  if ($generatedCredentialInstaller -match '__[A-Z0-9_]+__') {
    throw "Generated systemd credential installer contains an unresolved placeholder."
  }
}
Test-GeneratedBashSyntax `
  -BashPath $testBash `
  -Content $generatedBootstrapInstaller `
  -Description "VDS-only signing-key bootstrap installer"
Test-GeneratedBashSyntax `
  -BashPath $testBash `
  -Content $generatedNormalCredentialInstaller `
  -Description "existing signing-key validation and S3 credential installer"

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
Invoke-Expression (Import-TestFunctionFromAst -Ast $restoreAst -Name "ConvertTo-ShellSingleQuoted")
Invoke-Expression (Import-TestFunctionFromAst -Ast $restoreAst -Name "New-BackupSelectionCommand")
Invoke-Expression (Import-TestFunctionFromAst -Ast $restoreAst -Name "New-RemoteRestoreDrillScript")
$generatedSelectionCommand = New-BackupSelectionCommand -RemoteAppPath "/srv/example-app" -RequestedBackup ""
Test-GeneratedBashSyntax -BashPath $testBash -Content $generatedSelectionCommand -Description "signed backup selection"
$generatedRestoreInstaller = New-RemoteRestoreDrillScript `
  -RemoteAppPath "/srv/example-app" `
  -Project "example-app" `
  -DrillProject "example-restore-drill" `
  -BackendPort 18000 `
  -FrontendPort 18082 `
  -CanonicalHealth "http://127.0.0.1:8082/api/health" `
  -ReleaseSha ("0" * 40) `
  -ReleaseBuildTime "1970-01-01T00:00:00Z" `
  -Keep $false
Test-GeneratedBashSyntax -BashPath $testBash -Content $generatedRestoreInstaller -Description "restore drill installer"

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
Assert-Contains -Actual $vdsRestore.Output -Expected "manifest_signature_required=ed25519"

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
Assert-Contains -Actual $offHostRestore.Output -Expected "off_host_signature=s3://example-backups/shamrai/daily/shamrai-db.20260717T000000Z.manifest.sig"
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
Assert-Contains -Actual $latestOffHostRestore.Output -Expected "s3://example-backups/shamrai/daily/<latest>.manifest.sig"

$restoreSource = Get-Content -Raw -LiteralPath $restoreScript
Assert-Contains -Actual $restoreSource -Expected 'alembic upgrade head'
Assert-Contains -Actual $restoreSource -Expected 'alembic current --check-heads'
Assert-Contains -Actual $restoreSource -Expected '/api/ready'
Assert-Contains -Actual $restoreSource -Expected 'candidate-backend.tar.gz'
Assert-Contains -Actual $restoreSource -Expected '^/tmp/shamrai-restore-drill\.[A-Za-z0-9]{6}$'
Assert-Contains -Actual $restoreSource -Expected 'MaximumLatestAgeHours'
Assert-Contains -Actual $restoreSource -Expected '[IO.Path]::GetTempPath()'
Assert-Contains -Actual $restoreSource -Expected 'Set-OwnerOnlyLocalPath -Path $localStage -Directory $true'
Assert-Contains -Actual $restoreSource -Expected 'Set-OwnerOnlyLocalPath -Path $dumpLocal -Directory $false'
Assert-Contains -Actual $restoreSource -Expected 'Remove-Item -LiteralPath $localStage -Recurse -Force'
Assert-Contains -Actual $restoreSource -Expected "BackupManifestSignature.psm1"
Assert-Contains -Actual $restoreSource -Expected "Assert-ShamraiSignedBackupManifest"
Assert-Contains -Actual $restoreSource -Expected "signature_verified = `$true"
Assert-Contains -Actual $restoreSource -Expected "signing_public_key_sha256"
Assert-Contains -Actual $restoreSource -Expected "signature_sha256"
Assert-Contains -Actual $restoreSource -Expected "\.manifest\.sig`$"
$authenticateCallIndex = $restoreSource.LastIndexOf('$script:VerifiedManifestBundle = Assert-ShamraiSignedBackupManifest')
$ciphertextFetchIndex = $restoreSource.LastIndexOf('Copy-FromOffHostChecked -ObjectKey $script:offHostArtifactKey')
$decryptIndex = $restoreSource.LastIndexOf('Invoke-NativeChecked -FilePath $ageTool')
if (
  $authenticateCallIndex -lt 0 -or
  $ciphertextFetchIndex -le $authenticateCallIndex -or
  $decryptIndex -le $ciphertextFetchIndex
) {
  throw "Restore must verify the exact signed manifest before fetching or decrypting ciphertext."
}

Import-Module -Name $manifestSignatureModule -Force -ErrorAction Stop
$testOpenSsl = Find-TestOpenSsl
$signatureTestStage = Join-Path ([IO.Path]::GetTempPath()) (
  "shamrai-manifest-signature-test-{0}" -f [Guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $signatureTestStage | Out-Null
try {
  $privateKeyPath = Join-Path $signatureTestStage "synthetic-ed25519-private.pem"
  $publicKeyPath = Join-Path $signatureTestStage "synthetic-ed25519-public.der"
  $wrongPrivateKeyPath = Join-Path $signatureTestStage "wrong-ed25519-private.pem"
  $wrongPublicKeyPath = Join-Path $signatureTestStage "wrong-ed25519-public.der"
  $manifestPath = Join-Path $signatureTestStage "manifest.json"
  $signaturePath = Join-Path $signatureTestStage "manifest.sig"
  $missingPath = Join-Path $signatureTestStage "missing"
  Invoke-TestNativeChecked -FilePath $testOpenSsl -Arguments @("genpkey", "-algorithm", "ED25519", "-out", $privateKeyPath)
  Invoke-TestNativeChecked -FilePath $testOpenSsl -Arguments @("pkey", "-in", $privateKeyPath, "-pubout", "-outform", "DER", "-out", $publicKeyPath)
  Invoke-TestNativeChecked -FilePath $testOpenSsl -Arguments @("genpkey", "-algorithm", "ED25519", "-out", $wrongPrivateKeyPath)
  Invoke-TestNativeChecked -FilePath $testOpenSsl -Arguments @("pkey", "-in", $wrongPrivateKeyPath, "-pubout", "-outform", "DER", "-out", $wrongPublicKeyPath)

  $publicKeyHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $publicKeyPath).Hash.ToLowerInvariant()
  $utf8NoBom = New-Object Text.UTF8Encoding($false)
  function Set-SyntheticSignedManifest {
    param(
      [string]$CreatedAt = "20260717T000000Z",
      [string]$BaseName = "shamrai-db.20260717T000000Z",
      [int]$SchemaVersion = 3,
      [string]$SigningPublicKeyHash = $publicKeyHash
    )

    $payload = [ordered]@{
      schema_version = $SchemaVersion
      manifest_type = "shamrai_postgres_backup"
      created_at = $CreatedAt
      backup_id = $BaseName
      source = [ordered]@{ compose_project = "synthetic"; remote_path = "/synthetic"; database = "postgres" }
      artifact = [ordered]@{
        base_name = $BaseName
        encrypted_file = "$BaseName.dump.age"
        encrypted_sha256 = ("a" * 64)
        encrypted_size_bytes = 123
        dump_sha256 = ("b" * 64)
        dump_size_bytes = 456
        format = "pg_dump custom"
        encryption = "age"
      }
      table_counts = [ordered]@{ users = 1; payment_attempts = 2 }
      storage = [ordered]@{
        bucket = "example-backups"
        prefix = "shamrai"
        artifact_key = "shamrai/daily/$BaseName.dump.age"
        manifest_key = "shamrai/daily/$BaseName.manifest.json"
        signature_key = "shamrai/daily/$BaseName.manifest.sig"
      }
      signature = [ordered]@{ algorithm = "Ed25519"; public_key_sha256 = $SigningPublicKeyHash }
    }
    [IO.File]::WriteAllText($manifestPath, (($payload | ConvertTo-Json -Depth 8) + "`n"), $utf8NoBom)
    Invoke-TestNativeChecked -FilePath $testOpenSsl -Arguments @(
      "pkeyutl", "-sign", "-rawin", "-inkey", $privateKeyPath,
      "-in", $manifestPath, "-out", $signaturePath
    )
  }

  $validNames = @{
    ExpectedManifestFileName = "shamrai-db.20260717T000000Z.manifest.json"
    ExpectedSignatureFileName = "shamrai-db.20260717T000000Z.manifest.sig"
    ExpectedEncryptedFileName = "shamrai-db.20260717T000000Z.dump.age"
    ExpectedS3Bucket = "example-backups"
    ExpectedS3Prefix = "shamrai"
    ExpectedArtifactKey = "shamrai/daily/shamrai-db.20260717T000000Z.dump.age"
    ExpectedManifestKey = "shamrai/daily/shamrai-db.20260717T000000Z.manifest.json"
    ExpectedSignatureKey = "shamrai/daily/shamrai-db.20260717T000000Z.manifest.sig"
  }
  $verifyBase = @{
    ManifestPath = $manifestPath
    SignaturePath = $signaturePath
    PublicKeyDerPath = $publicKeyPath
    OpenSslPath = $testOpenSsl
  }

  Set-SyntheticSignedManifest
  Assert-ShamraiSignedBackupManifest @verifyBase @validNames | Out-Null

  Move-Item -LiteralPath $signaturePath -Destination $missingPath
  Assert-Throws -Description "unsigned manifest" -Script {
    Assert-ShamraiSignedBackupManifest @verifyBase @validNames | Out-Null
  }
  Move-Item -LiteralPath $missingPath -Destination $signaturePath

  Move-Item -LiteralPath $manifestPath -Destination $missingPath
  Assert-Throws -Description "signature without manifest" -Script {
    Assert-ShamraiSignedBackupManifest @verifyBase @validNames | Out-Null
  }
  Move-Item -LiteralPath $missingPath -Destination $manifestPath

  [IO.File]::AppendAllText($manifestPath, " ", $utf8NoBom)
  Assert-Throws -Description "one-byte manifest tamper" -Script {
    Assert-ShamraiSignedBackupManifest @verifyBase @validNames | Out-Null
  }

  Set-SyntheticSignedManifest
  $lfBytes = [IO.File]::ReadAllText($manifestPath).Replace("`r`n", "`n")
  [IO.File]::WriteAllText($manifestPath, $lfBytes.Replace("`n", "`r`n"), $utf8NoBom)
  Assert-Throws -Description "CRLF re-encoding after signing" -Script {
    Assert-ShamraiSignedBackupManifest @verifyBase @validNames | Out-Null
  }

  Set-SyntheticSignedManifest
  $replayedNames = @{} + $validNames
  $replayedNames.ExpectedManifestFileName = "shamrai-db.20260718T000000Z.manifest.json"
  Assert-Throws -Description "signed bundle replayed under a newer filename" -Script {
    Assert-ShamraiSignedBackupManifest @verifyBase @replayedNames | Out-Null
  }

  Set-SyntheticSignedManifest -CreatedAt "20260718T000000Z" -BaseName "shamrai-db.20260717T000000Z"
  $timestampNames = @{} + $validNames
  $timestampNames.ExpectedManifestFileName = "shamrai-db.20260718T000000Z.manifest.json"
  $timestampNames.ExpectedSignatureFileName = "shamrai-db.20260718T000000Z.manifest.sig"
  $timestampNames.ExpectedEncryptedFileName = "shamrai-db.20260718T000000Z.dump.age"
  Assert-Throws -Description "signed created_at and backup_id mismatch" -Script {
    Assert-ShamraiSignedBackupManifest @verifyBase @timestampNames | Out-Null
  }

  Set-SyntheticSignedManifest -SchemaVersion 2
  Assert-Throws -Description "signed legacy schema v2" -Script {
    Assert-ShamraiSignedBackupManifest @verifyBase @validNames | Out-Null
  }

  Set-SyntheticSignedManifest -SigningPublicKeyHash ("0" * 64)
  Assert-Throws -Description "signed manifest with wrong trust-anchor hash" -Script {
    Assert-ShamraiSignedBackupManifest @verifyBase @validNames | Out-Null
  }

  Set-SyntheticSignedManifest
  $wrongKeyArguments = @{} + $verifyBase
  $wrongKeyArguments.PublicKeyDerPath = $wrongPublicKeyPath
  Assert-Throws -Description "manifest checked with the wrong Ed25519 public key" -Script {
    Assert-ShamraiSignedBackupManifest @wrongKeyArguments @validNames | Out-Null
  }
} finally {
  Remove-Item -LiteralPath $signatureTestStage -Recurse -Force -ErrorAction SilentlyContinue
}

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
