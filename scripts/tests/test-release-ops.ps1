$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$deployPath = Join-Path $repoRoot "scripts\deploy-public-shamrai-web.ps1"
$releaseBackupPath = Join-Path $repoRoot "scripts\verify-shamrai-release-backup.ps1"
$hiddenGatePath = Join-Path $repoRoot "scripts\verify-shamrai-hidden-flat-e2e.ps1"
$hiddenGateModulePath = Join-Path $repoRoot "scripts\lib\HiddenFlatE2EGate.psm1"
$ciPath = Join-Path $repoRoot ".github\workflows\ci.yml"
$restorePath = Join-Path $repoRoot ".github\workflows\restore-drill.yml"

function Assert-Contains {
  param([string]$Actual, [string]$Expected)
  if (-not $Actual.Contains($Expected)) {
    throw "Expected source fragment was not found: $Expected"
  }
}

function Assert-NotContains {
  param([string]$Actual, [string]$Unexpected)
  if ($Actual.Contains($Unexpected)) {
    throw "Unexpected source fragment was found: $Unexpected"
  }
}

$tokens = $null
$parseErrors = $null
foreach ($sourcePath in @($deployPath, $releaseBackupPath, $hiddenGatePath, $hiddenGateModulePath)) {
  $tokens = $null
  $parseErrors = $null
  [System.Management.Automation.Language.Parser]::ParseFile(
    $sourcePath,
    [ref]$tokens,
    [ref]$parseErrors
  ) | Out-Null
  if ($parseErrors.Count -ne 0) {
    throw "Release script has PowerShell parse errors in ${sourcePath}: $($parseErrors.Message -join '; ')"
  }
}

$deploySource = Get-Content -Raw -LiteralPath $deployPath
$deployAst = [System.Management.Automation.Language.Parser]::ParseFile(
  $deployPath,
  [ref]$tokens,
  [ref]$parseErrors
)
$sanitizeFunctionAst = $deployAst.Find(
  {
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq 'Invoke-WithSanitizedReleaseEnvironment'
  },
  $true
)
if ($null -eq $sanitizeFunctionAst) {
  throw 'Release environment sanitizer function was not found.'
}
Invoke-Expression $sanitizeFunctionAst.Extent.Text

$invokeStepFunctionAst = $deployAst.Find(
  {
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq 'Invoke-Step'
  },
  $true
)
if ($null -eq $invokeStepFunctionAst) {
  throw 'Release step runner function was not found.'
}
Invoke-Expression $invokeStepFunctionAst.Extent.Text
$stepReturnFixture = Invoke-Step 'Capture return fixture' { '/tmp/shamrai-public-release.A1b2C3' }
if ([string]$stepReturnFixture -cne '/tmp/shamrai-public-release.A1b2C3') {
  throw 'Release step runner did not preserve its scriptblock return value.'
}

foreach ($functionName in @('ConvertTo-ShellSingleQuoted', 'New-FencedRemoteCommand')) {
  $functionAst = $deployAst.Find(
    {
      param($node)
      $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq $functionName
    },
    $true
  )
  if ($null -eq $functionAst) {
    throw "Release function '$functionName' was not found."
  }
  Invoke-Expression $functionAst.Extent.Text
}
$fencedCommandFixture = New-FencedRemoteCommand `
  -Command "printf 'ok'`r`n" `
  -OwnerToken 'shamrai-public-release.A1b2C3' `
  -InitializeOwner
if ($fencedCommandFixture.Contains("`r")) {
  throw 'Fenced remote command contains Windows carriage returns.'
}

$jsonPropertyCountFixture = '{"backend":"success","frontend":"success"}' | ConvertFrom-Json
if (@($jsonPropertyCountFixture.PSObject.Properties).Count -ne 2) {
  throw 'JSON property enumeration is not stable in this PowerShell runtime.'
}

