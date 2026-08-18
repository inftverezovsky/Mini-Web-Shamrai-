param(
  [string]$Workspace = "C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)",
  [string]$Server = "root@82.147.67.245",
  [int]$SshPort = 22,
  [string]$HostKeyFingerprint = "SHA256:xdVRtRaXWqK6eAIsE3VwD0o2H6GJDcCm65L1ZUBjuMw",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [string]$SshKeyPath = $env:SHAMRAI_SSH_KEY_PATH,
  [string]$KnownHostsPath = "",
  [string]$AgeIdentityPath = $env:SHAMRAI_BACKUP_AGE_IDENTITY_PATH,
  [string]$AttestationPath = "",
  [switch]$NoHostKeyScan,
  [switch]$LeaveBackendStopped,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($PSVersionTable.PSVersion.Major -lt 7) {
  throw "PowerShell 7 or newer is required for binary-safe encrypted backup streaming."
}

$ProtectedTables = @(
  "users",
  "bets",
  "user_bets",
  "subscription_plans",
  "payment_attempts",
  "subscriptions",
  "flat_subscriptions",
  "flat_subscription_credits",
  "quizzes",
  "historical_stats_import_batches",
  "historical_stats_monthly",
  "historical_stats_breakdowns",
  "historical_stats_details"
)

function Get-HomePath {
  if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) { return $env:USERPROFILE }
  if (-not [string]::IsNullOrWhiteSpace($HOME)) { return $HOME }
  throw "Unable to resolve the current user's home directory."
}

function Find-Tool {
  param([string[]]$Candidates)
  foreach ($candidate in $Candidates) {
    if (Test-Path -LiteralPath $candidate) { return (Resolve-Path -LiteralPath $candidate).Path }
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
  }
  return $null
}

