param(
  [string]$Workspace = "C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)",
  [string]$Server = "root@82.147.67.245",
  [int]$SshPort = 22,
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [string]$ExpectedAlembicRevision = "20260802_0041",
  [string]$HealthUrl = "http://127.0.0.1:8082/api/health",
  [string]$SshKeyPath = $env:SHAMRAI_SSH_KEY_PATH,
  [string]$KnownHostsPath = "",
  [string]$AgeIdentityPath = $env:SHAMRAI_BACKUP_AGE_IDENTITY_PATH,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ApprovedOverlayFiles = @(
  "backend/src/api/admin_broadcast.py",
  "backend/src/api/telegram_webhook.py",
  "backend/src/api/vk_callback.py",
  "backend/src/services/delivery_outbox.py",
  "backend/src/services/forecast_delivery.py",
  "backend/src/services/match_access.py",
  "backend/src/services/stats_export.py",
  "backend/src/services/telegram_bot.py"
)

$OverlayFiles = @(
  "backend/src/services/delivery_outbox.py",
  "backend/src/services/stats_export.py",
  "backend/src/services/telegram_bot.py"
)

$DeferredOverlayFiles = @(
  "backend/src/api/admin_broadcast.py",
  "backend/src/api/telegram_webhook.py",
  "backend/src/api/vk_callback.py",
  "backend/src/services/forecast_delivery.py",
  "backend/src/services/match_access.py"
)

$ForbiddenOverlayFiles = @(
  "backend/src/models/models.py",
  "backend/src/schemas/schemas.py",
  "backend/src/api/payments.py",
  "backend/src/api/subscriptions.py",
  "backend/src/services/flat_subscriptions.py",
  "backend/src/services/subscription_pricing.py",
  "backend/alembic/versions/20260802_0042_payment_checkout_safety.py",
  "backend/alembic/versions/20260802_0043_hidden_flat_plan_gate.py"
)

$ProtectedTables = @(
  "users",
  "bets",
  "user_bets",
  "payment_attempts",
  "subscriptions",
  "flat_subscriptions",
  "historical_stats_import_batches",
  "historical_stats_monthly",
  "historical_stats_breakdowns",
  "historical_stats_details"
)

if ($DryRun) {
  Write-Host "DRY_RUN deploy-shamrai-backend-no-db"
  Write-Host "database_mode=none"
  Write-Host "expected_alembic_revision=$ExpectedAlembicRevision"
  Write-Host "approved_overlay_file_count=8"
  Write-Host "overlay_file_count=3"
  Write-Host "deferred_overlay_file_count=5"
  foreach ($path in $OverlayFiles) {
    Write-Host "overlay=$path"
  }
  foreach ($path in $DeferredOverlayFiles) {
    Write-Host "deferred_overlay=$path"
  }
  Write-Host "encrypted_backup_required=true"
  Write-Host "isolated_restore_required=true"
  Write-Host "production_services_changed=backend"
  return
}

function Get-HomePath {
  if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    return $env:USERPROFILE
  }
  throw "USERPROFILE is required to resolve local SSH and age paths."
}

function Find-Tool {
  param([string[]]$Candidates)
  foreach ($candidate in $Candidates) {
    if (Test-Path -LiteralPath $candidate) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command) {
      return $command.Source
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
  return ($output -join "`n").Trim()
}

function ConvertTo-ShellSingleQuoted {
  param([string]$Value)
  if ($null -eq $Value) { $Value = "" }
  return "'" + $Value.Replace("'", "'`"'`"'") + "'"
}

function Set-OwnerOnlyLocalPath {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][bool]$Directory
  )
  $icacls = Find-Tool @("icacls.exe", "icacls")
  if (-not $icacls) {
    throw "icacls is required to protect local backup material."
  }
  $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
  $grant = if ($Directory) { "${identity}:(OI)(CI)F" } else { "${identity}:F" }
  Invoke-NativeChecked -FilePath $icacls -Arguments @($Path, "/inheritance:r", "/grant:r", $grant)
}

function Start-NativeProcess {
  param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [string[]]$Arguments = @(),
    [bool]$RedirectInput = $false,
    [bool]$RedirectOutput = $false
  )
  $startInfo = [Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = $FilePath
  $startInfo.UseShellExecute = $false
  $startInfo.CreateNoWindow = $true
  $startInfo.RedirectStandardInput = $RedirectInput
  $startInfo.RedirectStandardOutput = $RedirectOutput
  $startInfo.RedirectStandardError = $true
  foreach ($argument in $Arguments) {
    $startInfo.ArgumentList.Add($argument)
  }
  $process = [Diagnostics.Process]::new()
  $process.StartInfo = $startInfo
  if (-not $process.Start()) {
    throw "Unable to start native process: $FilePath"
  }
  return $process
}

