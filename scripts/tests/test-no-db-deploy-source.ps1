$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$deployPath = Join-Path $repoRoot "scripts\deploy-shamrai-backend-no-db.ps1"

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

if (-not (Test-Path -LiteralPath $deployPath -PathType Leaf)) {
  throw "No-DB deploy script was not found: $deployPath"
}

$tokens = $null
$parseErrors = $null
[System.Management.Automation.Language.Parser]::ParseFile(
  $deployPath,
  [ref]$tokens,
  [ref]$parseErrors
) | Out-Null
if ($parseErrors.Count -ne 0) {
  throw "No-DB deploy script has PowerShell parse errors: $($parseErrors.Message -join '; ')"
}

$source = Get-Content -Raw -LiteralPath $deployPath
$allowedOverlayFiles = @(
  "backend/src/api/admin_broadcast.py",
  "backend/src/api/telegram_webhook.py",
  "backend/src/api/vk_callback.py",
  "backend/src/services/delivery_outbox.py",
  "backend/src/services/forecast_delivery.py",
  "backend/src/services/match_access.py",
  "backend/src/services/stats_export.py",
  "backend/src/services/telegram_bot.py"
)

foreach ($path in $allowedOverlayFiles) {
  Assert-Contains -Actual $source -Expected ('"' + $path + '"')
}

foreach ($forbiddenPath in @(
  "backend/src/models/models.py",
  "backend/src/schemas/schemas.py",
  "backend/src/api/payments.py",
  "backend/src/api/subscriptions.py",
  "backend/src/services/flat_subscriptions.py",
  "backend/src/services/subscription_pricing.py",
  "backend/alembic/versions/20260802_0042_payment_checkout_safety.py",
  "backend/alembic/versions/20260802_0043_hidden_flat_plan_gate.py"
)) {
  Assert-Contains -Actual $source -Expected ('"' + $forbiddenPath + '"')
}

Assert-Contains $source '[string]$ExpectedAlembicRevision = "20260802_0041"'
Assert-Contains $source '[switch]$DryRun'
Assert-Contains $source 'database_mode=none'
Assert-Contains $source 'approved_overlay_file_count=8'
Assert-Contains $source 'overlay_file_count=3'
Assert-Contains $source 'deferred_overlay_file_count=5'
Assert-Contains $source '$expectedList = ($OverlayFiles | Sort-Object) -join "`n"'
Assert-NotContains $source '$expectedList = ($OverlayFiles | Sort-Object) -join "\n"'
Assert-Contains $source 'pg_dump'
Assert-Contains $source 'age-keygen'
Assert-Contains $source 'Invoke-BinaryPipeline'
Assert-Contains $source 'pg_restore'
Assert-Contains $source 'shamrai-no-db-restore-'
Assert-Contains $source 'ENABLE_BACKGROUND_TASKS: "false"'
Assert-Contains $source '/app/static:uid=10001,gid=10001,mode=0755'
Assert-Contains $source 'ps -q --all backend'
Assert-Contains $source 'Candidate backend stopped before becoming healthy.'
Assert-Contains $source 'Candidate backend health check timed out.'
Assert-Contains $source 'docker run --rm --entrypoint python "$candidate_image" -m compileall -q src'
Assert-Contains $source 'alembic current'
Assert-NotContains $source 'http://127.0.0.1:8000/api/ready'
Assert-Contains $source '--schema-only'
Assert-Contains $source "sed -E '/^\\(un)?restrict /d'"
Assert-Contains $source 'STRUCTURE_HASH'
Assert-Contains $source 'CHECK <expression>'
Assert-Contains $source 'WHERE <predicate>;'
Assert-Contains $source '[string]$ExpectedStructureHash'
Assert-Contains $source '--no-deps'
Assert-Contains $source '--force-recreate'
Assert-Contains $source 'schema hash changed during a no-DB deployment'
Assert-Contains $source 'Postgres container changed during a no-DB deployment'
Assert-Contains $source 'Redis container changed during a no-DB deployment'
Assert-Contains $source 'Invoke-NoDbRollback'
Assert-Contains $source 'backend-image-id'
Assert-Contains $source 'backend-image-ref'
Assert-Contains $source 'docker image tag "$previous_image" "$rollback_image"'
Assert-Contains $source 'docker image tag "$candidate_id" "$previous_image_ref"'
Assert-Contains $source 'Production backend did not start from the qualified candidate image.'
Assert-Contains $source '--no-build backend'
Assert-Contains $source '$script:remoteStage = New-RemoteStage'
Assert-Contains $source '$script:cutoverStarted = $true'
Assert-Contains $source 'if ($script:cutoverStarted)'
Assert-Contains $source 'StrictHostKeyChecking=yes'
Assert-Contains $source 'UserKnownHostsFile='
Assert-Contains $source 'Set-OwnerOnlyLocalPath'
Assert-Contains $source 'backend/.env'
Assert-Contains $source 'find "$stage/source/backend" -type f \( -name ''.env'' -o -name ''.env.*'' -o -name ''*.pem'' \) -delete'
Assert-Contains $source 'chmod 0700'
Assert-Contains $source 'chmod 0600'

Assert-NotContains $source 'alembic upgrade'
Assert-NotContains $source 'docker compose -p "$ComposeProject" up -d postgres redis'
Assert-NotContains $source 'docker-compose.yml"'
Assert-NotContains $source 'HTTPS_PROXY='
Assert-NotContains $source 'SHAMRAI_SSH_PASSWORD'
Assert-NotContains $source 'StrictHostKeyChecking=accept-new'
Assert-NotContains $source '$compose exec -T backend python -m compileall'
Assert-NotContains $source 'docker compose -p __COMPOSE_PROJECT__ build backend'

$dryRunOutput = & $deployPath -Workspace $repoRoot -DryRun 6>&1 | Out-String
Assert-Contains $dryRunOutput 'DRY_RUN deploy-shamrai-backend-no-db'
Assert-Contains $dryRunOutput 'database_mode=none'
Assert-Contains $dryRunOutput 'expected_alembic_revision=20260802_0041'
Assert-Contains $dryRunOutput 'approved_overlay_file_count=8'
Assert-Contains $dryRunOutput 'overlay_file_count=3'
Assert-Contains $dryRunOutput 'deferred_overlay_file_count=5'

Write-Host "no_db_deploy_source_tests_ok"