$secretTestName = 'SHAMRAI_RELEASE_TEST_TOKEN'
$viteTestName = 'VITE_UNDECLARED_RELEASE_TEST'
$safeTestName = 'SAFE_RELEASE_TEST_VALUE'
try {
  Set-Item -LiteralPath "Env:$secretTestName" -Value 'fake-secret'
  Set-Item -LiteralPath "Env:$viteTestName" -Value 'must-not-be-bundled'
  Set-Item -LiteralPath "Env:$safeTestName" -Value 'safe-value'
  $script:sanitizedSecret = 'not-run'
  $script:sanitizedVite = 'not-run'
  $script:sanitizedSafe = 'not-run'
  Invoke-WithSanitizedReleaseEnvironment {
    $script:sanitizedSecret = [Environment]::GetEnvironmentVariable($secretTestName)
    $script:sanitizedVite = [Environment]::GetEnvironmentVariable($viteTestName)
    $script:sanitizedSafe = [Environment]::GetEnvironmentVariable($safeTestName)
    Set-Item -LiteralPath 'Env:VITE_RELEASE_TEST_CREATED_INSIDE' -Value 'transient'
  }
  if ($null -ne $script:sanitizedSecret -or $null -ne $script:sanitizedVite) {
    throw 'Release environment sanitizer exposed a secret or undeclared VITE value.'
  }
  if ($script:sanitizedSafe -ne 'safe-value') {
    throw 'Release environment sanitizer removed an unrelated safe value.'
  }
  if ([Environment]::GetEnvironmentVariable($secretTestName) -ne 'fake-secret' -or
      [Environment]::GetEnvironmentVariable($viteTestName) -ne 'must-not-be-bundled') {
    throw 'Release environment sanitizer did not restore the caller environment.'
  }
  if ($null -ne [Environment]::GetEnvironmentVariable('VITE_RELEASE_TEST_CREATED_INSIDE')) {
    throw 'Release environment sanitizer left a transient VITE value behind.'
  }
} finally {
  Remove-Item -LiteralPath "Env:$secretTestName" -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath "Env:$viteTestName" -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath "Env:$safeTestName" -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath 'Env:VITE_RELEASE_TEST_CREATED_INSIDE' -ErrorAction SilentlyContinue
}
Assert-Contains $deploySource 'status --porcelain=v1 --untracked-files=normal'
Assert-Contains $deploySource 'Deployment requires a clean worktree.'
Assert-Contains $deploySource '[switch]$LocalEncryptedBackupGate'
Assert-Contains $deploySource 'LocalEncryptedBackupGate requires PowerShell 7 or newer'
Assert-Contains $deploySource 'C:\Program Files\Git\usr\bin\ssh-keyscan.exe'
Assert-Contains $deploySource 'Assert-CiReleaseGateAttestation'
Assert-Contains $deploySource 'refs/heads/main'
Assert-Contains $deploySource '$canonicalRemotePath = "/opt/shamrai-mini-app"'
Assert-Contains $deploySource '$canonicalComposeProject = "shamrai"'
Assert-Contains $deploySource '$canonicalPublicWebRoot = "/var/www/shamrai_web/dist"'
Assert-Contains $deploySource 'only accepts the canonical server, app path, Compose project, and public web root'
Assert-Contains $deploySource '[IO.FileShare]::None'
Assert-Contains $deploySource 'Another local Shamrai release process is already using this workspace.'
Assert-Contains $deploySource 'SkipLocalChecks is disabled for reproducible releases'
Assert-Contains $deploySource 'DRY_RUN deploy-public-shamrai-web'
Assert-Contains $deploySource '"archive"'
Assert-Contains $deploySource '"--format=tar.gz"'
Assert-Contains $deploySource '"release-manifest.json"'
Assert-Contains $deploySource '"SHAMRAI_GIT_SHA"'
Assert-Contains $deploySource '"SHAMRAI_BUILD_TIME"'
Assert-Contains $deploySource 'http://127.0.0.1:8082/api/ready'
Assert-Contains $deploySource 'https://shamra1.pro/api/ready'
Assert-Contains $deploySource 'https://shamra1.pro/api/version'
Assert-Contains $deploySource '.deploy\s3-retention-attestation.json'
Assert-Contains $deploySource '.deploy\shamrai-restore-attestation.json'
Assert-Contains $deploySource '.deploy\ci-release-attestation.json'
Assert-Contains $deploySource '.deploy\hidden-flat-e2e-attestation.json'
Assert-Contains $deploySource '[switch]$PreviewOnly'
Assert-Contains $deploySource 'Get-AuthenticatedGitHubArtifactJson'
Assert-Contains $deploySource 'GITHUB_PERSONAL_ACCESS_TOKEN'
Assert-Contains $deploySource 'Invoke-WebRequest'
Assert-Contains $deploySource 'CI release attestation must come from a protected branch.'
Assert-Contains $deploySource '@($jobResults.PSObject.Properties).Count'
Assert-NotContains $deploySource '$jobResults.PSObject.Properties.Count'
Assert-Contains $deploySource '$remoteStage = Invoke-Step "Create root-private remote release stage"'
Assert-Contains $deploySource 'return $stageCandidates[0]'
Assert-NotContains $deploySource '$remoteStage = $stageCandidates[0]'
Assert-Contains $deploySource '& $hiddenVerifier'
Assert-Contains $deploySource '[Guid]::TryParse([string](Get-RequiredJsonProperty $hiddenFlat "flat_subscription_id")'
Assert-Contains $deploySource 'Restore drill backend SHA does not match the exact release SHA.'
Assert-Contains $deploySource 'mktemp -d /tmp/shamrai-public-release.XXXXXX'
Assert-Contains $deploySource 'StrictHostKeyChecking=yes'
Assert-Contains $deploySource 'create-reproducible-tar.py'
Assert-Contains $deploySource 'Prepare isolated release source'
Assert-Contains $deploySource 'release-source-'
Assert-Contains $deploySource '"archive"'
Assert-Contains $deploySource '$frontendBuildPath'
Assert-Contains $deploySource 'Tracked Vite environment files are forbidden in a release source'
Assert-NotContains $deploySource 'Push-Location (Join-Path $Workspace "frontend")'
Assert-Contains $deploySource 'Invoke-NativeChecked "npm" "ci"'
Assert-Contains $deploySource 'Invoke-WithSanitizedReleaseEnvironment'
Assert-Contains $deploySource '$sensitivePrefixPattern'
$paymentDrain = $deploySource.IndexOf('docker compose -p "$ComposeProject" stop backend', [StringComparison]::Ordinal)
$migrationUpgrade = $deploySource.IndexOf('docker compose -p "$ComposeProject" run --rm --no-deps backend alembic upgrade head', [StringComparison]::Ordinal)
if ($paymentDrain -lt 0 -or $migrationUpgrade -lt 0 -or $paymentDrain -gt $migrationUpgrade) {
  throw 'The old backend must be stopped before checkout snapshot migrations run.'
}
$protectedFingerprintBefore = $deploySource.IndexOf('pre_migration_protected_fingerprints="`$(protected_data_fingerprints)"', [StringComparison]::Ordinal)
$protectedFingerprintAfter = $deploySource.IndexOf('post_migration_protected_fingerprints="`$(protected_data_fingerprints)"', [StringComparison]::Ordinal)
$protectedFingerprintComparison = $deploySource.IndexOf('Protected production data changed during migrations.', [StringComparison]::Ordinal)
$postMigrationInvariants = $deploySource.IndexOf('Post-migration data invariants failed.', [StringComparison]::Ordinal)
$startApplication = $deploySource.IndexOf('$startApplicationBlock', $postMigrationInvariants, [StringComparison]::Ordinal)
if (
  $protectedFingerprintBefore -lt $paymentDrain -or
  $protectedFingerprintBefore -gt $migrationUpgrade -or
  $protectedFingerprintAfter -lt $migrationUpgrade -or
  $protectedFingerprintComparison -lt $protectedFingerprintAfter -or
  $postMigrationInvariants -lt $protectedFingerprintComparison -or
  $startApplication -lt $postMigrationInvariants
) {
  throw 'Protected data fingerprints and migration invariants must pass while the backend is stopped.'
}
$schemaUpgradeMarker = $deploySource.IndexOf('install -m 0600 /dev/null "`$schema_upgrade_marker"', [StringComparison]::Ordinal)
$schemaUpgradeCommitted = $deploySource.IndexOf('schema_upgrade_committed=1', [StringComparison]::Ordinal)
if (
  $schemaUpgradeMarker -lt 0 -or
  $schemaUpgradeCommitted -lt $migrationUpgrade -or
  $schemaUpgradeCommitted -gt $schemaUpgradeMarker
) {
  throw 'The irreversible release-stage marker must be written only after Alembic succeeds.'
}
Assert-Contains $deploySource 'schema_upgrade_marker="$remoteStage/schema-upgrade.completed"'
Assert-Contains $deploySource 'if [ "`$schema_upgrade_committed" = "1" ] || [ -f "`$schema_upgrade_marker" ]; then'
Assert-Contains $deploySource 'protected_data_fingerprints()'
Assert-Contains $deploySource 'to_jsonb(t) - ARRAY['
Assert-Contains $deploySource "'checkout_intent_id', 'checkout_payload_hash', 'checkout_state', 'checkout_url'"
Assert-Contains $deploySource "SELECT 'users', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))"
Assert-Contains $deploySource "SELECT 'bets', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))"
Assert-Contains $deploySource "SELECT 'user_bets', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))"
Assert-Contains $deploySource 'NOT EXISTS (SELECT 1 FROM subscription_plan_checkout_allowlist)'
Assert-Contains $deploySource 'NOT EXISTS (SELECT 1 FROM flat_subscriptions WHERE revision IS DISTINCT FROM 1)'
Assert-Contains $deploySource '[ "`$migration_revision" = ''20260802_0043'' ]'
Assert-Contains $deploySource 'rm -f "`$backup_marker"'
Assert-Contains $deploySource 'Schema upgrade already committed; refusing pre-migration code rollback.'
Assert-Contains $deploySource 'Post-migration rollback retained the new code and database; forward repair is required.'
Assert-Contains $deploySource 'post_migration_fail_closed=1'
Assert-Contains $deploySource 'if [ "`$post_migration_fail_closed" = "1" ]; then'
Assert-Contains $deploySource 'post_migration_fail_closed'
$deployRollbackGuard = $deploySource.IndexOf('if [ "`$schema_upgrade_committed" = "1" ] || [ -f "`$schema_upgrade_marker" ]; then', [StringComparison]::Ordinal)
$normalRollbackMessage = $deploySource.IndexOf('Preview deploy failed; rolling back code snapshot.', $deployRollbackGuard, [StringComparison]::Ordinal)
$deployFailClosedStop = $deploySource.IndexOf('docker compose -p "$ComposeProject" stop backend', $deployRollbackGuard, [StringComparison]::Ordinal)
$deployCodeRestore = $deploySource.IndexOf('mv "`$backup_path" "$RemotePath"', $deployRollbackGuard, [StringComparison]::Ordinal)
if (
  $deployRollbackGuard -lt 0 -or
  $deployFailClosedStop -lt $deployRollbackGuard -or
  $deployFailClosedStop -gt $normalRollbackMessage -or
  $deployCodeRestore -lt $normalRollbackMessage
) {
  throw 'Post-migration deploy rollback must stop the backend before the pre-migration restore branch.'
}
$manualRollbackGuard = $deploySource.IndexOf('if [ -f "`$schema_upgrade_marker" ]; then', [StringComparison]::Ordinal)
$manualFailClosedStop = $deploySource.IndexOf('docker compose -p "$ComposeProject" stop backend', $manualRollbackGuard, [StringComparison]::Ordinal)
$manualCodeFallback = $deploySource.IndexOf('elif [ -f "`$code_backup_marker" ]; then', $manualRollbackGuard, [StringComparison]::Ordinal)
$manualCodeRestore = $deploySource.IndexOf('mv "`$code_backup" "$RemotePath"', $manualRollbackGuard, [StringComparison]::Ordinal)
$manualWebRollback = $deploySource.IndexOf('if [ -f "`$web_backup_marker" ]; then', $manualRollbackGuard, [StringComparison]::Ordinal)
$manualNginxRollback = $deploySource.IndexOf('if [ -f "`$nginx_backup_marker" ]; then', $manualWebRollback, [StringComparison]::Ordinal)
if (
  $manualRollbackGuard -lt 0 -or
  $manualFailClosedStop -lt $manualRollbackGuard -or
  $manualFailClosedStop -gt $manualCodeFallback -or
  $manualCodeRestore -lt $manualCodeFallback -or
  $manualWebRollback -lt $manualCodeRestore -or
  $manualNginxRollback -lt $manualWebRollback
) {
  throw 'Manual rollback must fail closed after migration and reach web/nginx rollback without restoring old code.'
}
Assert-Contains $deploySource 'Invoke-NativeChecked "npm" "ci" "--ignore-scripts"'
Assert-NotContains $deploySource '--ignore-scripts=false'
Assert-Contains $deploySource 'move_state_directories "`$backup_path" "$RemotePath"'
Assert-Contains $deploySource 'flock -w 300 8'
Assert-Contains $deploySource 'New-FencedRemoteCommand'
Assert-Contains $deploySource 'shamrai-public-deploy.owner'
Assert-Contains $deploySource 'refusing stale lifecycle action'
Assert-Contains $deploySource 'Prune exact snapshots after committed release'
Assert-Contains $deploySource 'Release is committed and healthy, but exact rollback snapshot cleanup failed.'
Assert-Contains $deploySource 'backup_schedule_state_ok'
Assert-Contains $deploySource 'systemctl is-enabled --quiet shamrai-db-backup.timer'
Assert-Contains $deploySource 'shamrai-backup-with-systemd-credentials'
Assert-Contains $deploySource 'LoadCredentialEncrypted=aws_access_key_id:'
Assert-Contains $deploySource 'latest.manifest.sig:daily/shamrai-db.*.manifest.sig'
Assert-Contains $deploySource '/etc/credstore.encrypted/shamrai-backup-manifest-signing-private-key.cred'
Assert-Contains $deploySource 'LoadCredentialEncrypted=manifest_signing_private_key:'
Assert-Contains $deploySource 'Get-RequiredJsonProperty $restore "signature_verified"'
Assert-Contains $deploySource 'Get-RequiredJsonProperty $restore "signature_sha256"'
Assert-Contains $deploySource 'Get-RequiredJsonProperty $restore "signing_public_key_sha256"'
Assert-Contains $deploySource 'systemd-creds decrypt --name=manifest_signing_private_key'
Assert-Contains $deploySource 'configured_manifest_signing_public_key_sha256'
Assert-Contains $deploySource 'derived_manifest_signing_public_key_sha256'
Assert-Contains $deploySource 'test "`$SHAMRAI_BACKUP_S3_BUCKET" = $attestedS3Bucket'
Assert-Contains $deploySource 'test "`$SHAMRAI_BACKUP_S3_PREFIX" = $attestedS3Prefix'
Assert-Contains $deploySource 'test "`$SHAMRAI_BACKUP_S3_ENDPOINT_URL" = $attestedS3EndpointUrl'
Assert-Contains $deploySource 'test "`$SHAMRAI_BACKUP_S3_REGION" = $attestedS3Region'
Assert-Contains $deploySource 'systemd-creds decrypt --name=aws_access_key_id'
Assert-Contains $deploySource 'schedule_restore_allowed=0'
Assert-Contains $deploySource 'backup timer left stopped because rollback state is incomplete'
Assert-Contains $deploySource 'Quiesce backend and create the final encrypted database backup'
Assert-Contains $deploySource 'Final backup did not create a new encrypted artifact.'
Assert-Contains $deploySource 'Restore-drill the quiesced final backup before migration'
Assert-Contains $deploySource 'scripts\run-shamrai-restore-drill.ps1'
Assert-Contains $deploySource 'OffHostArtifactKey = $finalBackupArtifactKey'
Assert-Contains $deploySource 'Final restore attestation is not bound to the quiesced backup artifact.'
Assert-Contains $deploySource 'Prebuild exact candidate images before backend downtime'
Assert-Contains $deploySource 'Create and restore-drill the final local encrypted production backup'
Assert-Contains $deploySource 'scripts\verify-shamrai-release-backup.ps1'
Assert-Contains $deploySource '-LeaveBackendStopped'
Assert-Contains $deploySource 'prebuilt_candidate_images_match_running_containers'
Assert-Contains $deploySource 'docker image tag "`$prebuilt_backend_image" shamrai-backend'
Assert-Contains $deploySource 'Migration backend image differs from the qualified candidate.'
Assert-Contains $deploySource 'up -d --no-deps --force-recreate --no-build backend frontend'
Assert-Contains $deploySource 'local_encrypted_backup_restore_gate_ok'
$localCandidatePreparationMatch = [regex]::Match(
  $deploySource,
  '(?s)if \(\$LocalEncryptedBackupGate\) \{\s+\$candidateImagePreparationBlock = @"(?<body>.*?)"@'
)
if (-not $localCandidatePreparationMatch.Success) {
  throw 'Local exact-candidate preparation block was not found.'
}
$localCandidatePreparationBody = $localCandidatePreparationMatch.Groups['body'].Value
Assert-Contains $localCandidatePreparationBody 'docker image tag "`$prebuilt_backend_image" shamrai-backend'
Assert-NotContains $localCandidatePreparationBody 'docker compose -p "$ComposeProject" build'
Assert-Contains $deploySource 'Verify preview APIs before any public frontend publish'
Assert-Contains $deploySource 'Publish public frontend after successful preview verification'
Assert-NotContains $deploySource '$deployLifecycle += " && bash $(ConvertTo-ShellSingleQuoted "$remoteStage/publish.sh")"'