function Invoke-BinaryPipeline {
  param(
    [Parameter(Mandatory = $true)][string]$SourceFile,
    [string[]]$SourceArguments = @(),
    [Parameter(Mandatory = $true)][string]$SinkFile,
    [string[]]$SinkArguments = @()
  )
  $source = $null
  $sink = $null
  try {
    $sink = Start-NativeProcess -FilePath $SinkFile -Arguments $SinkArguments -RedirectInput $true
    $source = Start-NativeProcess -FilePath $SourceFile -Arguments $SourceArguments -RedirectOutput $true
    $sourceError = $source.StandardError.ReadToEndAsync()
    $sinkError = $sink.StandardError.ReadToEndAsync()
    $source.StandardOutput.BaseStream.CopyTo($sink.StandardInput.BaseStream)
    $sink.StandardInput.Close()
    $source.WaitForExit()
    $sink.WaitForExit()
    $sourceMessage = $sourceError.GetAwaiter().GetResult().Trim()
    $sinkMessage = $sinkError.GetAwaiter().GetResult().Trim()
    if ($source.ExitCode -ne 0 -or $sink.ExitCode -ne 0) {
      throw "Binary pipeline failed: source=$($source.ExitCode) sink=$($sink.ExitCode) source_error=$sourceMessage sink_error=$sinkMessage"
    }
  } finally {
    if ($source) { $source.Dispose() }
    if ($sink) { $sink.Dispose() }
  }
}

function ConvertFrom-KeyValueOutput {
  param([string]$Output)
  $result = @{}
  foreach ($line in ($Output -split "`r?`n")) {
    if ($line -match '^([A-Z0-9_]+)=(.*)$') {
      $result[$matches[1]] = $matches[2].Trim()
    }
  }
  return $result
}

function Get-RemoteBaseline {
  $tables = $ProtectedTables -join " "
  $script = @'
set -Eeuo pipefail
cd __REMOTE_PATH__
docker compose -p __COMPOSE_PROJECT__ config -q
backend_id="$(docker compose -p __COMPOSE_PROJECT__ ps -q backend)"
postgres_id="$(docker compose -p __COMPOSE_PROJECT__ ps -q postgres)"
redis_id="$(docker compose -p __COMPOSE_PROJECT__ ps -q redis)"
frontend_id="$(docker compose -p __COMPOSE_PROJECT__ ps -q frontend)"
for id in "$backend_id" "$postgres_id" "$redis_id" "$frontend_id"; do
  [ -n "$id" ] && [ "$(docker inspect -f '{{.State.Running}}' "$id")" = "true" ]
done
port_owner="$(docker ps --filter label=com.docker.compose.project=__COMPOSE_PROJECT_RAW__ --format '{{.Ports}}' | grep -F ':8082->' || true)"
[ -n "$port_owner" ] || { echo 'Canonical project does not own port 8082.' >&2; exit 31; }
revision="$(docker compose -p __COMPOSE_PROJECT__ exec -T backend alembic current 2>/dev/null | tail -n 1 | awk '{print $1}')"
normalized_schema() {
  docker compose -p __COMPOSE_PROJECT__ exec -T postgres pg_dump -U shamrai -d shamrai --schema-only --no-owner --no-privileges | sed -E '/^\\(un)?restrict /d'
}
schema_hash="$(normalized_schema | sha256sum | awk '{print $1}')"
structure_hash="$(normalized_schema | awk '
/^    CONSTRAINT .* CHECK / { sub(/ CHECK .*/, " CHECK <expression>") }
/^CREATE .* INDEX .* WHERE / { sub(/ WHERE .*/, " WHERE <predicate>;") }
{ print }
' | sha256sum | awk '{print $1}')"
printf 'BACKEND_ID=%s\n' "$backend_id"
printf 'POSTGRES_ID=%s\n' "$postgres_id"
printf 'REDIS_ID=%s\n' "$redis_id"
printf 'FRONTEND_ID=%s\n' "$frontend_id"
printf 'BACKEND_IMAGE=%s\n' "$(docker inspect -f '{{.Image}}' "$backend_id")"
printf 'POSTGRES_IMAGE=%s\n' "$(docker inspect -f '{{.Config.Image}}' "$postgres_id")"
printf 'REDIS_IMAGE=%s\n' "$(docker inspect -f '{{.Config.Image}}' "$redis_id")"
printf 'REVISION=%s\n' "$revision"
printf 'SCHEMA_HASH=%s\n' "$schema_hash"
printf 'STRUCTURE_HASH=%s\n' "$structure_hash"
printf 'BACKEND_ENV_HASH=%s\n' "$(sha256sum __REMOTE_PATH__/backend/.env | awk '{print $1}')"
for table in __PROTECTED_TABLES__; do
  count="$(docker compose -p __COMPOSE_PROJECT__ exec -T postgres psql -U shamrai -d shamrai -At -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM $table")"
  upper="$(printf '%s' "$table" | tr '[:lower:]' '[:upper:]')"
  printf 'COUNT_%s=%s\n' "$upper" "$count"
done
'@
  $script = $script.Replace("__REMOTE_PATH__", (ConvertTo-ShellSingleQuoted $RemotePath))
  $script = $script.Replace("__COMPOSE_PROJECT__", (ConvertTo-ShellSingleQuoted $ComposeProject))
  $script = $script.Replace("__COMPOSE_PROJECT_RAW__", $ComposeProject)
  $script = $script.Replace("__PROTECTED_TABLES__", $tables)
  return ConvertFrom-KeyValueOutput (Invoke-RemoteOutputChecked -Command $script)
}

function Invoke-RemoteChecked {
  param([Parameter(Mandatory = $true)][string]$Command)
  $arguments = @($script:SshArguments + @($Server, $Command))
  Invoke-NativeChecked -FilePath $script:SshTool -Arguments $arguments
}

function Invoke-RemoteOutputChecked {
  param([Parameter(Mandatory = $true)][string]$Command)
  $arguments = @($script:SshArguments + @($Server, $Command))
  return Invoke-NativeOutputChecked -FilePath $script:SshTool -Arguments $arguments
}