function ConvertTo-ShellSingleQuoted {
  param([string]$Value)
  if ($null -eq $Value) { $Value = "" }
  return "'" + $Value.Replace("'", "'`"'`"'") + "'"
}

function Invoke-NativeChecked {
  param([string]$FilePath, [string[]]$Arguments = @())
  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code ${LASTEXITCODE}: $FilePath" }
}

function Invoke-NativeOutputChecked {
  param([string]$FilePath, [string[]]$Arguments = @())
  $output = & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code ${LASTEXITCODE}: $FilePath" }
  return ($output -join "`n").Trim()
}

function Invoke-BinaryPipeline {
  param(
    [string]$SourceFile,
    [string[]]$SourceArguments,
    [string]$SinkFile,
    [string[]]$SinkArguments,
    [int]$TimeoutSeconds = 600
  )
  if ($TimeoutSeconds -lt 30 -or $TimeoutSeconds -gt 3600) { throw "Binary pipeline timeout is outside the safe range." }
  $sourceInfo = [Diagnostics.ProcessStartInfo]::new()
  $sourceInfo.FileName = $SourceFile
  $sourceInfo.UseShellExecute = $false
  $sourceInfo.RedirectStandardOutput = $true
  $sourceInfo.RedirectStandardError = $true
  foreach ($argument in $SourceArguments) { [void]$sourceInfo.ArgumentList.Add($argument) }
  $sinkInfo = [Diagnostics.ProcessStartInfo]::new()
  $sinkInfo.FileName = $SinkFile
  $sinkInfo.UseShellExecute = $false
  $sinkInfo.RedirectStandardInput = $true
  $sinkInfo.RedirectStandardError = $true
  foreach ($argument in $SinkArguments) { [void]$sinkInfo.ArgumentList.Add($argument) }
  $source = [Diagnostics.Process]::new()
  $source.StartInfo = $sourceInfo
  $sink = [Diagnostics.Process]::new()
  $sink.StartInfo = $sinkInfo
  $sourceStarted = $false
  $sinkStarted = $false
  try {
    [void]$sink.Start()
    $sinkStarted = $true
    [void]$source.Start()
    $sourceStarted = $true
    $sourceError = $source.StandardError.ReadToEndAsync()
    $sinkError = $sink.StandardError.ReadToEndAsync()
    $copyTask = $source.StandardOutput.BaseStream.CopyToAsync($sink.StandardInput.BaseStream)
    if (-not $copyTask.Wait($TimeoutSeconds * 1000)) {
      if (-not $source.HasExited) { $source.Kill($true) }
      if (-not $sink.HasExited) { $sink.Kill($true) }
      throw "Binary pipeline exceeded the ${TimeoutSeconds}-second timeout."
    }
    [void]$copyTask.GetAwaiter().GetResult()
    $sink.StandardInput.Close()
    if (-not $source.WaitForExit(30000) -or -not $sink.WaitForExit(30000)) {
      if (-not $source.HasExited) { $source.Kill($true) }
      if (-not $sink.HasExited) { $sink.Kill($true) }
      throw "Binary pipeline process exit timed out."
    }
    $sourceMessage = $sourceError.GetAwaiter().GetResult().Trim()
    $sinkMessage = $sinkError.GetAwaiter().GetResult().Trim()
    if ($source.ExitCode -ne 0 -or $sink.ExitCode -ne 0) {
      throw "Binary pipeline failed: source=$($source.ExitCode) sink=$($sink.ExitCode) source_error=$sourceMessage sink_error=$sinkMessage"
    }
  } finally {
    if ($sourceStarted -and -not $source.HasExited) { $source.Kill($true) }
    if ($sinkStarted -and -not $sink.HasExited) { $sink.Kill($true) }
    $source.Dispose()
    $sink.Dispose()
  }
}

function Set-OwnerOnlyLocalPath {
  param([string]$Path, [bool]$Directory)
  $icacls = Find-Tool @("icacls.exe", "icacls")
  if (-not $icacls) { throw "icacls is required to protect local backup material." }
  $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
  $grant = if ($Directory) { "${identity}:(OI)(CI)F" } else { "${identity}:F" }
  Invoke-NativeChecked $icacls @($Path, "/inheritance:r", "/grant:r", $grant)
}

function ConvertFrom-KeyValueOutput {
  param([string]$Output)
  $result = @{}
  foreach ($line in ($Output -split "`r?`n")) {
    if ($line -match '^([A-Z0-9_]+)=(.*)$') { $result[$Matches[1]] = $Matches[2] }
  }
  return $result
}

if ($DryRun) {
  Write-Host "DRY_RUN verify-shamrai-release-backup"
  Write-Host "encrypted_streaming_dump=true"
  Write-Host "plaintext_dump_persisted=false"
  Write-Host "isolated_postgres_restore=true"
  Write-Host "leave_backend_stopped=$([bool]$LeaveBackendStopped)"
  exit 0
}

if ($RemotePath -notmatch '^/[A-Za-z0-9._/-]+$' -or $RemotePath -match '(^|/)\.\.(/|$)' -or $RemotePath.Contains('//')) {
  throw "RemotePath must be a normalized absolute Unix path."
}
if ($ComposeProject -notmatch '^[a-z0-9][a-z0-9_-]*$') { throw "ComposeProject has an invalid shape." }
if ($Server -notmatch '^(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9._-]+$') { throw "Server has an invalid shape." }

$homePath = Get-HomePath
if ([string]::IsNullOrWhiteSpace($SshKeyPath)) { $SshKeyPath = Join-Path $homePath ".ssh\codex_deploy_ed25519" }
if ([string]::IsNullOrWhiteSpace($AgeIdentityPath)) { $AgeIdentityPath = Join-Path $homePath ".config\age\shamrai-backup-identity.txt" }
foreach ($requiredPath in @($SshKeyPath, $AgeIdentityPath)) {
  if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) { throw "Required local file was not found: $requiredPath" }
}

$ssh = Find-Tool @("ssh.exe", "ssh")
$sshKeyscan = Find-Tool @("C:\Program Files\Git\usr\bin\ssh-keyscan.exe", "ssh-keyscan.exe", "ssh-keyscan")
$sshKeygen = Find-Tool @("ssh-keygen.exe", "ssh-keygen")
$age = Find-Tool @("age.exe", "age")
$ageKeygen = Find-Tool @("age-keygen.exe", "age-keygen")
if (-not $ssh -or -not $sshKeyscan -or -not $sshKeygen -or -not $age -or -not $ageKeygen) {
  throw "OpenSSH and age tools are required."
}