$quiescedBackupStep = $deploySource.IndexOf('Quiesce backend and create the final encrypted database backup', [StringComparison]::Ordinal)
$quiescedBackendStop = $deploySource.IndexOf('docker compose -p ''$ComposeProject'' stop backend', $quiescedBackupStep, [StringComparison]::Ordinal)
$finalBackupStart = $deploySource.IndexOf('systemctl start shamrai-db-backup.service', $quiescedBackendStop, [StringComparison]::Ordinal)
$finalRestoreDrill = $deploySource.IndexOf('Restore-drill the quiesced final backup before migration', $finalBackupStart, [StringComparison]::Ordinal)
$deployAfterRestore = $deploySource.IndexOf('Deploy exact code to the canonical preview under the release fence', $finalRestoreDrill, [StringComparison]::Ordinal)
$previewVerification = $deploySource.IndexOf('Verify preview APIs before any public frontend publish', $deployAfterRestore, [StringComparison]::Ordinal)
$publicPublish = $deploySource.IndexOf('Publish public frontend after successful preview verification', $previewVerification, [StringComparison]::Ordinal)
if (
  $quiescedBackupStep -lt 0 -or
  $quiescedBackendStop -lt $quiescedBackupStep -or
  $finalBackupStart -lt $quiescedBackendStop -or
  $finalRestoreDrill -lt $finalBackupStart -or
  $deployAfterRestore -lt $finalRestoreDrill -or
  $previewVerification -lt $deployAfterRestore -or
  $publicPublish -lt $previewVerification
) {
  throw 'Stage 2 ordering must be quiesce -> final backup -> restore drill -> migration -> preview verification -> public publish.'
}
$localPrebuild = $deploySource.IndexOf('Prebuild exact candidate images before backend downtime', [StringComparison]::Ordinal)
$localBackup = $deploySource.IndexOf('Create and restore-drill the final local encrypted production backup', $localPrebuild, [StringComparison]::Ordinal)
$localDeploy = $deploySource.IndexOf('Deploy exact code to the canonical preview under the release fence', $localBackup, [StringComparison]::Ordinal)
$localPreview = $deploySource.IndexOf('Verify preview APIs before any public frontend publish', $localDeploy, [StringComparison]::Ordinal)
if ($localPrebuild -lt 0 -or $localBackup -lt $localPrebuild -or $localDeploy -lt $localBackup -or $localPreview -lt $localDeploy) {
  throw 'Local Stage 2 ordering must be prebuild -> encrypted backup/restore -> migration -> preview verification.'
}
Assert-Contains $deploySource 'find "$RemotePath" \( -path "$RemotePath/ops" -o -path "$RemotePath/db-backups" \) -prune'
Assert-NotContains $deploySource ' -pw $password '
Assert-NotContains $deploySource 'StrictHostKeyChecking=accept-new'
Assert-NotContains $deploySource 'find "$RemotePath" -type f -exec chmod 0644'
Assert-NotContains $deploySource '"-czf" $repoArchive "."'
Assert-NotContains $deploySource 'mv "$RemotePath" "`$failed_path" || true'
Assert-NotContains $deploySource 'mv "$PublicWebRoot" "`$web_failed" || true'
Assert-NotContains $deploySource 'move_state_directories "`$failed_path" "$RemotePath" || true'