function Copy-ToRemoteChecked {
  param(
    [Parameter(Mandatory = $true)][string]$LocalPath,
    [Parameter(Mandatory = $true)][string]$RemoteFilePath
  )
  $target = "${Server}:$RemoteFilePath"
  $arguments = @(
    "-i", $SshKeyPath,
    "-P", "$SshPort",
    "-o", "BatchMode=yes",
    "-o", "IdentitiesOnly=yes",
    "-o", "UserKnownHostsFile=$KnownHostsPath",
    "-o", "StrictHostKeyChecking=yes",
    $LocalPath,
    $target
  )
  Invoke-NativeChecked -FilePath $script:ScpTool -Arguments $arguments
}

function New-OverlayArchive {
  param([string]$Destination)
  $partition = @($OverlayFiles) + @($DeferredOverlayFiles)
  if (
    $partition.Count -ne $ApprovedOverlayFiles.Count -or
    @($partition | Sort-Object -Unique).Count -ne $ApprovedOverlayFiles.Count -or
    (Compare-Object ($partition | Sort-Object) ($ApprovedOverlayFiles | Sort-Object))
  ) {
    throw "The active and deferred no-DB overlays do not partition the approved file list."
  }
  foreach ($relativePath in $OverlayFiles) {
    $fullPath = Join-Path $Workspace ($relativePath.Replace("/", "\"))
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
      throw "Overlay file was not found: $relativePath"
    }
    $item = Get-Item -LiteralPath $fullPath
    if ($item.LinkType) {
      throw "Overlay symlinks are forbidden: $relativePath"
    }
  }
  foreach ($path in $ForbiddenOverlayFiles) {
    if ($OverlayFiles -contains $path) {
      throw "Forbidden path was included in the no-DB overlay: $path"
    }
  }
  Invoke-NativeChecked -FilePath $script:TarTool -Arguments (@("-czf", $Destination, "-C", $Workspace) + $OverlayFiles)
  if (-not (Test-Path -LiteralPath $Destination -PathType Leaf) -or (Get-Item $Destination).Length -le 0) {
    throw "No-DB overlay archive was not created."
  }
}

function New-EncryptedDatabaseBackup {
  param([string]$BackupPath)
  $recipient = Invoke-NativeOutputChecked -FilePath $script:AgeKeygenTool -Arguments @("-y", $AgeIdentityPath)
  if ($recipient -notmatch '^age1[0-9a-z]+$') {
    throw "Unable to derive a valid age recipient from the configured identity."
  }
  $remoteDump = "cd $((ConvertTo-ShellSingleQuoted $RemotePath)) && docker compose -p $((ConvertTo-ShellSingleQuoted $ComposeProject)) exec -T postgres pg_dump -U shamrai -d shamrai -Fc"
  $sourceArguments = @($script:SshArguments + @($Server, $remoteDump))
  try {
    Invoke-BinaryPipeline `
      -SourceFile $script:SshTool `
      -SourceArguments $sourceArguments `
      -SinkFile $script:AgeTool `
      -SinkArguments @("-r", $recipient, "-o", $BackupPath)
    if (-not (Test-Path -LiteralPath $BackupPath -PathType Leaf) -or (Get-Item $BackupPath).Length -lt 1024) {
      throw "Encrypted pg_dump is missing or unexpectedly small."
    }
    Set-OwnerOnlyLocalPath -Path $BackupPath -Directory $false
  } catch {
    Remove-Item -LiteralPath $BackupPath -Force -ErrorAction SilentlyContinue
    throw
  }
}

function New-CountsFile {
  param([hashtable]$Baseline, [string]$Destination)
  $lines = foreach ($table in $ProtectedTables) {
    $key = "COUNT_$($table.ToUpperInvariant())"
    if (-not $Baseline.ContainsKey($key) -or $Baseline[$key] -notmatch '^\d+$') {
      throw "Remote baseline did not return a valid count for $table."
    }
    "$table`t$($Baseline[$key])"
  }
  [IO.File]::WriteAllText($Destination, (($lines -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
}

function New-RemoteStage {
  $command = "stage=`$(mktemp -d /tmp/shamrai-no-db.XXXXXX); chmod 0700 `"`$stage`"; printf '%s' `"`$stage`""
  $stage = (Invoke-RemoteOutputChecked -Command $command).Trim()
  if ($stage -notmatch '^/tmp/shamrai-no-db\.[A-Za-z0-9]{6}$') {
    throw "Remote no-DB stage path was invalid: $stage"
  }
  return $stage
}

function Initialize-RemoteCandidate {
  param(
    [string]$Stage,
    [string]$CandidateImage,
    [string]$RestoreProject,
    [string]$PostgresImage,
    [string]$RedisImage
  )
  $expectedList = ($OverlayFiles | Sort-Object) -join "`n"
  $template = @'
set -Eeuo pipefail
stage=__STAGE__
remote_path=__REMOTE_PATH__
candidate_image=__CANDIDATE_IMAGE__
restore_project=__RESTORE_PROJECT__
mkdir -p "$stage/source"
chmod 0700 "$stage"
cp -a "$remote_path/backend" "$stage/source/backend"
rm -f "$stage/source/backend/.env" "$stage/source/backend/check.py" "$stage/source/backend/get_export.py"
find "$stage/source/backend" -type f \( -name '.env' -o -name '.env.*' -o -name '*.pem' \) -delete
chmod 0600 "$stage/overlay.tar.gz" "$stage/counts.tsv"
tar -tzf "$stage/overlay.tar.gz" | sed 's#^\./##' | sort > "$stage/archive-files.txt"
cat > "$stage/expected-files.txt" <<'FILES'
__EXPECTED_FILES__
FILES
if ! diff -u "$stage/expected-files.txt" "$stage/archive-files.txt"; then
  echo 'No-DB overlay archive did not contain exactly the approved paths.' >&2
  exit 41
fi
tar -xzf "$stage/overlay.tar.gz" -C "$stage/source"
if find "$stage/source/backend" -type f \( -name '.env' -o -name '.env.*' -o -name '*.pem' \) | grep -q .; then
  echo 'Candidate build context contains a secret-bearing file.' >&2
  exit 42
fi
docker build --label "shamrai.no_db_candidate=$restore_project" -t "$candidate_image" "$stage/source/backend"
docker run --rm --entrypoint python "$candidate_image" -m compileall -q src
cat > "$stage/restore.yml" <<EOF
services:
  postgres:
    image: __POSTGRES_IMAGE__
    environment:
      POSTGRES_DB: shamrai_restore
      POSTGRES_USER: shamrai_restore
      POSTGRES_PASSWORD: restore_drill_password
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U \$\${POSTGRES_USER} -d \$\${POSTGRES_DB}"]
      interval: 2s
      timeout: 3s
      retries: 30
    volumes:
      - postgres_data:/var/lib/postgresql/data
  redis:
    image: __REDIS_IMAGE__
    command: ["redis-server", "--appendonly", "yes"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 3s
      retries: 30
  backend:
    image: __CANDIDATE_IMAGE_RAW__
    environment:
      APP_ENV: restore-drill
      DEBUG_MODE: "false"
      ALLOW_DEBUG_AUTH_BYPASS: "false"
      ENABLE_BACKGROUND_TASKS: "false"
      DATABASE_URL: postgresql://shamrai_restore:restore_drill_password@postgres:5432/shamrai_restore
      REDIS_URL: redis://redis:6379/0
      REDIS_CACHE_ENABLED: "true"
      JWT_SECRET_KEY: restore-drill-jwt-secret-not-real-1234567890
      CORS_ALLOWED_ORIGINS: http://127.0.0.1
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
    read_only: true
    tmpfs:
      - /tmp:uid=10001,gid=10001,mode=1777
      - /app/static:uid=10001,gid=10001,mode=0755
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).read()\""]
      interval: 3s
      timeout: 5s
      retries: 30
volumes:
  postgres_data:
EOF
docker compose -f "$stage/restore.yml" -p "$restore_project" down -v --remove-orphans >/dev/null 2>&1 || true
docker compose -f "$stage/restore.yml" -p "$restore_project" up -d postgres redis
for _ in $(seq 1 60); do
  if docker compose -f "$stage/restore.yml" -p "$restore_project" exec -T postgres pg_isready -U shamrai_restore -d shamrai_restore >/dev/null 2>&1; then
    exit 0
  fi
  sleep 2
done
echo 'Isolated restore Postgres did not become ready.' >&2
exit 43
'@
  $template = $template.Replace("__STAGE__", (ConvertTo-ShellSingleQuoted $Stage))
  $template = $template.Replace("__REMOTE_PATH__", (ConvertTo-ShellSingleQuoted $RemotePath))
  $template = $template.Replace("__CANDIDATE_IMAGE__", (ConvertTo-ShellSingleQuoted $CandidateImage))
  $template = $template.Replace("__CANDIDATE_IMAGE_RAW__", $CandidateImage)
  $template = $template.Replace("__RESTORE_PROJECT__", (ConvertTo-ShellSingleQuoted $RestoreProject))
  $template = $template.Replace("__POSTGRES_IMAGE__", $PostgresImage)
  $template = $template.Replace("__REDIS_IMAGE__", $RedisImage)
  $template = $template.Replace("__EXPECTED_FILES__", $expectedList)
  Invoke-RemoteChecked -Command $template
}

function Restore-EncryptedBackupToCandidate {
  param([string]$BackupPath, [string]$Stage, [string]$RestoreProject)
  $remoteRestore = "docker compose -f $((ConvertTo-ShellSingleQuoted "$Stage/restore.yml")) -p $((ConvertTo-ShellSingleQuoted $RestoreProject)) exec -T postgres pg_restore --no-owner --role=shamrai_restore --exit-on-error -U shamrai_restore -d shamrai_restore"
  $sinkArguments = @($script:SshArguments + @($Server, $remoteRestore))
  Invoke-BinaryPipeline `
    -SourceFile $script:AgeTool `
    -SourceArguments @("-d", "-i", $AgeIdentityPath, $BackupPath) `
    -SinkFile $script:SshTool `
    -SinkArguments $sinkArguments
}

function Test-RemoteCandidate {
  param(
    [string]$Stage,
    [string]$RestoreProject,
    [string]$ExpectedStructureHash
  )
  $template = @'
set -Eeuo pipefail
stage=__STAGE__
restore_project=__RESTORE_PROJECT__
expected_revision=__EXPECTED_REVISION__
expected_structure_hash=__EXPECTED_STRUCTURE_HASH__
compose="docker compose -f $stage/restore.yml -p $restore_project"
revision="$($compose exec -T postgres psql -U shamrai_restore -d shamrai_restore -At -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version')"
[ "$revision" = "$expected_revision" ] || { echo "Restored revision mismatch: $revision" >&2; exit 51; }
while IFS=$'\t' read -r table expected; do
  [ -n "$table" ] || continue
  case "$table" in
    users|bets|user_bets|payment_attempts|subscriptions|flat_subscriptions|historical_stats_import_batches|historical_stats_monthly|historical_stats_breakdowns|historical_stats_details) ;;
    *) echo "Unsafe protected table name: $table" >&2; exit 52 ;;
  esac
  actual="$($compose exec -T postgres psql -U shamrai_restore -d shamrai_restore -At -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM $table")"
  [ "$actual" = "$expected" ] || { echo "Restore count mismatch for $table: expected=$expected actual=$actual" >&2; exit 53; }
done < "$stage/counts.tsv"
structure_hash="$($compose exec -T postgres pg_dump -U shamrai_restore -d shamrai_restore --schema-only --no-owner --no-privileges | sed -E '/^\\(un)?restrict /d' | awk '
/^    CONSTRAINT .* CHECK / { sub(/ CHECK .*/, " CHECK <expression>") }
/^CREATE .* INDEX .* WHERE / { sub(/ WHERE .*/, " WHERE <predicate>;") }
{ print }
' | sha256sum | awk '{print $1}')"
[ "$structure_hash" = "$expected_structure_hash" ] || { echo "Restore structure hash mismatch: expected=$expected_structure_hash actual=$structure_hash" >&2; exit 54; }
$compose up -d backend
candidate_ready=0
for _ in $(seq 1 60); do
  backend_id="$($compose ps -q --all backend)"
  state="$(docker inspect -f '{{.State.Status}}' "$backend_id" 2>/dev/null || true)"
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$backend_id" 2>/dev/null || true)"
  case "$state" in
    exited|dead|removing)
      echo 'Candidate backend stopped before becoming healthy.' >&2
      $compose logs --tail 100 backend >&2
      exit 55
      ;;
  esac
  if [ "$health" = "healthy" ]; then
    candidate_ready=1
    break
  fi
  if [ "$health" = "unhealthy" ]; then
    $compose logs --tail 100 backend >&2
    exit 55
  fi
  sleep 2