$deployDirectory = Join-Path $Workspace ".deploy"
$backupDirectory = Join-Path $deployDirectory "db-backups"
New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
Set-OwnerOnlyLocalPath -Path $backupDirectory -Directory $true
if ([string]::IsNullOrWhiteSpace($AttestationPath)) {
  $AttestationPath = Join-Path $deployDirectory "shamrai-release-backup-attestation.json"
}
Remove-Item -LiteralPath $AttestationPath -Force -ErrorAction SilentlyContinue
if ([string]::IsNullOrWhiteSpace($KnownHostsPath)) { $KnownHostsPath = Join-Path $deployDirectory "shamrai-known-hosts" }
$hostName = if ($Server.Contains("@")) { $Server.Split("@", 2)[1] } else { $Server }
if (-not $NoHostKeyScan) {
  $scan = Invoke-NativeOutputChecked $sshKeyscan @("-p", "$SshPort", "-t", "ed25519", $hostName)
  [IO.File]::WriteAllText($KnownHostsPath, ($scan.Trim() + "`n"), [Text.UTF8Encoding]::new($false))
}
if (-not (Test-Path -LiteralPath $KnownHostsPath -PathType Leaf)) { throw "Known hosts file was not found." }
$fingerprint = Invoke-NativeOutputChecked $sshKeygen @("-lf", $KnownHostsPath)
if ($fingerprint -notmatch [regex]::Escape($HostKeyFingerprint)) { throw "SSH host key fingerprint mismatch." }

$sshArguments = @(
  "-i", $SshKeyPath,
  "-p", "$SshPort",
  "-o", "BatchMode=yes",
  "-o", "IdentitiesOnly=yes",
  "-o", "ConnectTimeout=15",
  "-o", "ServerAliveInterval=15",
  "-o", "ServerAliveCountMax=4",
  "-o", "UserKnownHostsFile=$KnownHostsPath",
  "-o", "StrictHostKeyChecking=yes"
)
function ConvertTo-RemoteBashCommand {
  param([Parameter(Mandatory = $true)][string]$Command)

  $normalized = ((($Command -replace "`r`n", "`n") -replace "`r", "").TrimEnd("`n")) + "`n"
  $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($normalized))
  return "printf '%s' '$encoded' | base64 -d | bash"
}
function Invoke-RemoteChecked {
  param([string]$Command)
  Invoke-NativeChecked $ssh @($sshArguments + @($Server, (ConvertTo-RemoteBashCommand $Command)))
}
function Invoke-RemoteOutputChecked {
  param([string]$Command)
  return Invoke-NativeOutputChecked $ssh @($sshArguments + @($Server, (ConvertTo-RemoteBashCommand $Command)))
}

$recipient = Invoke-NativeOutputChecked $ageKeygen @("-y", $AgeIdentityPath)
if ($recipient -notmatch '^age1[0-9a-z]+$') { throw "Unable to derive a valid age recipient." }

$releaseId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-" + [Guid]::NewGuid().ToString("N").Substring(0, 8)
$backupPath = Join-Path $backupDirectory "shamrai-stage2-final.$releaseId.dump.age"
$manifestPath = Join-Path $backupDirectory "shamrai-stage2-final.$releaseId.manifest.json"
$restoreContainer = "shamrai-stage2-restore-$($releaseId.ToLowerInvariant())"
$restoreVolume = "$restoreContainer-data"
$backendStopped = $false
$restoreCreated = $false
$succeeded = $false

$projectShell = ConvertTo-ShellSingleQuoted $ComposeProject
$tableSql = ($ProtectedTables | ForEach-Object {
  "printf 'COUNT_$($_.ToUpperInvariant())=%s\n' `"`$(docker compose -p $projectShell exec -T postgres psql -U shamrai -d shamrai -At -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM $($_)')`""
}) -join "`n"
$baselineCommand = @"
set -Eeuo pipefail
cd $(ConvertTo-ShellSingleQuoted $RemotePath)
docker compose -p $projectShell stop backend
postgres_id="`$(docker compose -p $projectShell ps -q postgres)"
test -n "`$postgres_id"
revision="`$(docker compose -p $projectShell exec -T postgres psql -U shamrai -d shamrai -At -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version')"
normalized_schema() {
  docker compose -p $projectShell exec -T postgres pg_dump -U shamrai -d shamrai --schema-only --no-owner --no-privileges | sed -E '/^\\(un)?restrict /d'
}
canonical_schema() {
  normalized_schema | sed -E \
    -e "s/\('([^']*)'::character varying\)::text/'\1'/g" \
    -e "s/'([^']*)'::character varying/'\1'/g" \
    -e 's/\(ARRAY\[([^]]*)\]\)::text\[\]/ARRAY[\1]/g'
}
normalized_schema_hash="`$(canonical_schema | sha256sum | awk '{print `$1}')"
printf 'POSTGRES_IMAGE=%s\n' "`$(docker inspect -f '{{.Config.Image}}' "`$postgres_id")"
printf 'REVISION=%s\n' "`$revision"
printf 'NORMALIZED_SCHEMA_HASH=%s\n' "`$normalized_schema_hash"
$tableSql
"@