$releaseBackupSource = Get-Content -Raw -LiteralPath $releaseBackupPath
Assert-Contains $releaseBackupSource 'PowerShell 7 or newer is required for binary-safe encrypted backup streaming.'
Assert-Contains $releaseBackupSource 'docker compose -p $projectShell stop backend'
Assert-Contains $releaseBackupSource 'pg_dump -U shamrai -d shamrai -Fc'
Assert-Contains $releaseBackupSource '-SinkFile $age'
Assert-Contains $releaseBackupSource '-SourceFile $age'
Assert-Contains $releaseBackupSource 'isolated_postgres_restore=true'
Assert-Contains $releaseBackupSource 'plaintext_dump_persisted = $false'
Assert-Contains $releaseBackupSource 'Restored backup row count differs for $table.'
Assert-Contains $releaseBackupSource 'normalized_schema_hash'
Assert-Contains $releaseBackupSource 'canonical_schema()'
Assert-NotContains $releaseBackupSource 'CHECK <expression>'
Assert-NotContains $releaseBackupSource 'WHERE <predicate>'
Assert-Contains $releaseBackupSource 'up -d --no-deps backend'
Assert-Contains $releaseBackupSource 'Binary pipeline exceeded the ${TimeoutSeconds}-second timeout.'
Assert-Contains $releaseBackupSource '$source.Kill($true)'
Assert-Contains $releaseBackupSource '$sink.Kill($true)'
Assert-Contains $releaseBackupSource 'ConnectTimeout=15'
Assert-Contains $releaseBackupSource 'ServerAliveInterval=15'
Assert-Contains $releaseBackupSource 'ServerAliveCountMax=4'
Assert-Contains $releaseBackupSource 'throw "Backend restart after release backup verification failed:'
foreach ($protectedTable in @('subscription_plans', 'flat_subscription_credits', 'quizzes')) {
  $expectedProtectedTableLine = '  "' + $protectedTable + '",'
  Assert-Contains $releaseBackupSource $expectedProtectedTableLine
}
Assert-NotContains $releaseBackupSource 'pg_dump -f'
Assert-NotContains $releaseBackupSource 'SHAMRAI_SSH_PASSWORD'