done
[ "$candidate_ready" = "1" ] || {
  echo 'Candidate backend health check timed out.' >&2
  $compose logs --tail 100 backend >&2
  exit 55
}
$compose exec -T backend python -c 'import src.main; print("candidate_import_ok")'
candidate_revision="$($compose exec -T backend alembic current 2>/dev/null | tail -n 1 | awk '{print $1}')"
[ "$candidate_revision" = "$expected_revision" ] || { echo "Candidate expects another revision: $candidate_revision" >&2; exit 56; }
$compose exec -T backend python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=5).read()'
echo 'NO_DB_RESTORE_DRILL_OK'
'@
  $template = $template.Replace("__STAGE__", (ConvertTo-ShellSingleQuoted $Stage))
  $template = $template.Replace("__RESTORE_PROJECT__", (ConvertTo-ShellSingleQuoted $RestoreProject))
  $template = $template.Replace("__EXPECTED_REVISION__", (ConvertTo-ShellSingleQuoted $ExpectedAlembicRevision))
  $template = $template.Replace("__EXPECTED_STRUCTURE_HASH__", (ConvertTo-ShellSingleQuoted $ExpectedStructureHash))
  Invoke-RemoteChecked -Command $template
}

function Remove-RemoteCandidateResources {
  param([string]$Stage, [string]$RestoreProject, [string]$CandidateImage)
  if ($Stage -notmatch '^/tmp/shamrai-no-db\.[A-Za-z0-9]{6}$') { return }
  $command = "docker compose -f $((ConvertTo-ShellSingleQuoted "$Stage/restore.yml")) -p $((ConvertTo-ShellSingleQuoted $RestoreProject)) down -v --remove-orphans >/dev/null 2>&1 || true; docker image rm $((ConvertTo-ShellSingleQuoted $CandidateImage)) >/dev/null 2>&1 || true; rm -rf -- $((ConvertTo-ShellSingleQuoted $Stage))"
  Invoke-RemoteChecked -Command $command
}