try {
  # Fail-safe restart applies even when the remote baseline command fails after stopping the backend.
  $backendStopped = $true
  $baseline = ConvertFrom-KeyValueOutput (Invoke-RemoteOutputChecked $baselineCommand)
  foreach ($requiredKey in @("POSTGRES_IMAGE", "REVISION", "NORMALIZED_SCHEMA_HASH")) {
    if (-not $baseline.ContainsKey($requiredKey) -or [string]::IsNullOrWhiteSpace($baseline[$requiredKey])) {
      throw "Backup baseline is missing $requiredKey."
    }
  }
  if ($baseline.REVISION -notmatch '^20[0-9]{6}_[0-9]{4}$' -or $baseline.NORMALIZED_SCHEMA_HASH -notmatch '^[0-9a-f]{64}$') {
    throw "Backup baseline revision or schema hash is invalid."
  }
  foreach ($table in $ProtectedTables) {
    $key = "COUNT_$($table.ToUpperInvariant())"
    if (-not $baseline.ContainsKey($key) -or $baseline[$key] -notmatch '^\d+$') { throw "Backup baseline is missing $key." }
  }

  $remoteDump = "cd $((ConvertTo-ShellSingleQuoted $RemotePath)) && docker compose -p $projectShell exec -T postgres pg_dump -U shamrai -d shamrai -Fc"
  try {
    Invoke-BinaryPipeline `
      -SourceFile $ssh `
      -SourceArguments @($sshArguments + @($Server, $remoteDump)) `
      -SinkFile $age `
      -SinkArguments @("-r", $recipient, "-o", $backupPath)
  } catch {
    Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
    throw
  }
  if (-not (Test-Path -LiteralPath $backupPath -PathType Leaf) -or (Get-Item -LiteralPath $backupPath).Length -lt 1024) {
    throw "Encrypted production dump is missing or unexpectedly small."
  }
  Set-OwnerOnlyLocalPath -Path $backupPath -Directory $false

  $containerShell = ConvertTo-ShellSingleQuoted $restoreContainer
  $volumeShell = ConvertTo-ShellSingleQuoted $restoreVolume
  $mountShell = ConvertTo-ShellSingleQuoted "${restoreVolume}:/var/lib/postgresql/data"
  $imageShell = ConvertTo-ShellSingleQuoted $baseline.POSTGRES_IMAGE
  $restoreSetup = @"
set -Eeuo pipefail
docker rm -f $containerShell >/dev/null 2>&1 || true
docker volume rm $volumeShell >/dev/null 2>&1 || true
docker volume create $volumeShell >/dev/null
docker run -d --name $containerShell --network none -e POSTGRES_DB=shamrai_restore -e POSTGRES_USER=shamrai_restore -e POSTGRES_PASSWORD=restore_drill_password -v $mountShell $imageShell >/dev/null
for attempt in {1..60}; do
  if docker exec $containerShell pg_isready -U shamrai_restore -d shamrai_restore >/dev/null 2>&1; then
    exit 0
  fi
  sleep 2
done
echo 'Isolated restore PostgreSQL did not become ready.' >&2
exit 91
"@
  Invoke-RemoteChecked $restoreSetup
  $restoreCreated = $true

  $remoteRestore = "docker exec -i $containerShell pg_restore --no-owner --role=shamrai_restore --exit-on-error -U shamrai_restore -d shamrai_restore"
  Invoke-BinaryPipeline `
    -SourceFile $age `
    -SourceArguments @("-d", "-i", $AgeIdentityPath, $backupPath) `
    -SinkFile $ssh `
    -SinkArguments @($sshArguments + @($Server, $remoteRestore))

  $restoreTableSql = ($ProtectedTables | ForEach-Object {
    "printf 'COUNT_$($_.ToUpperInvariant())=%s\n' `"`$(docker exec $containerShell psql -U shamrai_restore -d shamrai_restore -At -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM $($_)')`""
  }) -join "`n"
  $verifyCommand = @"
set -Eeuo pipefail
revision="`$(docker exec $containerShell psql -U shamrai_restore -d shamrai_restore -At -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version')"
normalized_schema() {
  docker exec $containerShell pg_dump -U shamrai_restore -d shamrai_restore --schema-only --no-owner --no-privileges | sed -E '/^\\(un)?restrict /d'
}
canonical_schema() {
  normalized_schema | sed -E \
    -e "s/\('([^']*)'::character varying\)::text/'\1'/g" \
    -e "s/'([^']*)'::character varying/'\1'/g" \
    -e 's/\(ARRAY\[([^]]*)\]\)::text\[\]/ARRAY[\1]/g'
}
normalized_schema_hash="`$(canonical_schema | sha256sum | awk '{print `$1}')"
printf 'REVISION=%s\n' "`$revision"
printf 'NORMALIZED_SCHEMA_HASH=%s\n' "`$normalized_schema_hash"
$restoreTableSql
"@
  $restored = ConvertFrom-KeyValueOutput (Invoke-RemoteOutputChecked $verifyCommand)
  if ($restored.REVISION -cne $baseline.REVISION -or
      $restored.NORMALIZED_SCHEMA_HASH -cne $baseline.NORMALIZED_SCHEMA_HASH) {
    throw "Restored backup revision or schema hash differs from production."
  }
  foreach ($table in $ProtectedTables) {
    $key = "COUNT_$($table.ToUpperInvariant())"
    if ($restored[$key] -cne $baseline[$key]) { throw "Restored backup row count differs for $table." }
  }

  $manifest = [ordered]@{
    schema_version = 1
    status = "passed"
    verified_at = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    artifact = [IO.Path]::GetFileName($backupPath)
    encrypted_sha256 = (Get-FileHash -LiteralPath $backupPath -Algorithm SHA256).Hash.ToLowerInvariant()
    encrypted_size_bytes = (Get-Item -LiteralPath $backupPath).Length
    alembic_revision = $baseline.REVISION
    normalized_schema_hash = $baseline.NORMALIZED_SCHEMA_HASH
    table_counts = [ordered]@{}
    plaintext_dump_persisted = $false
    isolated_restore = $true
  }
  foreach ($table in $ProtectedTables) { $manifest.table_counts[$table] = [int64]$baseline["COUNT_$($table.ToUpperInvariant())"] }
  $manifestJson = ($manifest | ConvertTo-Json -Depth 5) + "`n"
  [IO.File]::WriteAllText($manifestPath, $manifestJson, [Text.UTF8Encoding]::new($false))
  Set-OwnerOnlyLocalPath -Path $manifestPath -Directory $false
  $attestationTemporary = "$AttestationPath.tmp"
  [IO.File]::WriteAllText($attestationTemporary, $manifestJson, [Text.UTF8Encoding]::new($false))
  Set-OwnerOnlyLocalPath -Path $attestationTemporary -Directory $false
  Move-Item -LiteralPath $attestationTemporary -Destination $AttestationPath -Force
  $succeeded = $true
  Write-Host "release_backup_artifact=$backupPath"
  Write-Host "release_backup_manifest=$manifestPath"
  Write-Host "release_backup_restore=passed"
} finally {
  if ($restoreCreated) {
    try {
      Invoke-RemoteChecked "docker rm -f $containerShell >/dev/null 2>&1 || true; docker volume rm $volumeShell >/dev/null 2>&1 || true"
    } catch {
      Write-Warning "Temporary restore resource cleanup failed."
    }
  }
  if ($backendStopped -and (-not $succeeded -or -not $LeaveBackendStopped)) {
    try {
      $restart = "cd $((ConvertTo-ShellSingleQuoted $RemotePath)) && docker compose -p $projectShell up -d --no-deps backend && for attempt in {1..60}; do if curl -fsS http://127.0.0.1:8082/api/health >/dev/null 2>&1; then exit 0; fi; sleep 2; done; exit 92"
      Invoke-RemoteChecked $restart
    } catch {
      throw "Backend restart after release backup verification failed: $($_.Exception.Message)"
    }
  }
}

if (-not $succeeded) { throw "Release backup verification did not complete." }