foreach ($workflowPath in @($ciPath, $restorePath)) {
  $workflowSource = Get-Content -Raw -LiteralPath $workflowPath
  $usesMatches = [regex]::Matches($workflowSource, '(?m)^\s*uses:\s*([^\s#]+)')
  foreach ($usesMatch in $usesMatches) {
    $usesValue = $usesMatch.Groups[1].Value
    if ($usesValue -notmatch '^[^@\s]+@[0-9a-f]{40}$') {
      throw "Workflow action is not pinned to a full commit SHA in ${workflowPath}: $usesValue"
    }
  }
}

$ciSource = Get-Content -Raw -LiteralPath $ciPath
Assert-Contains $ciSource 'gitleaks git --redact --no-banner --exit-code 1 --config .gitleaks.toml'
Assert-Contains $ciSource 'gitleaks dir --redact --no-banner --exit-code 1 --config .gitleaks.toml .'
Assert-Contains $ciSource 'pip-audit -r requirements.lock --no-deps --disable-pip --strict'
Assert-Contains $ciSource 'pip-audit -r requirements-dev.lock --no-deps --disable-pip --strict'
Assert-Contains $ciSource 'verify-python-lock.py requirements.txt requirements.lock'
Assert-Contains $ciSource 'verify-python-lock.py requirements-dev.txt requirements-dev.lock'
Assert-Contains $ciSource 'pip install --require-hashes -r requirements-dev.lock'
Assert-NotContains $ciSource 'pip install -r requirements-dev.txt'
Assert-NotContains $ciSource 'pip install pytest==8.4.2 pytest-cov==7.0.0'
Assert-Contains $ciSource 'npm audit --audit-level=moderate'
Assert-Contains $ciSource 'postgres-concurrency:'
Assert-Contains $ciSource 'SHAMRAI_RUN_POSTGRES_CONCURRENCY_TESTS: "1"'
Assert-Contains $ciSource 'tests/test_flat_postgres_concurrency.py'
Assert-Contains $ciSource 'flat-coverage:'
Assert-Contains $ciSource '--cov=src.services.flat_subscriptions'
Assert-Contains $ciSource '--cov=src.services.subscription_pricing'
Assert-Contains $ciSource '--cov-fail-under=80'
Assert-Contains $ciSource 'release-attestation:'
Assert-Contains $ciSource 'RELEASE_SHA: ${{ github.sha }}'
Assert-Contains $ciSource 'REF_PROTECTED: ${{ github.ref_protected }}'
Assert-Contains $ciSource '"status": "passed"'
Assert-Contains $ciSource 'ci-release-attestation-${{ github.sha }}'
Assert-Contains $ciSource './scripts/tests/test-hidden-flat-e2e-gate.ps1'
foreach ($requiredJob in @('backend', 'flat-coverage', 'frontend', 'secret-scan', 'docker-build', 'alembic-smoke', 'postgres-concurrency', 'compose', 'powershell')) {
  Assert-Contains $ciSource "      - $requiredJob"
}
Assert-NotContains $ciSource 'dependency-review-action'