function Invoke-NoDbRollback {
  param([string]$BackupRoot)
  if ($BackupRoot -notmatch '^/opt/shamrai-mini-app/\.deploy-backups/no-db-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$') {
    throw "Refusing rollback from an invalid backup root: $BackupRoot"
  }
  $template = @'
set -Eeuo pipefail
cd __REMOTE_PATH__
backup_root=__BACKUP_ROOT__
[ -d "$backup_root/backend" ] || { echo 'No-DB rollback backend snapshot is missing.' >&2; exit 71; }
[ -f "$backup_root/backend-image-id" ] || { echo 'No-DB rollback image ID is missing.' >&2; exit 71; }
[ -f "$backup_root/backend-image-tag" ] || { echo 'No-DB rollback image tag is missing.' >&2; exit 71; }
previous_image="$(cat "$backup_root/backend-image-id")"
rollback_image="$(cat "$backup_root/backend-image-tag")"
if [ -f "$backup_root/backend-image-ref" ]; then
  previous_image_ref="$(cat "$backup_root/backend-image-ref")"
else
  current_backend_id="$(docker compose -p __COMPOSE_PROJECT__ ps -q backend)"
  previous_image_ref="$(docker inspect -f '{{.Config.Image}}' "$current_backend_id")"
fi
case "$previous_image" in sha256:[0-9a-f][0-9a-f]*) ;; *) echo 'Invalid rollback image ID.' >&2; exit 71 ;; esac
case "$rollback_image" in shamrai-no-db-rollback:*) ;; *) echo 'Invalid rollback image tag.' >&2; exit 71 ;; esac
printf '%s' "$previous_image_ref" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._/-]*(:[A-Za-z0-9][A-Za-z0-9._-]*)?$' || { echo 'Invalid rollback image reference.' >&2; exit 71; }
failed="$backup_root/failed-backend"
rm -rf "$failed"
mv backend "$failed"
cp -a "$backup_root/backend" backend
if docker image inspect "$rollback_image" >/dev/null 2>&1; then
  docker image tag "$rollback_image" "$previous_image_ref"
  docker compose -p __COMPOSE_PROJECT__ up -d --no-deps --force-recreate --no-build backend
