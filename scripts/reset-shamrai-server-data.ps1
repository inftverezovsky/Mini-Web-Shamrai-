param(
  [string]$Server = "root@82.147.67.245",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [string]$HealthUrl = "http://127.0.0.1:8082/api/health",
  [string]$SshKeyPath = $env:SHAMRAI_SSH_KEY_PATH,
  [switch]$Execute,
  [switch]$NoSeed
)

$ErrorActionPreference = "Stop"

function Find-Tool {
  param([string[]]$Candidates)
  foreach ($candidate in $Candidates) {
    if (Test-Path $candidate) { return $candidate }
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
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
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
  )

  & $FilePath @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $FilePath"
  }
}

function Invoke-RemoteSh {
  param([Parameter(Mandatory = $true)][string]$Script)

  $normalizedScript = ((($Script -replace "`r`n", "`n") -replace "`r", "").TrimEnd("`n")) + "`n"
  $encodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($normalizedScript))
  $remoteCommand = "printf '%s' '$encodedScript' | base64 -d | bash"
  Invoke-NativeChecked $ssh `
    "-i" $SshKeyPath `
    "-o" "BatchMode=yes" `
    "-o" "IdentitiesOnly=yes" `
    "-o" "StrictHostKeyChecking=accept-new" `
    $Server `
    $remoteCommand
}

$defaultSshKeyPath = Join-Path $env:USERPROFILE ".ssh\codex_deploy_ed25519"
if ([string]::IsNullOrWhiteSpace($SshKeyPath) -and (Test-Path -LiteralPath $defaultSshKeyPath)) {
  $SshKeyPath = $defaultSshKeyPath
}

if ([string]::IsNullOrWhiteSpace($SshKeyPath) -or -not (Test-Path -LiteralPath $SshKeyPath)) {
  throw "SSH key not found. Set SHAMRAI_SSH_KEY_PATH or create $defaultSshKeyPath."
}

$ssh = Find-Tool @("ssh.exe", "ssh")
if (-not $ssh) {
  throw "OpenSSH ssh not found. Install OpenSSH Client."
}

if (-not $Execute) {
  Write-Host "Dry-run only. Add -Execute to delete server data." -ForegroundColor Yellow
}

Invoke-Step "Server port/project guard" {
  $guard = @"
set -e
cd '$RemotePath'
echo 'Shamrai server inventory:'
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
echo 'Compose labels:'
docker ps -aq | while read -r id; do
  [ -n "`$id" ] || continue
  name="`$(docker inspect -f '{{.Name}}' "`$id" 2>/dev/null | sed 's#^/##' || true)"
  project="`$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "`$id" 2>/dev/null || true)"
  workdir="`$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "`$id" 2>/dev/null || true)"
  printf '%s\tproject=%s\tworkdir=%s\n' "`$name" "`$project" "`$workdir"
done
echo 'Listening ports 80/443/8000/8081/8082:'
ss -ltnp | grep -E ':(80|443|8000|8081|8082)\b' || true
if [ ! -d '$RemotePath' ]; then
  echo 'Canonical Shamrai path is missing: $RemotePath' >&2
  exit 21
fi
conflicts="`$(docker ps --format '{{.Names}}\t{{.Ports}}\t{{.Label "com.docker.compose.project"}}' | awk -F '\t' 'index(`$2, ":8082->") && `$3 != "shamrai" { print }')"
if [ -n "`$conflicts" ]; then
  echo 'Port 8082 is owned by a non-canonical container.' >&2
  echo "`$conflicts" >&2
  exit 20
fi
"@
  Invoke-RemoteSh -Script $guard
}

$executeFlag = if ($Execute) { "--execute" } else { "" }
$seedFlag = if ($NoSeed) { "--no-seed" } else { "" }
$backupBlock = if ($Execute) {
  @"
backup_dir='$RemotePath/db-backups'
backup_path="`$backup_dir/pre-reset-`$(date +%Y%m%d%H%M%S).dump"
mkdir -p "`$backup_dir"
chmod 700 "`$backup_dir"
docker compose -p '$ComposeProject' exec -T postgres sh -c 'pg_dump -U "`$POSTGRES_USER" -d "`$POSTGRES_DB" -Fc' > "`$backup_path"
chmod 600 "`$backup_path"
echo "DB_BACKUP_CREATED=`$backup_path"
"@
} else {
  ""
}

Invoke-Step "Server database reset $(if ($Execute) { 'EXECUTE' } else { 'DRY-RUN' })" {
  $remote = @"
set -e
cd '$RemotePath'
$backupBlock
docker compose -p '$ComposeProject' exec -T backend python -m src.scripts.reset_app_data $executeFlag $seedFlag < /dev/null
if [ '$Execute' = 'True' ]; then
  docker compose -p '$ComposeProject' exec -T backend sh -lc 'find /app/static/coupons -type f -delete 2>/dev/null || true' < /dev/null
fi
docker compose -p '$ComposeProject' ps
curl -fsS '$HealthUrl'
"@
  Invoke-RemoteSh -Script $remote
}

Write-Host ""
Write-Host "Shamrai server reset finished: execute=$Execute" -ForegroundColor Green