$hiddenGateSource = Get-Content -Raw -LiteralPath $hiddenGatePath
Assert-Contains $hiddenGateSource 'SHAMRAI_HIDDEN_GATE_JWT'
Assert-Contains $hiddenGateSource 'AuthenticationHeaderValue]::new("Bearer", $Jwt)'
Assert-Contains $hiddenGateSource '$handler.AllowAutoRedirect = $false'
Assert-Contains $hiddenGateSource '/api/payments/attempts/'
Assert-Contains $hiddenGateSource '/api/subscriptions/flats/current'
Assert-Contains $hiddenGateSource '.deploy\hidden-flat-e2e-attestation.json'
Assert-Contains $hiddenGateSource 'Remove-StaleAttestation'
Assert-Contains $hiddenGateSource '[datetimeoffset]$MinimumEvidenceTimeUtc'
Assert-NotContains $hiddenGateSource 'Write-Host $jwt'
Assert-NotContains $hiddenGateSource 'Write-Output $jwt'

$hiddenGateModuleSource = Get-Content -Raw -LiteralPath $hiddenGateModulePath
Assert-Contains $hiddenGateModuleSource 'payment_status") -Expected "succeeded"'
Assert-Contains $hiddenGateModuleSource 'is_hidden'
Assert-Contains $hiddenGateModuleSource 'subscription_purchase'
Assert-Contains $hiddenGateModuleSource 'settled_bet_count'
Assert-Contains $hiddenGateModuleSource 'minimum_evidence_time'
Assert-Contains $hiddenGateModuleSource 'latest_settled_at'