else
  docker image inspect "$previous_image" >/dev/null
  docker image tag "$previous_image" "$previous_image_ref"
  docker compose -p __COMPOSE_PROJECT__ up -d --no-deps --force-recreate --no-build backend
fi
for _ in $(seq 1 60); do
  curl -fsS __HEALTH_URL__ >/dev/null 2>&1 && exit 0
  sleep 2
done
docker compose -p __COMPOSE_PROJECT__ logs --tail 150 backend >&2
exit 72
'@
  $template = $template.Replace("__REMOTE_PATH__", (ConvertTo-ShellSingleQuoted $RemotePath))
  $template = $template.Replace("__BACKUP_ROOT__", (ConvertTo-ShellSingleQuoted $BackupRoot))
  $template = $template.Replace("__COMPOSE_PROJECT__", (ConvertTo-ShellSingleQuoted $ComposeProject))
  $template = $template.Replace("__HEALTH_URL__", (ConvertTo-ShellSingleQuoted $HealthUrl))
  Invoke-RemoteChecked -Command $template
}

function Invoke-ProductionCutover {
  param([string]$Stage, [string]$BackupRoot, [string]$CandidateImage)
  $template = @'
set -Eeuo pipefail
cd __REMOTE_PATH__
stage=__STAGE__
backup_root=__BACKUP_ROOT__
candidate_image=__CANDIDATE_IMAGE__
mkdir -p "$backup_root"
chmod 0700 "$backup_root"
backend_id="$(docker compose -p __COMPOSE_PROJECT__ ps -q backend)"
previous_image="$(docker inspect -f '{{.Image}}' "$backend_id")"
previous_image_ref="$(docker inspect -f '{{.Config.Image}}' "$backend_id")"
candidate_id="$(docker image inspect -f '{{.Id}}' "$candidate_image")"
case "$previous_image" in sha256:[0-9a-f][0-9a-f]*) ;; *) echo 'Invalid current backend image ID.' >&2; exit 60 ;; esac
case "$candidate_id" in sha256:[0-9a-f][0-9a-f]*) ;; *) echo 'Invalid candidate backend image ID.' >&2; exit 60 ;; esac
printf '%s' "$previous_image_ref" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._/-]*(:[A-Za-z0-9][A-Za-z0-9._-]*)?$' || { echo 'Invalid current backend image reference.' >&2; exit 60; }
rollback_image="shamrai-no-db-rollback:$(basename "$backup_root" | sed 's/^no-db-//')"
docker image tag "$previous_image" "$rollback_image"
printf '%s\n' "$previous_image" > "$backup_root/backend-image-id"
printf '%s\n' "$rollback_image" > "$backup_root/backend-image-tag"
printf '%s\n' "$previous_image_ref" > "$backup_root/backend-image-ref"
chmod 0600 "$backup_root/backend-image-id" "$backup_root/backend-image-tag" "$backup_root/backend-image-ref"
cp -a backend "$backup_root/backend"
chmod 0600 "$backup_root/backend/.env"
tar -xzf "$stage/overlay.tar.gz" -C __REMOTE_PATH__
docker image tag "$candidate_id" "$previous_image_ref"
docker compose -p __COMPOSE_PROJECT__ up -d --no-deps --force-recreate --no-build backend
running_backend_id="$(docker compose -p __COMPOSE_PROJECT__ ps -q backend)"
running_image="$(docker inspect -f '{{.Image}}' "$running_backend_id")"
[ "$running_image" = "$candidate_id" ] || { echo 'Production backend did not start from the qualified candidate image.' >&2; exit 62; }
for _ in $(seq 1 60); do
  if curl -fsS __HEALTH_URL__ >/dev/null 2>&1; then
    exit 0
  fi
  sleep 2
done
docker compose -p __COMPOSE_PROJECT__ logs --tail 150 backend >&2
exit 61
'@
  $template = $template.Replace("__REMOTE_PATH__", (ConvertTo-ShellSingleQuoted $RemotePath))
  $template = $template.Replace("__STAGE__", (ConvertTo-ShellSingleQuoted $Stage))
  $template = $template.Replace("__BACKUP_ROOT__", (ConvertTo-ShellSingleQuoted $BackupRoot))
  $template = $template.Replace("__CANDIDATE_IMAGE__", (ConvertTo-ShellSingleQuoted $CandidateImage))
  $template = $template.Replace("__COMPOSE_PROJECT__", (ConvertTo-ShellSingleQuoted $ComposeProject))
  $template = $template.Replace("__HEALTH_URL__", (ConvertTo-ShellSingleQuoted $HealthUrl))
  Invoke-RemoteChecked -Command $template
}