$restoreSource = Get-Content -Raw -LiteralPath $restorePath
Assert-Contains $restoreSource "SHAMRAI_BACKUP_S3_REGION: `${{ vars.SHAMRAI_BACKUP_S3_REGION }}"
Assert-Contains $restoreSource "SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64: `${{ vars.SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64 }}"
Assert-Contains $restoreSource "github.event_name == 'schedule' && 's3'"
Assert-Contains $restoreSource 'SSH_PRIVATE_KEY: ${{ secrets.SHAMRAI_SSH_PRIVATE_KEY }}'
Assert-Contains $restoreSource 'AGE_IDENTITY: ${{ secrets.SHAMRAI_BACKUP_AGE_IDENTITY }}'
Assert-Contains $restoreSource 'Missing SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64 variable'
Assert-Contains $restoreSource 'sudo apt-get install -y --no-install-recommends age openssl'
Assert-Contains $restoreSource 'printf ''%s\n'' "$SSH_PRIVATE_KEY"'
Assert-Contains $restoreSource 'printf ''%s\n'' "$AGE_IDENTITY"'
Assert-NotContains $restoreSource 'printf ''%s\n'' "${{ secrets.SHAMRAI_SSH_PRIVATE_KEY }}"'
Assert-NotContains $restoreSource 'printf ''%s\n'' "${{ secrets.SHAMRAI_BACKUP_AGE_IDENTITY }}"'
Assert-Contains $restoreSource 'path: .deploy/s3-retention-attestation.json'
Assert-Contains $restoreSource 'path: .deploy/shamrai-restore-attestation.json'
Assert-Contains $restoreSource 'environment: shamrai-production-backup'
Assert-Contains $restoreSource "github.ref == format('refs/heads/{0}', github.event.repository.default_branch)"

Write-Host "release_ops_source_tests_ok"