function Assert-PostDeployState {
  param([hashtable]$Before, [hashtable]$After)
  if ($After.REVISION -ne $ExpectedAlembicRevision) {
    throw "Alembic revision changed during a no-DB deployment: expected=$ExpectedAlembicRevision actual=$($After.REVISION)"
  }
  if ($After.SCHEMA_HASH -ne $Before.SCHEMA_HASH) {
    throw "PostgreSQL schema hash changed during a no-DB deployment."
  }
  if ($After.POSTGRES_ID -ne $Before.POSTGRES_ID) {
    throw "Postgres container changed during a no-DB deployment."
  }
  if ($After.REDIS_ID -ne $Before.REDIS_ID) {
    throw "Redis container changed during a no-DB deployment."
  }
  if ($After.FRONTEND_ID -ne $Before.FRONTEND_ID) {
    throw "Frontend container changed during a backend-only no-DB deployment."
  }
  if ($After.BACKEND_ENV_HASH -ne $Before.BACKEND_ENV_HASH) {
    throw "backend/.env changed during a no-DB deployment."
  }
}

$canonicalServer = "root@82.147.67.245"
$canonicalRemotePath = "/opt/shamrai-mini-app"
$canonicalComposeProject = "shamrai"
if ($Server -ne $canonicalServer -or $RemotePath -ne $canonicalRemotePath -or $ComposeProject -ne $canonicalComposeProject) {
  throw "No-DB deployment accepts only the canonical Shamrai server, path, and Compose project."
}
if ($ExpectedAlembicRevision -ne "20260802_0041") {
  throw "No-DB deployment is locked to the verified 20260802_0041 schema."
}

$Workspace = (Resolve-Path -LiteralPath $Workspace).Path
if ([string]::IsNullOrWhiteSpace($SshKeyPath)) {
  $SshKeyPath = Join-Path (Get-HomePath) ".ssh\codex_deploy_ed25519"
}
if ([string]::IsNullOrWhiteSpace($KnownHostsPath)) {
  $KnownHostsPath = Join-Path (Get-HomePath) ".ssh\known_hosts"
}
if ([string]::IsNullOrWhiteSpace($AgeIdentityPath)) {
  $AgeIdentityPath = Join-Path (Get-HomePath) ".config\age\shamrai-backup-identity.txt"
}
foreach ($requiredFile in @($SshKeyPath, $KnownHostsPath, $AgeIdentityPath)) {
  if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
    throw "Required local security file was not found: $requiredFile"
  }
}

$script:SshTool = Find-Tool @("ssh.exe", "ssh")
$script:ScpTool = Find-Tool @("scp.exe", "scp")
$script:TarTool = Find-Tool @("tar.exe", "tar")
$script:AgeTool = Find-Tool @("age.exe", "age")
$script:AgeKeygenTool = Find-Tool @("age-keygen.exe", "age-keygen")
foreach ($tool in @($script:SshTool, $script:ScpTool, $script:TarTool, $script:AgeTool, $script:AgeKeygenTool)) {
  if (-not $tool) { throw "A required deployment tool was not found." }
}

$script:SshArguments = @(
  "-i", $SshKeyPath,
  "-p", "$SshPort",
  "-o", "BatchMode=yes",
  "-o", "IdentitiesOnly=yes",
  "-o", "UserKnownHostsFile=$KnownHostsPath",
  "-o", "StrictHostKeyChecking=yes"
)

$deployId = "{0}-{1}" -f ([DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")), ([Guid]::NewGuid().ToString("N").Substring(0, 8))
$restoreProject = "shamrai-no-db-restore-$($deployId.ToLowerInvariant())"
$candidateImage = "shamrai-no-db-candidate:$($deployId.ToLowerInvariant())"
$backupRoot = "$RemotePath/.deploy-backups/no-db-$deployId"
$deployDir = Join-Path $Workspace ".deploy"
$backupDir = Join-Path $deployDir "db-backups"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Set-OwnerOnlyLocalPath -Path $backupDir -Directory $true
$overlayArchive = Join-Path $deployDir "no-db-overlay-$deployId.tar.gz"
$countsPath = Join-Path $deployDir "no-db-counts-$deployId.tsv"
$backupPath = Join-Path $backupDir "shamrai-no-db.$deployId.dump.age"
$manifestPath = Join-Path $backupDir "shamrai-no-db.$deployId.manifest.json"
$script:remoteStage = ""
$script:cutoverStarted = $false

try {
  Invoke-Step "Capture canonical 0041 baseline" {
    $script:baseline = Get-RemoteBaseline
    if ($script:baseline.REVISION -ne $ExpectedAlembicRevision) {
      throw "Server revision is not safe for the no-DB overlay: $($script:baseline.REVISION)"
    }
    New-CountsFile -Baseline $script:baseline -Destination $countsPath
    Write-Host "revision=$($script:baseline.REVISION)"
    Write-Host "schema_hash=$($script:baseline.SCHEMA_HASH)"
  }

  Invoke-Step "Create approved eight-file overlay" {
    New-OverlayArchive -Destination $overlayArchive
    Write-Host "approved_overlay_file_count=8"
    Write-Host "overlay_file_count=3"
    Write-Host "deferred_overlay_file_count=5"
  }

  Invoke-Step "Create encrypted production pg_dump" {
    New-EncryptedDatabaseBackup -BackupPath $backupPath
    $overlayHashes = [ordered]@{}
    foreach ($relativePath in $OverlayFiles) {
      $fullPath = Join-Path $Workspace ($relativePath.Replace("/", "\"))
      $overlayHashes[$relativePath] = (Get-FileHash -Algorithm SHA256 -LiteralPath $fullPath).Hash.ToLowerInvariant()
    }
    $tableCounts = [ordered]@{}
    foreach ($table in $ProtectedTables) {
      $tableCounts[$table] = [int64]$script:baseline["COUNT_$($table.ToUpperInvariant())"]
    }
    $manifest = [ordered]@{
      schema_version = 1
      type = "shamrai_no_db_predeploy_backup"
      created_at = [DateTime]::UtcNow.ToString("o")
      deploy_id = $deployId
      server = "82.147.67.245"
      compose_project = $ComposeProject
      alembic_revision = $script:baseline.REVISION
      schema_sha256 = $script:baseline.SCHEMA_HASH
      structure_sha256 = $script:baseline.STRUCTURE_HASH
      encrypted_dump_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $backupPath).Hash.ToLowerInvariant()
      encrypted_dump_size = (Get-Item -LiteralPath $backupPath).Length
      table_counts = $tableCounts
      approved_overlay_files = $ApprovedOverlayFiles
      deferred_overlay_files = $DeferredOverlayFiles
      overlay_sha256 = $overlayHashes
    }
    [IO.File]::WriteAllText($manifestPath, (($manifest | ConvertTo-Json -Depth 8) + "`n"), [Text.UTF8Encoding]::new($false))
    Set-OwnerOnlyLocalPath -Path $manifestPath -Directory $false
    Write-Host "encrypted_backup=$backupPath"
  }

  Invoke-Step "Build candidate and start isolated restore database" {
    $script:remoteStage = New-RemoteStage
    Copy-ToRemoteChecked -LocalPath $overlayArchive -RemoteFilePath "$script:remoteStage/overlay.tar.gz"
    Copy-ToRemoteChecked -LocalPath $countsPath -RemoteFilePath "$script:remoteStage/counts.tsv"
    Initialize-RemoteCandidate `
      -Stage $script:remoteStage `
      -CandidateImage $candidateImage `
      -RestoreProject $restoreProject `
      -PostgresImage $script:baseline.POSTGRES_IMAGE `
      -RedisImage $script:baseline.REDIS_IMAGE
  }

  Invoke-Step "Restore encrypted backup without writing a raw dump" {
    Restore-EncryptedBackupToCandidate -BackupPath $backupPath -Stage $script:remoteStage -RestoreProject $restoreProject
    Test-RemoteCandidate -Stage $script:remoteStage -RestoreProject $restoreProject -ExpectedStructureHash $script:baseline.STRUCTURE_HASH
  }

  Invoke-Step "Switch only the canonical backend" {
    $script:cutoverStarted = $true
    Invoke-ProductionCutover -Stage $script:remoteStage -BackupRoot $backupRoot -CandidateImage $candidateImage
  }

  Invoke-Step "Verify no database or dependency container change" {
    $after = Get-RemoteBaseline
    Assert-PostDeployState -Before $script:baseline -After $after
    $logCheck = "cd $((ConvertTo-ShellSingleQuoted $RemotePath)) && docker compose -p $((ConvertTo-ShellSingleQuoted $ComposeProject)) logs --since 10m backend 2>&1 | grep -Eiq 'undefined table|undefined column|does not exist|missing column|missing table' && exit 1 || exit 0"
    Invoke-RemoteChecked -Command $logCheck
    Invoke-RemoteChecked -Command "curl -fsS $((ConvertTo-ShellSingleQuoted $HealthUrl)) >/dev/null"
    Write-Host "database_mode=none"
    Write-Host "alembic_revision=$($after.REVISION)"
    Write-Host "schema_hash_unchanged=true"
    Write-Host "postgres_container_unchanged=true"
    Write-Host "redis_container_unchanged=true"
    Write-Host "frontend_container_unchanged=true"
  }
} catch {
  $originalError = $_
  if ($script:cutoverStarted) {
    Write-Warning "No-DB cutover failed; restoring the exact backend snapshot."
    try {
      Invoke-NoDbRollback -BackupRoot $backupRoot
    } catch {
      Write-Error "Automatic no-DB rollback also failed: $($_.Exception.Message)"
    }
  }
  throw $originalError
} finally {
  if (-not [string]::IsNullOrWhiteSpace($script:remoteStage)) {
    try {
      Remove-RemoteCandidateResources -Stage $script:remoteStage -RestoreProject $restoreProject -CandidateImage $candidateImage
    } catch {
      Write-Warning "Temporary no-DB candidate cleanup failed: $($_.Exception.Message)"
    }
  }
  Remove-Item -LiteralPath $overlayArchive -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $countsPath -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "No-DB backend deployment completed." -ForegroundColor Green
Write-Host "backup_manifest=$manifestPath"
Write-Host "code_backup=$backupRoot"
