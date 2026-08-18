param(
  [string]$Workspace = "C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)",
  [string]$Server = "root@82.147.67.245",
  [string]$HostKey = "ssh-ed25519 255 SHA256:xdVRtRaXWqK6eAIsE3VwD0o2H6GJDcCm65L1ZUBjuMw",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [string]$PublicWebRoot = "/var/www/shamrai_web/dist",
  [string]$VkGroupId = "239419819",
  [string]$ExpectedVkCallbackConfirmationCode = $env:SHAMRAI_EXPECTED_VK_CALLBACK_CONFIRMATION_CODE,
  [switch]$SkipLocalChecks,
  [switch]$RepairShamraiConflicts,
  [string]$SshKeyPath = $env:SHAMRAI_SSH_KEY_PATH,
  [string]$S3RetentionAttestationPath = "",
  [string]$RestoreAttestationPath = "",
  [string]$CiAttestationPath = "",
  [string]$HiddenFlatAttestationPath = "",
  [switch]$LocalEncryptedBackupGate,
  [switch]$PreviewOnly,
  [switch]$PromptPassword,
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($LocalEncryptedBackupGate -and $PSVersionTable.PSVersion.Major -lt 7) {
  throw "LocalEncryptedBackupGate requires PowerShell 7 or newer for binary-safe backup streaming."
}

function Invoke-Step {
  param([string]$Title, [scriptblock]$Script)
  Write-Host ""
  Write-Host "==> $Title" -ForegroundColor Cyan
  & $Script
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

function New-FencedRemoteCommand {
  param(
    [Parameter(Mandatory = $true)][string]$Command,
    [Parameter(Mandatory = $true)][string]$OwnerToken,
    [switch]$InitializeOwner,
    [switch]$ReleaseOwner
  )

  if ($OwnerToken -notmatch '^shamrai-public-release\.[A-Za-z0-9]{6}$') {
    throw "Release owner token has an invalid shape."
  }
  $ownerTokenShell = ConvertTo-ShellSingleQuoted $OwnerToken
  $body = @"
set -Eeuo pipefail
umask 077
owner_file=/run/lock/shamrai-public-deploy.owner
expected_owner=$ownerTokenShell
"@
  if ($InitializeOwner) {
    $body += @'

if [ -e "$owner_file" ] || [ -L "$owner_file" ]; then
  echo 'Another Shamrai release lifecycle is active or requires manual recovery.' >&2
  exit 76
fi
owner_tmp="$(mktemp /run/lock/.shamrai-public-deploy.owner.XXXXXX)"
printf '%s\n' "$expected_owner" > "$owner_tmp"
chown root:root "$owner_tmp"
chmod 0600 "$owner_tmp"
mv -fT "$owner_tmp" "$owner_file"
'@
  } else {
    $body += @'

if [ -L "$owner_file" ] || [ ! -f "$owner_file" ] || [ "$(stat -c '%u:%a' -- "$owner_file")" != '0:600' ]; then
  echo 'Shamrai release ownership fence is missing or unsafe.' >&2
  exit 73
fi
if [ "$(cat "$owner_file")" != "$expected_owner" ]; then
  echo 'Shamrai release was superseded by another deployment; refusing stale lifecycle action.' >&2
  exit 74
fi
'@
  }
  $body += "`n" + $Command.Trim() + "`n"
  if ($ReleaseOwner) {
    $body += @'
if [ "$(cat "$owner_file")" != "$expected_owner" ]; then
  echo 'Shamrai release ownership changed before lifecycle completion.' >&2
  exit 75
fi
rm -f -- "$owner_file"
'@
  }
  $normalizedBody = (($body -replace "`r`n", "`n") -replace "`r", "")
  return "flock -n /run/lock/shamrai-public-deploy.lock bash -c " +
    (ConvertTo-ShellSingleQuoted $normalizedBody)
}

function Find-Tool {
  param([string[]]$Candidates)
  foreach ($candidate in $Candidates) {
    if (Test-Path $candidate) { return $candidate }
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
  }
  return $null
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

function Get-FrontendDistAssets {
  param([string]$DistPath)

  $indexPath = Join-Path $DistPath "index.html"
  if (-not (Test-Path -LiteralPath $indexPath)) {
    throw "Frontend dist index was not found: $indexPath"
  }

  $html = Get-Content -Raw -LiteralPath $indexPath
  $matches = [regex]::Matches($html, "/?assets/[^`"'<>\s]+\.(?:css|js)")
  $assets = @(
    foreach ($match in $matches) {
      $match.Value.TrimStart("/")
    }
  ) | Sort-Object -Unique

  if ($assets.Count -eq 0) {
    throw "Frontend dist index has no CSS/JS assets: $indexPath"
  }

  return $assets
}

function Get-ShamraiSshPassword {
  param([bool]$ForcePrompt = $false)

  $passwordFromEnv = $env:SHAMRAI_SSH_PASSWORD
  if (-not $ForcePrompt -and -not [string]::IsNullOrWhiteSpace($passwordFromEnv)) {
    return $passwordFromEnv
  }

  $securePassword = Read-Host "VDS password" -AsSecureString
  if ($securePassword.Length -eq 0) {
    throw "VDS password was empty."
  }

  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
  try {
    return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
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

function New-ServerGuardScript {
  param([bool]$Repair)

  $repairValue = if ($Repair) { "1" } else { "0" }
  $template = @'
set -e
CANON_PROJECT='__COMPOSE_PROJECT__'
CANON_PATH='__REMOTE_PATH__'
CANON_PORT='8082'
REPAIR_SHAMRAI_CONFLICTS='__REPAIR__'

echo 'Shamrai server inventory:'
docker ps -a --format 'table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}' || true
echo 'Compose labels:'
docker ps -aq | while read -r id; do
  [ -n "$id" ] || continue
  name="$(docker inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's#^/##' || true)"
  labels_json="$(docker inspect -f '{{json .Config.Labels}}' "$id" 2>/dev/null || echo '{}')"
  project="$(printf '%s' "$labels_json" | python3 -c "import json,sys; print((json.load(sys.stdin) or {}).get('com.docker.compose.project', ''))" 2>/dev/null || true)"
  workdir="$(printf '%s' "$labels_json" | python3 -c "import json,sys; print((json.load(sys.stdin) or {}).get('com.docker.compose.project.working_dir', ''))" 2>/dev/null || true)"
  printf '%s\tproject=%s\tworkdir=%s\n' "$name" "$project" "$workdir"
done
echo 'Listening ports 80/443/8000/8081/8082:'
ss -ltnp | grep -E ':(80|443|8000|8081|8082)\b' || true

find_compose_workdir() {
  target_project="$1"
  docker ps -aq --filter "label=com.docker.compose.project=$target_project" | while read -r id; do
    [ -n "$id" ] || continue
    labels_json="$(docker inspect -f '{{json .Config.Labels}}' "$id" 2>/dev/null || echo '{}')"
    workdir="$(printf '%s' "$labels_json" | python3 -c "import json,sys; print((json.load(sys.stdin) or {}).get('com.docker.compose.project.working_dir', ''))" 2>/dev/null || true)"
    if [ -n "$workdir" ]; then
      printf '%s\n' "$workdir"
      break
    fi
  done
}

stop_stale_compose_project() {
  stale_project="$1"
  if [ "$stale_project" = "$CANON_PROJECT" ]; then
    return 0
  fi
  workdir="$(find_compose_workdir "$stale_project" | head -n 1)"
  if [ -n "$workdir" ] && [ -d "$workdir" ]; then
    echo "Stopping stale compose project $stale_project from $workdir"
    docker compose --project-directory "$workdir" -p "$stale_project" down --remove-orphans || true
    return 0
  fi
  docker compose -p "$stale_project" down --remove-orphans || true
}

if [ "$REPAIR_SHAMRAI_CONFLICTS" = "1" ]; then
  for project in sports-betting shamrai-preview shamrai-mini shamrai-mini-app mini-web mini-web-shamrai; do
    stop_stale_compose_project "$project"
  done

  docker ps -aq | while read -r id; do
    [ -n "$id" ] || continue
    name="$(docker inspect -f '{{.Name}}' "$id" 2>/dev/null | sed 's#^/##' || true)"
    labels_json="$(docker inspect -f '{{json .Config.Labels}}' "$id" 2>/dev/null || echo '{}')"
    project="$(printf '%s' "$labels_json" | python3 -c "import json,sys; print((json.load(sys.stdin) or {}).get('com.docker.compose.project', ''))" 2>/dev/null || true)"
    if [ "$project" = "$CANON_PROJECT" ]; then
      continue
    fi
    case "$name:$project" in
      *shamrai*|*sports-betting*|*mini-web*|*Mini-Web*)
        echo "Removing stale Shamrai-related container: $name project=$project"
        docker rm -f "$id" || true
        ;;
    esac
  done
fi

conflicts="$(
  docker ps --format '{{.ID}}\t{{.Names}}\t{{.Ports}}' | while IFS="$(printf '\t')" read -r id name ports; do
    labels_json="$(docker inspect -f '{{json .Config.Labels}}' "$id" 2>/dev/null || echo '{}')"
    project="$(printf '%s' "$labels_json" | python3 -c "import json,sys; print((json.load(sys.stdin) or {}).get('com.docker.compose.project', ''))" 2>/dev/null || true)"
    case "$ports" in
      *":$CANON_PORT->"*)
        if [ "$project" != "$CANON_PROJECT" ]; then
          printf '%s\t%s\t%s\t%s\n' "$id" "$name" "$ports" "$project"
        fi
        ;;
    esac
  done
)"
if [ -n "$conflicts" ]; then
  echo "Port $CANON_PORT is owned by a non-canonical container. Do not deploy Shamrai to a new port." >&2
  echo "$conflicts" >&2
  echo "Rerun with -RepairShamraiConflicts only if those containers are stale Shamrai/sports-betting deployments." >&2
  exit 20
fi

listeners_8082="$(ss -ltnp | awk -v port="$CANON_PORT" 'NR > 1 { n=split($4, parts, ":"); if (parts[n] == port) print }' || true)"
canonical_8082="$(
  docker ps --filter "label=com.docker.compose.project=$CANON_PROJECT" --format '{{.Ports}}' | grep -F ":$CANON_PORT->" || true
)"
if [ -n "$listeners_8082" ] && [ -z "$canonical_8082" ]; then
  echo "Port $CANON_PORT already has a listener, but it is not owned by the canonical Docker project." >&2
  echo "$listeners_8082" >&2
  exit 21
fi

if [ -f "$CANON_PATH/.env" ]; then
  current_port="$(grep -E '^FRONTEND_PORT=' "$CANON_PATH/.env" | tail -n 1 | cut -d= -f2- || true)"
  if [ -n "$current_port" ] && [ "$current_port" != "$CANON_PORT" ]; then
    echo "Canonical preview .env had FRONTEND_PORT=$current_port; forcing FRONTEND_PORT=$CANON_PORT."
    python3 - <<'PY'
from pathlib import Path
p = Path("__REMOTE_PATH__/.env")
lines = p.read_text().splitlines() if p.exists() else []
out = []
seen = False
for line in lines:
    if line.startswith("FRONTEND_PORT="):
        out.append("FRONTEND_PORT=8082")
        seen = True
    else:
        out.append(line)
if not seen:
    out.append("FRONTEND_PORT=8082")
p.write_text("\n".join(out) + "\n")
PY
  fi
fi
'@

  return $template.
    Replace("__COMPOSE_PROJECT__", $ComposeProject).
    Replace("__REMOTE_PATH__", $RemotePath).
    Replace("__REPAIR__", $repairValue)
}

if (-not (Test-Path $Workspace)) {
  throw "Workspace not found: $Workspace"
}

function Invoke-WithSanitizedReleaseEnvironment {
  param([Parameter(Mandatory = $true)][scriptblock]$Script)

  $sensitivePrefixPattern = '^(?i:(?:AWS|AZURE|CLOUDFLARE|DATABASE|DOCKER|GCLOUD|GITHUB|GOOGLE|NPM|PG|POSTGRES|REDIS|SENTRY|SHAMRAI|SSH|STRIPE|TEGRO|TELEGRAM|VITE|VK|YOOKASSA)_.*)$'
  $sensitiveNamePattern = '(?i)(?:^|_)(?:ACCESS_KEY(?:_ID)?|API_KEY|AUTH(?:ORIZATION)?|COOKIE|CREDENTIALS?|DATABASE_URL|PASS(?:WORD)?|PRIVATE_KEY|PROXY|SECRET|SESSION_TOKEN|TOKEN)(?:$|_)'
  $executionControlPattern = '^(?i:(?:ANALYZE|BASH_ENV|ENV|NODE_OPTIONS|PYTHONHOME|PYTHONPATH|PYTHONSTARTUP|SOURCE_DATE_EPOCH))$'
  $savedEnvironment = @{}
  $sensitiveEntries = @(
    Get-ChildItem Env: | Where-Object {
      $_.Name -match $sensitivePrefixPattern -or
        $_.Name -match $sensitiveNamePattern -or
        $_.Name -match $executionControlPattern
    }
  )

  foreach ($entry in $sensitiveEntries) {
    $savedEnvironment[$entry.Name] = $entry.Value
    Remove-Item -LiteralPath "Env:$($entry.Name)" -ErrorAction SilentlyContinue
  }

  try {
    & $Script
  } finally {
    $currentSensitiveEntries = @(
      Get-ChildItem Env: | Where-Object {
        $_.Name -match $sensitivePrefixPattern -or
          $_.Name -match $sensitiveNamePattern -or
          $_.Name -match $executionControlPattern
      }
    )
    foreach ($entry in $currentSensitiveEntries) {
      Remove-Item -LiteralPath "Env:$($entry.Name)" -ErrorAction SilentlyContinue
    }
    foreach ($name in $savedEnvironment.Keys) {
      Set-Item -LiteralPath "Env:$name" -Value $savedEnvironment[$name]
    }
  }
}

$canonicalServer = "root@82.147.67.245"
$canonicalRemotePath = "/opt/shamrai-mini-app"
$canonicalComposeProject = "shamrai"
$canonicalPublicWebRoot = "/var/www/shamrai_web/dist"
if (
  $Server -cne $canonicalServer -or
  $RemotePath -cne $canonicalRemotePath -or
  $ComposeProject -cne $canonicalComposeProject -or
  $PublicWebRoot -cne $canonicalPublicWebRoot
) {
  throw "This Shamrai deploy script only accepts the canonical server, app path, Compose project, and public web root."
}

$localDeployDirectory = Join-Path $Workspace ".deploy"
New-Item -ItemType Directory -Force -Path $localDeployDirectory | Out-Null
$localDeployLockPath = Join-Path $localDeployDirectory "shamrai-public-deploy.local.lock"
try {
  $localDeployLockStream = [IO.File]::Open(
    $localDeployLockPath,
    [IO.FileMode]::OpenOrCreate,
    [IO.FileAccess]::ReadWrite,
    [IO.FileShare]::None
  )
} catch {
  throw "Another local Shamrai release process is already using this workspace."
}

try {

function Get-RequiredJsonProperty {
  param([object]$Object, [string]$Name)
  $property = $Object.PSObject.Properties[$Name]
  if ($null -eq $property) {
    throw "Release gate attestation is missing '$Name'."
  }
  return $property.Value
}

function Assert-FreshUtcTimestamp {
  param([string]$Value, [string]$Name, [int]$MaximumAgeHours)
  $parsed = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParse($Value, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeUniversal, [ref]$parsed)) {
    throw "Release gate timestamp '$Name' is invalid."
  }
  $now = [DateTimeOffset]::UtcNow
  if ($parsed -gt $now.AddMinutes(10) -or $parsed -lt $now.AddHours(-$MaximumAgeHours)) {
    throw "Release gate timestamp '$Name' is outside the allowed $MaximumAgeHours-hour window."
  }
  return $parsed.ToUniversalTime()
}

function Assert-AttestationSchemaVersion {
  param([object]$Payload, [string]$Name, [int]$ExpectedVersion = 1)
  if ([int](Get-RequiredJsonProperty $Payload "schema_version") -ne $ExpectedVersion) {
    throw "Release gate attestation '$Name' has an unsupported schema version."
  }
}

function Read-ReleaseGateBootstrap {
  param([string]$Path)

  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Required release gate bootstrap was not found: $Path"
  }
  $item = Get-Item -LiteralPath $Path -Force
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.Length -lt 2 -or $item.Length -gt 1048576) {
    throw "Release gate bootstrap must be a regular JSON file no larger than 1 MiB: $Path"
  }
  return Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
}

function Get-GitHubApiHeaders {
  $token = [string]$env:GITHUB_PERSONAL_ACCESS_TOKEN
  if ([string]::IsNullOrWhiteSpace($token) -or $token.Length -gt 512 -or $token -match '\s') {
    throw "GITHUB_PERSONAL_ACCESS_TOKEN is required at runtime to authenticate release evidence downloads."
  }
  return @{
    Accept = "application/vnd.github+json"
    Authorization = "Bearer $token"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent" = "shamrai-release-gate"
  }
}

function Invoke-GitHubApiJson {
  param([string]$Uri, [hashtable]$Headers)

  try {
    return Invoke-RestMethod -Method Get -Uri $Uri -Headers $Headers -TimeoutSec 30
  } catch {
    throw "Authenticated GitHub release evidence request failed."
  }
}

function Assert-GitHubWorkflowRun {
  param(
    [object]$Run,
    [string]$ExpectedRepository,
    [string]$ExpectedReleaseSha,
    [string]$ExpectedWorkflowName,
    [string]$ExpectedWorkflowPath,
    [string[]]$AllowedEvents,
    [int64]$ExpectedRunId,
    [int]$ExpectedRunAttempt
  )

  if ([int64](Get-RequiredJsonProperty $Run "id") -ne $ExpectedRunId -or
      [string](Get-RequiredJsonProperty $Run "head_sha") -cne $ExpectedReleaseSha -or
      [string](Get-RequiredJsonProperty $Run "name") -cne $ExpectedWorkflowName -or
      [string](Get-RequiredJsonProperty $Run "status") -cne "completed" -or
      [string](Get-RequiredJsonProperty $Run "conclusion") -cne "success" -or
      [int](Get-RequiredJsonProperty $Run "run_attempt") -ne $ExpectedRunAttempt) {
    throw "Authenticated GitHub workflow run does not match the required successful release run."
  }
  if ([string](Get-RequiredJsonProperty (Get-RequiredJsonProperty $Run "repository") "full_name") -cne $ExpectedRepository -or
      [string](Get-RequiredJsonProperty (Get-RequiredJsonProperty $Run "head_repository") "full_name") -cne $ExpectedRepository) {
    throw "Authenticated GitHub workflow run belongs to a different repository or fork."
  }
  if ([string](Get-RequiredJsonProperty $Run "event") -notin $AllowedEvents) {
    throw "Authenticated GitHub workflow run used an unsupported trigger."
  }
  $workflowPath = [string](Get-RequiredJsonProperty $Run "path")
  if ($workflowPath -cne $ExpectedWorkflowPath -and -not $workflowPath.StartsWith("$ExpectedWorkflowPath@", [StringComparison]::Ordinal)) {
    throw "Authenticated GitHub workflow run used an unexpected workflow file."
  }
}

function Get-AuthenticatedGitHubArtifactJson {
  param(
    [object]$Bootstrap,
    [string]$ExpectedRepository,
    [string]$ExpectedReleaseSha,
    [string]$ExpectedWorkflowName,
    [string]$ExpectedWorkflowPath,
    [string[]]$AllowedEvents,
    [string]$ArtifactName,
    [string]$ExpectedFileName
  )

  $runId = [int64](Get-RequiredJsonProperty $Bootstrap "workflow_run_id")
  $runAttempt = [int](Get-RequiredJsonProperty $Bootstrap "workflow_run_attempt")
  if ($runId -lt 1 -or $runAttempt -lt 1) {
    throw "Release gate bootstrap has invalid GitHub workflow identity."
  }
  $headers = Get-GitHubApiHeaders
  $apiRoot = "https://api.github.com/repos/$ExpectedRepository"
  $run = Invoke-GitHubApiJson -Uri "$apiRoot/actions/runs/$runId" -Headers $headers
  Assert-GitHubWorkflowRun `
    -Run $run `
    -ExpectedRepository $ExpectedRepository `
    -ExpectedReleaseSha $ExpectedReleaseSha `
    -ExpectedWorkflowName $ExpectedWorkflowName `
    -ExpectedWorkflowPath $ExpectedWorkflowPath `
    -AllowedEvents $AllowedEvents `
    -ExpectedRunId $runId `
    -ExpectedRunAttempt $runAttempt

  $artifactList = Invoke-GitHubApiJson -Uri "$apiRoot/actions/runs/$runId/artifacts?per_page=100" -Headers $headers
  $matches = @((Get-RequiredJsonProperty $artifactList "artifacts") | Where-Object {
    [string](Get-RequiredJsonProperty $_ "name") -ceq $ArtifactName -and
    -not [bool](Get-RequiredJsonProperty $_ "expired")
  })
  if ($matches.Count -ne 1) {
    throw "Authenticated GitHub run does not contain exactly one unexpired '$ArtifactName' artifact."
  }
  $artifactId = [int64](Get-RequiredJsonProperty $matches[0] "id")
  if ($artifactId -lt 1) {
    throw "Authenticated GitHub artifact id is invalid."
  }

  $zipPath = Join-Path ([IO.Path]::GetTempPath()) ("shamrai-release-evidence-{0}.zip" -f [Guid]::NewGuid().ToString("N"))
  try {
    try {
      Invoke-WebRequest `
        -Method Get `
        -Uri "$apiRoot/actions/artifacts/$artifactId/zip" `
        -Headers $headers `
        -OutFile $zipPath `
        -MaximumRedirection 5 `
        -TimeoutSec 60 | Out-Null
    } catch {
      throw "Authenticated GitHub release artifact download failed."
    }
    $zipItem = Get-Item -LiteralPath $zipPath -Force
    if ($zipItem.Length -lt 22 -or $zipItem.Length -gt 10485760) {
      throw "Authenticated GitHub release artifact has an invalid archive size."
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zipPath)
    try {
      $entries = @($archive.Entries | Where-Object { $_.Name -ceq $ExpectedFileName })
      if ($entries.Count -ne 1 -or $entries[0].Length -lt 2 -or $entries[0].Length -gt 1048576) {
        throw "Authenticated GitHub release artifact does not contain one valid '$ExpectedFileName'."
      }
      $stream = $entries[0].Open()
      $reader = [IO.StreamReader]::new($stream, [Text.UTF8Encoding]::new($false), $true)
      try {
        return ($reader.ReadToEnd() | ConvertFrom-Json)
      } finally {
        $reader.Dispose()
        $stream.Dispose()
      }
    } finally {
      $archive.Dispose()
    }
  } finally {
    if (Test-Path -LiteralPath $zipPath -PathType Leaf) {
      Remove-Item -LiteralPath $zipPath -Force
    }
  }
}

function Assert-CiReleaseGateAttestation {
  param(
    [string]$CiPath,
    [string]$ExpectedReleaseSha,
    [string]$ExpectedRepository
  )

  if (-not (Test-Path -LiteralPath $CiPath -PathType Leaf)) {
    throw "Required CI release gate attestation was not found: $CiPath"
  }
  $bootstrap = Read-ReleaseGateBootstrap $CiPath
  $runId = [int64](Get-RequiredJsonProperty $bootstrap "workflow_run_id")
  $runAttempt = [int](Get-RequiredJsonProperty $bootstrap "workflow_run_attempt")
  $ci = Get-AuthenticatedGitHubArtifactJson `
    -Bootstrap $bootstrap `
    -ExpectedRepository $ExpectedRepository `
    -ExpectedReleaseSha $ExpectedReleaseSha `
    -ExpectedWorkflowName "CI" `
    -ExpectedWorkflowPath ".github/workflows/ci.yml" `
    -AllowedEvents @("push") `
    -ArtifactName "ci-release-attestation-$ExpectedReleaseSha" `
    -ExpectedFileName "ci-release-attestation.json"
  Assert-AttestationSchemaVersion $ci "ci-release"
  if ([string](Get-RequiredJsonProperty $ci "repository") -cne $ExpectedRepository -or
      [string](Get-RequiredJsonProperty $ci "git_sha") -cne $ExpectedReleaseSha -or
      [int64](Get-RequiredJsonProperty $ci "workflow_run_id") -ne $runId -or
      [int](Get-RequiredJsonProperty $ci "workflow_run_attempt") -ne $runAttempt) {
    throw "Authenticated CI release evidence has inconsistent workflow provenance."
  }
  if ([string](Get-RequiredJsonProperty $ci "status") -cne "passed") {
    throw "CI release attestation did not pass."
  }
  $ciVerifiedAt = Assert-FreshUtcTimestamp ([string](Get-RequiredJsonProperty $ci "verified_at")) "ci.verified_at" 168
  if ((Get-RequiredJsonProperty $ci "ref_protected") -isnot [bool] -or
      -not [bool](Get-RequiredJsonProperty $ci "ref_protected")) {
    throw "CI release attestation must come from a protected branch."
  }
  if ([string](Get-RequiredJsonProperty $ci "workflow") -cne "CI" -or
      [string](Get-RequiredJsonProperty $ci "event_name") -cne "push" -or
      [string](Get-RequiredJsonProperty $ci "ref") -cne "refs/heads/main" -or
      [int64](Get-RequiredJsonProperty $ci "run_id") -ne $runId -or
      [int](Get-RequiredJsonProperty $ci "run_attempt") -ne $runAttempt) {
    throw "CI release attestation must come from the exact protected main-branch push run."
  }
  $requiredCiJobs = @(
    "backend", "flat-coverage", "frontend", "secret-scan", "docker-build",
    "alembic-smoke", "postgres-concurrency", "compose", "powershell"
  )
  $jobResults = Get-RequiredJsonProperty $ci "job_results"
  if (@($jobResults.PSObject.Properties).Count -ne $requiredCiJobs.Count) {
    throw "CI release attestation has an unexpected mandatory job set."
  }
  foreach ($jobName in $requiredCiJobs) {
    if ([string](Get-RequiredJsonProperty $jobResults $jobName) -cne "success") {
      throw "CI release attestation job '$jobName' did not succeed."
    }
  }

  return [pscustomobject]@{
    Bucket = ""
    Prefix = ""
    EndpointUrl = ""
    Region = ""
    WriterAccessKeySha256 = ""
    ManifestSigningPublicKeySha256 = ""
    CiVerifiedAt = $ciVerifiedAt
  }
}

function Assert-ReleaseGateAttestations {
  param(
    [string]$S3Path,
    [string]$RestorePath,
    [string]$CiPath,
    [string]$ExpectedReleaseSha,
    [string]$ExpectedRepository
  )

  $requiredPaths = @($S3Path, $RestorePath, $CiPath)
  foreach ($path in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
      throw "Required release gate attestation was not found: $path"
    }
  }
  $s3Bootstrap = Read-ReleaseGateBootstrap $S3Path
  $restoreBootstrap = Read-ReleaseGateBootstrap $RestorePath
  $ciBootstrap = Read-ReleaseGateBootstrap $CiPath
  $ciRunId = [int64](Get-RequiredJsonProperty $ciBootstrap "workflow_run_id")
  $restoreRunId = [int64](Get-RequiredJsonProperty $restoreBootstrap "workflow_run_id")
  if ([int64](Get-RequiredJsonProperty $s3Bootstrap "workflow_run_id") -ne $restoreRunId) {
    throw "S3 retention and restore bootstraps do not reference the same workflow run."
  }
  $ci = Get-AuthenticatedGitHubArtifactJson `
    -Bootstrap $ciBootstrap `
    -ExpectedRepository $ExpectedRepository `
    -ExpectedReleaseSha $ExpectedReleaseSha `
    -ExpectedWorkflowName "CI" `
    -ExpectedWorkflowPath ".github/workflows/ci.yml" `
    -AllowedEvents @("push") `
    -ArtifactName "ci-release-attestation-$ExpectedReleaseSha" `
    -ExpectedFileName "ci-release-attestation.json"
  $s3 = Get-AuthenticatedGitHubArtifactJson `
    -Bootstrap $s3Bootstrap `
    -ExpectedRepository $ExpectedRepository `
    -ExpectedReleaseSha $ExpectedReleaseSha `
    -ExpectedWorkflowName "Restore drill" `
    -ExpectedWorkflowPath ".github/workflows/restore-drill.yml" `
    -AllowedEvents @("schedule", "workflow_dispatch") `
    -ArtifactName "s3-retention-attestation-$restoreRunId" `
    -ExpectedFileName "s3-retention-attestation.json"
  $restore = Get-AuthenticatedGitHubArtifactJson `
    -Bootstrap $restoreBootstrap `
    -ExpectedRepository $ExpectedRepository `
    -ExpectedReleaseSha $ExpectedReleaseSha `
    -ExpectedWorkflowName "Restore drill" `
    -ExpectedWorkflowPath ".github/workflows/restore-drill.yml" `
    -AllowedEvents @("schedule", "workflow_dispatch") `
    -ArtifactName "s3-restore-attestation-$restoreRunId" `
    -ExpectedFileName "shamrai-restore-attestation.json"
  Assert-AttestationSchemaVersion $s3 "s3-retention" 3
  Assert-AttestationSchemaVersion $restore "restore-drill"
  Assert-AttestationSchemaVersion $ci "ci-release"
  $authenticatedEvidence = @(
    [pscustomobject]@{ Name = "ci"; Payload = $ci; RunId = $ciRunId; RunAttempt = [int](Get-RequiredJsonProperty $ciBootstrap "workflow_run_attempt") },
    [pscustomobject]@{ Name = "s3"; Payload = $s3; RunId = $restoreRunId; RunAttempt = [int](Get-RequiredJsonProperty $s3Bootstrap "workflow_run_attempt") },
    [pscustomobject]@{ Name = "restore"; Payload = $restore; RunId = $restoreRunId; RunAttempt = [int](Get-RequiredJsonProperty $restoreBootstrap "workflow_run_attempt") }
  )
  foreach ($evidence in $authenticatedEvidence) {
    if ([string](Get-RequiredJsonProperty $evidence.Payload "repository") -cne $ExpectedRepository -or
        [string](Get-RequiredJsonProperty $evidence.Payload "git_sha") -cne $ExpectedReleaseSha -or
        [int64](Get-RequiredJsonProperty $evidence.Payload "workflow_run_id") -ne $evidence.RunId -or
        [int](Get-RequiredJsonProperty $evidence.Payload "workflow_run_attempt") -ne $evidence.RunAttempt) {
      throw "Authenticated $($evidence.Name) release evidence has inconsistent workflow provenance."
    }
  }

  if ((Get-RequiredJsonProperty $ci "status") -ne "passed") { throw "CI release attestation did not pass." }
  $ciVerifiedAt = Assert-FreshUtcTimestamp ([string](Get-RequiredJsonProperty $ci "verified_at")) "ci.verified_at" 168
  if ([string](Get-RequiredJsonProperty $ci "git_sha") -cne $ExpectedReleaseSha) {
    throw "CI release attestation does not match the exact release SHA."
  }
  if ([string](Get-RequiredJsonProperty $ci "repository") -cne $ExpectedRepository) {
    throw "CI release attestation belongs to a different repository."
  }
  if ((Get-RequiredJsonProperty $ci "ref_protected") -isnot [bool] -or
      -not [bool](Get-RequiredJsonProperty $ci "ref_protected")) {
    throw "CI release attestation must come from a protected branch."
  }
  if ([string](Get-RequiredJsonProperty $ci "workflow") -cne "CI" -or
      [string](Get-RequiredJsonProperty $ci "event_name") -cne "push" -or
      [string](Get-RequiredJsonProperty $ci "ref") -notmatch '^refs/heads/[A-Za-z0-9._/-]+$') {
    throw "CI release attestation must come from the CI push workflow on a branch."
  }
  if ([int64](Get-RequiredJsonProperty $ci "run_id") -lt 1 -or
      [int](Get-RequiredJsonProperty $ci "run_attempt") -lt 1) {
    throw "CI release attestation has invalid workflow run identity."
  }
  $requiredCiJobs = @(
    "backend", "flat-coverage", "frontend", "secret-scan", "docker-build",
    "alembic-smoke", "postgres-concurrency", "compose", "powershell"
  )
  $jobResults = Get-RequiredJsonProperty $ci "job_results"
  if (@($jobResults.PSObject.Properties).Count -ne $requiredCiJobs.Count) {
    throw "CI release attestation has an unexpected mandatory job set."
  }
  foreach ($jobName in $requiredCiJobs) {
    if ([string](Get-RequiredJsonProperty $jobResults $jobName) -cne "success") {
      throw "CI release attestation job '$jobName' did not succeed."
    }
  }

  if ((Get-RequiredJsonProperty $s3 "status") -ne "passed") { throw "S3 retention attestation did not pass." }
  if ((Get-RequiredJsonProperty $s3 "versioning_status") -ne "Enabled") { throw "S3 versioning is not attested as Enabled." }
  if ((Get-RequiredJsonProperty $s3 "object_lock_status") -ne "Enabled") { throw "S3 Object Lock is not attested as Enabled." }
  if ([string](Get-RequiredJsonProperty $s3 "sts_endpoint_url") -cne [string](Get-RequiredJsonProperty $s3 "endpoint_url")) {
    throw "S3 writer STS evidence is not bound to the configured S3 endpoint."
  }
  if ([int](Get-RequiredJsonProperty $s3 "default_retention_days") -lt 35) { throw "S3 default immutable retention is below 35 days." }
  if ([int](Get-RequiredJsonProperty $s3 "current_expiration_days") -lt 35) { throw "S3 current-object retention is below 35 days." }
  if ([int](Get-RequiredJsonProperty $s3 "noncurrent_expiration_days") -lt 90) { throw "S3 non-current retention is below 90 days." }
  if ([string](Get-RequiredJsonProperty $s3 "bucket_deny_policy_status") -cne "passed") {
    throw "S3 immutable bucket Deny policy is not attested as passed."
  }
  $bucketDenyPolicyHash = [string](Get-RequiredJsonProperty $s3 "bucket_deny_policy_sha256")
  if ($bucketDenyPolicyHash -notmatch '^[0-9a-f]{64}$') { throw "S3 immutable bucket Deny policy hash is invalid." }
  if ([int](Get-RequiredJsonProperty $s3 "bucket_deny_policy_required_rule_count") -ne 4) {
    throw "S3 immutable bucket Deny policy rule coverage is incomplete."
  }
  $policyHash = [string](Get-RequiredJsonProperty $s3 "writer_policy_sha256")
  if ($policyHash -notmatch '^[0-9a-f]{64}$') { throw "S3 writer policy evidence hash is invalid." }
  $writerCredentialHash = [string](Get-RequiredJsonProperty $s3 "writer_access_key_id_sha256")
  if ($writerCredentialHash -notmatch '^[0-9a-f]{64}$') { throw "S3 writer credential fingerprint is invalid." }
  if ([string](Get-RequiredJsonProperty $s3 "writer_identity_evidence") -cne "aws_sts_and_iam_permissions_boundary_v1") {
    throw "S3 writer identity is not bound to live AWS STS and IAM permissions-boundary evidence."
  }
  if ([string](Get-RequiredJsonProperty $s3 "writer_principal_type") -notin @("iam_user", "iam_role", "assumed_role_to_iam_role")) {
    throw "S3 writer principal type is unsupported."
  }
  foreach ($principalHashField in @("writer_caller_arn_sha256", "writer_stable_principal_arn_sha256")) {
    if ([string](Get-RequiredJsonProperty $s3 $principalHashField) -notmatch '^[0-9a-f]{64}$') {
      throw "S3 writer principal fingerprint '$principalHashField' is invalid."
    }
  }
  if ([string](Get-RequiredJsonProperty $s3 "writer_permissions_boundary_status") -cne "passed") {
    throw "S3 writer IAM permissions boundary is not attested as passed."
  }
  foreach ($boundaryHashField in @("writer_permissions_boundary_sha256", "writer_permissions_boundary_arn_sha256")) {
    if ([string](Get-RequiredJsonProperty $s3 $boundaryHashField) -notmatch '^[0-9a-f]{64}$') {
      throw "S3 writer permissions-boundary fingerprint '$boundaryHashField' is invalid."
    }
  }
  if ([string](Get-RequiredJsonProperty $s3 "writer_permissions_boundary_version_id") -notmatch '^v[1-9][0-9]*$' -or
      [int](Get-RequiredJsonProperty $s3 "writer_permissions_boundary_required_rule_count") -ne 5) {
    throw "S3 writer IAM permissions-boundary evidence is incomplete."
  }
  if ([string](Get-RequiredJsonProperty $s3 "writer_capability_probe") -cne "passed") {
    throw "S3 writer capability probe did not pass."
  }
  if ([string](Get-RequiredJsonProperty $s3 "writer_effective_permission_evidence") -cne "live_aws_sts_iam_boundary_bucket_policy_v3+live_unconfounded_object_probes_v3") {
    throw "S3 writer effective-permission evidence is incomplete or obsolete."
  }
  if ([string](Get-RequiredJsonProperty $s3 "writer_admin_capability_probe") -cne "passed") {
    throw "S3 writer administrative capability evidence did not pass."
  }
  foreach ($capabilityField in @(
    "writer_put_object_allowed", "writer_get_object_allowed", "writer_list_prefix_allowed",
    "writer_delete_object_denied", "writer_delete_object_version_denied",
    "writer_outside_prefix_put_denied", "writer_outside_prefix_get_denied",
    "writer_outside_prefix_list_denied",
    "writer_put_object_retention_denied", "writer_put_object_legal_hold_denied",
    "writer_put_object_acl_denied", "writer_put_object_tagging_denied",
    "writer_delete_object_tagging_denied", "writer_abort_multipart_upload_denied",
    "writer_put_bucket_policy_denied", "writer_delete_bucket_policy_denied", "writer_delete_bucket_denied",
    "writer_put_bucket_lifecycle_configuration_denied",
    "writer_put_object_lock_configuration_denied", "writer_put_bucket_versioning_denied"
  )) {
    if ((Get-RequiredJsonProperty $s3 $capabilityField) -isnot [bool] -or
        -not [bool](Get-RequiredJsonProperty $s3 $capabilityField)) {
      throw "S3 writer capability evidence '$capabilityField' is not passed."
    }
  }
  Assert-FreshUtcTimestamp ([string](Get-RequiredJsonProperty $s3 "verified_at")) "s3.verified_at" 168 | Out-Null

  if ((Get-RequiredJsonProperty $restore "status") -ne "passed" -or (Get-RequiredJsonProperty $restore "artifact_source") -ne "s3") {
    throw "Latest restore attestation is not a successful S3 drill."
  }
  Assert-FreshUtcTimestamp ([string](Get-RequiredJsonProperty $restore "verified_at")) "restore.verified_at" 6 | Out-Null
  Assert-FreshUtcTimestamp ([string](Get-RequiredJsonProperty $restore "backup_created_at")) "restore.backup_created_at" 6 | Out-Null
  if ([string](Get-RequiredJsonProperty $restore "backend_git_sha") -ne $ExpectedReleaseSha) {
    throw "Restore drill backend SHA does not match the exact release SHA."
  }
  if ((Get-RequiredJsonProperty $restore "signature_verified") -isnot [bool] -or
      -not [bool](Get-RequiredJsonProperty $restore "signature_verified") -or
      [string](Get-RequiredJsonProperty $restore "signature_algorithm") -cne "Ed25519") {
    throw "Restore drill did not attest an authenticated Ed25519 backup manifest."
  }
  $restoreManifestSignatureHash = [string](Get-RequiredJsonProperty $restore "signature_sha256")
  $restoreSigningPublicKeyHash = [string](Get-RequiredJsonProperty $restore "signing_public_key_sha256")
  if ($restoreManifestSignatureHash -notmatch '^[0-9a-f]{64}$' -or
      $restoreSigningPublicKeyHash -notmatch '^[0-9a-f]{64}$') {
    throw "Restore drill signing evidence hashes are invalid."
  }
  $restoreArtifactKey = [string](Get-RequiredJsonProperty $restore "artifact_key")
  $restoreManifestKey = [string](Get-RequiredJsonProperty $restore "manifest_key")
  $restoreSignatureKey = [string](Get-RequiredJsonProperty $restore "signature_key")
  if ($restoreArtifactKey -notmatch '(^|/)daily/shamrai-db\.\d{8}T\d{6}Z\.dump\.age$' -or
      $restoreManifestKey -cne ($restoreArtifactKey -replace '\.dump\.age$', '.manifest.json') -or
      $restoreSignatureKey -cne ($restoreArtifactKey -replace '\.dump\.age$', '.manifest.sig')) {
    throw "Restore drill signed bundle keys are not bound to one timestamped backup."
  }
  foreach ($storageField in @("bucket", "prefix", "endpoint_url", "region", "writer_access_key_id_sha256")) {
    if ([string](Get-RequiredJsonProperty $restore $storageField) -cne [string](Get-RequiredJsonProperty $s3 $storageField)) {
      throw "S3 retention and restore attestations differ on '$storageField'."
    }
  }

  return [pscustomobject]@{
    Bucket = [string](Get-RequiredJsonProperty $s3 "bucket")
    Prefix = [string](Get-RequiredJsonProperty $s3 "prefix")
    EndpointUrl = [string](Get-RequiredJsonProperty $s3 "endpoint_url")
    Region = [string](Get-RequiredJsonProperty $s3 "region")
    WriterAccessKeySha256 = $writerCredentialHash
    ManifestSigningPublicKeySha256 = $restoreSigningPublicKeyHash
    CiVerifiedAt = $ciVerifiedAt
  }
}

function Assert-HiddenFlatReleaseEvidence {
  param(
    [string]$Path,
    [string]$ExpectedReleaseSha,
    [datetimeoffset]$MinimumEvidenceTimeUtc
  )

  $hiddenFlat = Read-ReleaseGateBootstrap $Path
  Assert-AttestationSchemaVersion $hiddenFlat "hidden-flat-e2e"
  if ((Get-RequiredJsonProperty $hiddenFlat "status") -ne "passed") { throw "Hidden flat E2E attestation did not pass." }
  Assert-FreshUtcTimestamp ([string](Get-RequiredJsonProperty $hiddenFlat "verified_at")) "hidden-flat.verified_at" 24 | Out-Null
  if ([string](Get-RequiredJsonProperty $hiddenFlat "git_sha") -cne $ExpectedReleaseSha) {
    throw "Hidden flat E2E attestation does not match the exact release SHA."
  }
  if ([string](Get-RequiredJsonProperty $hiddenFlat "base_url") -cne "https://shamra1.pro") {
    throw "Hidden flat E2E attestation was not produced against the public origin."
  }
  if ([string](Get-RequiredJsonProperty $hiddenFlat "payment_provider") -notin @("yookassa", "tegro")) {
    throw "Hidden flat E2E attestation did not use an approved real provider."
  }
  $attestedMinimum = Assert-FreshUtcTimestamp `
    ([string](Get-RequiredJsonProperty $hiddenFlat "minimum_evidence_time")) `
    "hidden-flat.minimum_evidence_time" `
    168
  if ($attestedMinimum -ne $MinimumEvidenceTimeUtc.ToUniversalTime()) {
    throw "Hidden flat E2E evidence is not bound to this release's CI run."
  }
  foreach ($timestampField in @("payment_attempt_created_at", "completed_at", "latest_settled_at")) {
    $evidenceTimestamp = Assert-FreshUtcTimestamp `
      ([string](Get-RequiredJsonProperty $hiddenFlat $timestampField)) `
      "hidden-flat.$timestampField" `
      168
    if ($evidenceTimestamp -lt $attestedMinimum) {
      throw "Hidden flat E2E evidence '$timestampField' predates this release."
    }
  }
  $paymentAttemptId = [Guid]::Empty
  $flatSubscriptionId = [Guid]::Empty
  if (-not [Guid]::TryParse([string](Get-RequiredJsonProperty $hiddenFlat "payment_attempt_id"), [ref]$paymentAttemptId) -or
      $paymentAttemptId -eq [Guid]::Empty -or
      -not [Guid]::TryParse([string](Get-RequiredJsonProperty $hiddenFlat "flat_subscription_id"), [ref]$flatSubscriptionId) -or
      $flatSubscriptionId -eq [Guid]::Empty -or
      [int](Get-RequiredJsonProperty $hiddenFlat "hidden_plan_id") -lt 1 -or
      [int](Get-RequiredJsonProperty $hiddenFlat "flat_subscription_revision") -lt 1 -or
      [int](Get-RequiredJsonProperty $hiddenFlat "settled_bet_count") -lt 1) {
    throw "Hidden flat E2E attestation has invalid completed purchase evidence."
  }
}

$git = Find-Tool @("git.exe", "git")
if (-not $git) {
  throw "Git was not found. A deployment must be built from a verified commit."
}
$gitStatus = @(& $git -C $Workspace status --porcelain=v1 --untracked-files=normal)
if ($LASTEXITCODE -ne 0) {
  throw "Unable to inspect the deployment worktree."
}
if ($gitStatus.Count -ne 0) {
  throw "Deployment requires a clean worktree. Commit or remove all staged, unstaged, and untracked files first."
}
if ($SkipLocalChecks) {
  throw "SkipLocalChecks is disabled for reproducible releases; build and compile checks are mandatory."
}
$releaseSha = ((& $git -C $Workspace rev-parse --verify HEAD) -join "").Trim().ToLowerInvariant()
if ($LASTEXITCODE -ne 0 -or $releaseSha -notmatch '^[0-9a-f]{40}$') {
  throw "Unable to resolve a full release commit SHA."
}
$releaseEpoch = ((& $git -C $Workspace show -s --format=%ct $releaseSha) -join "").Trim()
if ($LASTEXITCODE -ne 0 -or $releaseEpoch -notmatch '^\d+$') {
  throw "Unable to resolve the release commit timestamp."
}
$releaseBuildTime = [DateTimeOffset]::FromUnixTimeSeconds([int64]$releaseEpoch).
  ToUniversalTime().
  ToString("yyyy-MM-ddTHH:mm:ssZ")
if ([string]::IsNullOrWhiteSpace($S3RetentionAttestationPath)) {
  $S3RetentionAttestationPath = Join-Path $Workspace ".deploy\s3-retention-attestation.json"
}
if ([string]::IsNullOrWhiteSpace($RestoreAttestationPath)) {
  $RestoreAttestationPath = Join-Path $Workspace ".deploy\shamrai-restore-attestation.json"
}
if ([string]::IsNullOrWhiteSpace($CiAttestationPath)) {
  $CiAttestationPath = Join-Path $Workspace ".deploy\ci-release-attestation.json"
}
if ([string]::IsNullOrWhiteSpace($HiddenFlatAttestationPath)) {
  $HiddenFlatAttestationPath = Join-Path $Workspace ".deploy\hidden-flat-e2e-attestation.json"
}
if ($LocalEncryptedBackupGate) {
  $releaseGateContext = Assert-CiReleaseGateAttestation `
    -CiPath $CiAttestationPath `
    -ExpectedReleaseSha $releaseSha `
    -ExpectedRepository "inftverezovsky/Mini-Web-Shamrai-"
} else {
  $releaseGateContext = Assert-ReleaseGateAttestations `
    -S3Path $S3RetentionAttestationPath `
    -RestorePath $RestoreAttestationPath `
    -CiPath $CiAttestationPath `
    -ExpectedReleaseSha $releaseSha `
    -ExpectedRepository "inftverezovsky/Mini-Web-Shamrai-"
}

if (-not $PreviewOnly) {
  $hiddenBootstrap = Read-ReleaseGateBootstrap $HiddenFlatAttestationPath
  $hiddenPaymentAttemptId = [Guid]::Empty
  if (-not [Guid]::TryParse([string](Get-RequiredJsonProperty $hiddenBootstrap "payment_attempt_id"), [ref]$hiddenPaymentAttemptId) -or
      $hiddenPaymentAttemptId -eq [Guid]::Empty) {
    throw "Hidden flat E2E bootstrap has an invalid payment attempt id."
  }
  $hiddenPlanId = [int](Get-RequiredJsonProperty $hiddenBootstrap "hidden_plan_id")
  if ($hiddenPlanId -lt 1) {
    throw "Hidden flat E2E bootstrap has an invalid hidden plan id."
  }
  $hiddenVerifier = Join-Path $Workspace "scripts\verify-shamrai-hidden-flat-e2e.ps1"
  & $hiddenVerifier `
    -PaymentAttemptId $hiddenPaymentAttemptId `
    -HiddenPlanId $hiddenPlanId `
    -ExpectedGitSha $releaseSha `
    -MinimumEvidenceTimeUtc $releaseGateContext.CiVerifiedAt `
    -BaseUrl "https://shamra1.pro" `
    -AttestationPath $HiddenFlatAttestationPath
  Assert-HiddenFlatReleaseEvidence `
    -Path $HiddenFlatAttestationPath `
    -ExpectedReleaseSha $releaseSha `
    -MinimumEvidenceTimeUtc $releaseGateContext.CiVerifiedAt
}

$defaultSshKeyPath = Join-Path $env:USERPROFILE ".ssh\codex_deploy_ed25519"
if ([string]::IsNullOrWhiteSpace($SshKeyPath) -and (Test-Path -LiteralPath $defaultSshKeyPath)) {
  $SshKeyPath = $defaultSshKeyPath
}

$ssh = Find-Tool @("ssh.exe", "ssh")
$scp = Find-Tool @("scp.exe", "scp")
$sshKeyscan = Find-Tool @("C:\Program Files\Git\usr\bin\ssh-keyscan.exe", "ssh-keyscan.exe", "ssh-keyscan")
$sshKeygen = Find-Tool @("ssh-keygen.exe", "ssh-keygen")
$knownHostsPath = Join-Path $Workspace ".deploy\shamrai-known-hosts"

if ($DryRun) {
  # Archive verification below needs no remote authentication.
} else {
  if ($PromptPassword) {
    throw "Password deployment is disabled because command-line password arguments leak through the process list. Use an SSH key."
  }
  if (-not $ssh -or -not $scp -or -not $sshKeyscan -or -not $sshKeygen) {
    throw "OpenSSH ssh/scp/ssh-keyscan/ssh-keygen are required."
  }
  if ([string]::IsNullOrWhiteSpace($SshKeyPath) -or -not (Test-Path -LiteralPath $SshKeyPath)) {
    throw "A valid SSH key is required. Pass -SshKeyPath or set SHAMRAI_SSH_KEY_PATH."
  }
  $hostName = if ($Server.Contains("@")) { $Server.Split("@", 2)[1] } else { $Server }
  $expectedFingerprintMatch = [regex]::Match($HostKey, 'SHA256:[A-Za-z0-9+/=]+')
  if (-not $expectedFingerprintMatch.Success) {
    throw "HostKey must contain a pinned SHA256 fingerprint."
  }
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $knownHostsPath) | Out-Null
  $knownHostLine = @(& $sshKeyscan -p 22 -t ed25519 $hostName)
  if ($LASTEXITCODE -ne 0 -or $knownHostLine.Count -eq 0) {
    throw "ssh-keyscan returned no ed25519 host key for $hostName."
  }
  Set-Utf8NoBomLfContent -Path $knownHostsPath -Content (($knownHostLine -join "`n").Trim() + "`n")
  $actualFingerprint = @(& $sshKeygen -lf $knownHostsPath)
  if ($LASTEXITCODE -ne 0 -or ($actualFingerprint -join "`n") -notmatch [regex]::Escape($expectedFingerprintMatch.Value)) {
    throw "SSH host key fingerprint mismatch for $hostName."
  }
}

function Invoke-RemoteChecked {
  param([Parameter(Mandatory = $true)][string]$Command)

  Invoke-NativeChecked $ssh `
    "-i" $SshKeyPath `
    "-o" "BatchMode=yes" `
    "-o" "IdentitiesOnly=yes" `
    "-o" "UserKnownHostsFile=$knownHostsPath" `
    "-o" "StrictHostKeyChecking=yes" `
    $Server `
    $Command
}

function Invoke-RemoteOutputChecked {
  param([Parameter(Mandatory = $true)][string]$Command)

  $output = & $ssh `
    "-i" $SshKeyPath `
    "-o" "BatchMode=yes" `
    "-o" "IdentitiesOnly=yes" `
    "-o" "UserKnownHostsFile=$knownHostsPath" `
    "-o" "StrictHostKeyChecking=yes" `
    $Server `
    $Command
  if ($LASTEXITCODE -ne 0) {
    throw "Remote command failed with exit code ${LASTEXITCODE}."
  }
  return ($output -join "`n")
}

function Copy-ToRemoteChecked {
  param(
    [Parameter(Mandatory = $true)][string]$LocalPath,
    [Parameter(Mandatory = $true)][string]$RemotePath
  )

  $target = "${Server}:$RemotePath"
  Invoke-NativeChecked $scp `
    "-i" $SshKeyPath `
    "-o" "BatchMode=yes" `
    "-o" "IdentitiesOnly=yes" `
    "-o" "UserKnownHostsFile=$knownHostsPath" `
    "-o" "StrictHostKeyChecking=yes" `
    $LocalPath `
    $target
}

$deployDir = Join-Path $Workspace ".deploy"
$repoArchive = Join-Path $deployDir "shamrai-public-repo.tar.gz"
$webArchive = Join-Path $deployDir "shamrai-web-dist.tar.gz"
$localBuildRoot = Join-Path $deployDir ("release-source-{0}" -f [Guid]::NewGuid().ToString("N"))
$localSourceArchive = Join-Path $localBuildRoot "release-source.tar"
$frontendBuildPath = Join-Path $localBuildRoot "frontend"
$backendBuildPath = Join-Path $localBuildRoot "backend"
$frontendDistPath = Join-Path $frontendBuildPath "dist"
$script:frontendPublicAssets = @()
$nginxConfig = Join-Path $Workspace "deploy\nginx\shamrai.conf"
$remoteGuardScript = Join-Path $deployDir "shamrai-public-guard.sh"
$remoteDeployScript = Join-Path $deployDir "shamrai-public-remote-deploy.sh"
$remotePublishScript = Join-Path $deployDir "shamrai-public-remote-publish.sh"
$remoteRollbackScript = Join-Path $deployDir "shamrai-public-remote-rollback.sh"

if (-not (Test-Path $nginxConfig)) {
  throw "Nginx config not found: $nginxConfig"
}

Invoke-Step "Prepare isolated release source" {
  $tar = Find-Tool @("tar.exe", "tar")
  if (-not $tar) {
    throw "tar was not found; an isolated exact-SHA release source cannot be prepared."
  }
  New-Item -ItemType Directory -Force -Path $localBuildRoot | Out-Null
  Invoke-NativeChecked $git `
    "-C" $Workspace `
    "archive" `
    "--format=tar" `
    "--output=$localSourceArchive" `
    $releaseSha `
    "backend" `
    "frontend"
  Invoke-NativeChecked $tar "-xf" $localSourceArchive "-C" $localBuildRoot
  Remove-Item -LiteralPath $localSourceArchive -Force

  $viteEnvironmentFiles = @(
    @(
      ".env",
      ".env.local",
      ".env.production",
      ".env.production.local"
    ) | ForEach-Object { Join-Path $frontendBuildPath $_ } | Where-Object { Test-Path -LiteralPath $_ }
  )
  if ($viteEnvironmentFiles.Count -ne 0) {
    throw "Tracked Vite environment files are forbidden in a release source; use the explicit public release environment allowlist."
  }
}

if (-not $SkipLocalChecks) {
  Invoke-WithSanitizedReleaseEnvironment {
    Invoke-Step "Frontend build" {
      Push-Location $frontendBuildPath
      try {
        $nodeVersion = ((& node --version) -join "").Trim()
        $npmVersion = ((& npm --version) -join "").Trim()
        if ($nodeVersion -ne "v24.15.0" -or $npmVersion -ne "11.12.1") {
          throw "Reproducible frontend release requires Node v24.15.0 and npm 11.12.1 (got $nodeVersion / $npmVersion)."
        }
        $env:VITE_API_URL = ""
        $env:VITE_ENABLE_DEBUG_AUTH = "false"
        $env:VITE_ENABLE_BEARER_AUTH_COMPAT = "false"
        $env:VITE_VK_ID_APP_ID = "54626979"
        $env:VITE_VK_ID_REDIRECT_URI = "https://shamra1.pro"
        $env:VITE_VK_GROUP_ID = $VkGroupId
        $env:VITE_TELEGRAM_BOT_USERNAME = "Shamra1_bot"
        $env:VITE_WEB_PUSH_VAPID_PUBLIC_KEY = ""
        $env:VITE_PROMO_MARATHON = "false"
        $env:VITE_PROMO_SWIPE = "false"
        $env:VITE_PROMO_PVP = "false"
        $env:VITE_PROMO_QUIZ = "false"
        $env:VITE_PROMO_CROWD_BET = "false"
        $env:VITE_PLAUSIBLE_DOMAIN = "shamra1.pro"
        $env:VITE_PLAUSIBLE_ENDPOINT = ""
        $env:VITE_PLAUSIBLE_CAPTURE_LOCALHOST = "false"
        $env:SOURCE_DATE_EPOCH = $releaseEpoch
        $env:ANALYZE = "false"
        Invoke-NativeChecked "npm" "ci" "--ignore-scripts"
        Invoke-NativeChecked "npm" "run" "build"
      } finally {
        Pop-Location
      }
    }

    Invoke-Step "Backend compile" {
      Push-Location $backendBuildPath
      try { Invoke-NativeChecked "python" "-m" "compileall" "-q" "src" "alembic" } finally { Pop-Location }
    }
  }
}

Invoke-Step "Create deployment archives" {
  New-Item -ItemType Directory -Force -Path $deployDir | Out-Null
  Remove-Item -LiteralPath $repoArchive, $webArchive -Force -ErrorAction SilentlyContinue

  Invoke-NativeChecked $git `
    "-C" $Workspace `
    "archive" `
    "--format=tar.gz" `
    "--output=$repoArchive" `
    $releaseSha `
    ".env.example" `
    "backend" `
    "frontend" `
    "docker-compose.yml"

  $script:frontendPublicAssets = @(Get-FrontendDistAssets -DistPath $frontendDistPath)
  $releaseFiles = @(
    Get-ChildItem -LiteralPath $frontendDistPath -Recurse -File |
      ForEach-Object {
        [IO.Path]::GetRelativePath($frontendDistPath, $_.FullName).Replace([IO.Path]::DirectorySeparatorChar, '/')
      } |
      Sort-Object -Unique
  )
  $releaseManifestFiles = [ordered]@{}
  foreach ($relativePath in $releaseFiles) {
    $absolutePath = Join-Path $frontendDistPath ($relativePath -replace '/', [IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
      throw "Release file was not found: $relativePath"
    }
    $item = Get-Item -LiteralPath $absolutePath
    $releaseManifestFiles[$relativePath] = [ordered]@{
      sha256 = (Get-FileHash -LiteralPath $absolutePath -Algorithm SHA256).Hash.ToLowerInvariant()
      bytes = $item.Length
    }
  }
  $releaseManifest = [ordered]@{
    schema_version = 1
    git_sha = $releaseSha
    build_time = $releaseBuildTime
    files = $releaseManifestFiles
  }
  Set-Utf8NoBomLfContent `
    -Path (Join-Path $frontendDistPath "release-manifest.json") `
    -Content (($releaseManifest | ConvertTo-Json -Depth 6) + "`n")
  Invoke-NativeChecked "python" `
    (Join-Path $Workspace "scripts\create-reproducible-tar.py") `
    $frontendDistPath `
    $webArchive `
    $releaseEpoch
  Write-Host "repo_archive_sha256=$((Get-FileHash -LiteralPath $repoArchive -Algorithm SHA256).Hash.ToLowerInvariant())"
  Write-Host "web_archive_sha256=$((Get-FileHash -LiteralPath $webArchive -Algorithm SHA256).Hash.ToLowerInvariant())"
}

if ($DryRun) {
  Write-Host "DRY_RUN deploy-public-shamrai-web"
  Write-Host "git_sha=$releaseSha"
  Write-Host "build_time=$releaseBuildTime"
  Write-Host "repo_archive=$repoArchive"
  Write-Host "web_archive=$webArchive"
  Write-Host "release_manifest=$frontendDistPath\release-manifest.json"
  exit 0
}

$remoteStage = ""
$remoteStageToken = ""
$remoteStage = Invoke-Step "Create root-private remote release stage" {
  $stageOutput = Invoke-RemoteOutputChecked "stage=`$(mktemp -d /tmp/shamrai-public-release.XXXXXX); chmod 0700 `"`$stage`"; realpath -e -- `"`$stage`""
  $stageCandidates = @($stageOutput -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^/tmp/shamrai-public-release\.[A-Za-z0-9]{6}$' })
  if ($stageCandidates.Count -ne 1) {
    throw "Remote mktemp did not return one validated Shamrai release directory."
  }
  return $stageCandidates[0]
}
$remoteStage = ([string]$remoteStage).Trim()
$remoteStageToken = Split-Path -Leaf $remoteStage

Invoke-Step "Create remote scripts" {
  $remoteGuardContent = New-ServerGuardScript -Repair:$RepairShamraiConflicts
  Set-Utf8NoBomLfContent -Path $remoteGuardScript -Content $remoteGuardContent

  if ($LocalEncryptedBackupGate) {
    $candidateImagePreparationBlock = @"
prebuilt_backend_image="`$(cat '$remoteStage/prebuilt-backend-image-id')"
prebuilt_frontend_image="`$(cat '$remoteStage/prebuilt-frontend-image-id')"
case "`$prebuilt_backend_image:`$prebuilt_frontend_image" in sha256:*:sha256:*) ;; *) echo 'Prebuilt candidate image IDs are invalid.' >&2; exit 32 ;; esac
docker image inspect "`$prebuilt_backend_image" >/dev/null
docker image inspect "`$prebuilt_frontend_image" >/dev/null
docker image tag "`$prebuilt_backend_image" shamrai-backend
docker image tag "`$prebuilt_frontend_image" shamrai-frontend
[ "`$(docker image inspect -f '{{.Id}}' shamrai-backend)" = "`$prebuilt_backend_image" ]
[ "`$(docker image inspect -f '{{.Id}}' shamrai-frontend)" = "`$prebuilt_frontend_image" ]
backend_image_id="`$prebuilt_backend_image"
"@
    $migrationImageVerificationBlock = @"
[ "`$(docker image inspect -f '{{.Id}}' shamrai-backend)" = "`$prebuilt_backend_image" ] || { echo 'Migration backend image differs from the qualified candidate.' >&2; exit 34; }
"@
    $startApplicationBlock = "docker compose -p '$ComposeProject' up -d --no-deps --force-recreate --no-build backend frontend"
  } else {
    $candidateImagePreparationBlock = @"
docker compose -p '$ComposeProject' build backend frontend
backend_image_id="`$(docker compose -p '$ComposeProject' images -q backend | head -n 1)"
if [ -z "`$backend_image_id" ]; then
  echo 'Unable to resolve the exact freshly built backend image.' >&2
  exit 32
fi
"@
    $migrationImageVerificationBlock = "docker image inspect `"`$backend_image_id`" >/dev/null"
    $startApplicationBlock = "docker compose -p '$ComposeProject' up -d"
  }

  $remoteDeployContent = @"
set -Eeuo pipefail
umask 077
deploy_id="$remoteStageToken"
stage_path="$RemotePath.stage.`$deploy_id"
backup_path="$RemotePath.rollback.`$deploy_id"
failed_path="$RemotePath.failed.`$deploy_id"
backup_marker="$remoteStage/code-backup.path"
schema_upgrade_marker="$remoteStage/schema-upgrade.completed"
schema_upgrade_committed=0
backup_timer_enabled=0
backup_timer_active=0
backup_lock_held=0
schedule_restore_allowed=1
rm -rf "`$stage_path"
rm -f "`$backup_marker" "`$schema_upgrade_marker"
mkdir -p "`$stage_path"
tar -xzf "$remoteStage/repo.tar.gz" -C "`$stage_path"
if [ -f "$RemotePath/.env" ]; then
  install -m 0600 "$RemotePath/.env" "`$stage_path/.env"
elif [ -f "`$stage_path/.env.example" ]; then
  install -m 0600 "`$stage_path/.env.example" "`$stage_path/.env"
else
  echo "Missing root runtime env and .env.example fallback." >&2
  exit 20
fi
if [ -f "$RemotePath/backend/.env" ]; then
  install -m 0600 "$RemotePath/backend/.env" "`$stage_path/backend/.env"
elif [ -f "`$stage_path/backend/.env.example" ]; then
  install -m 0600 "`$stage_path/backend/.env.example" "`$stage_path/backend/.env"
else
  echo "Missing backend runtime env and backend/.env.example fallback." >&2
  exit 21
fi
python3 - "`$stage_path/.env" "`$stage_path/backend/.env" <<'PY'
from pathlib import Path
import sys

root_env = Path(sys.argv[1])
backend_env = Path(sys.argv[2])

def patch_env(path, values):
    lines = path.read_text().splitlines() if path.exists() else []
    seen = set()
    out = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0]
            if key in values:
                if key not in seen:
                    out.append(f"{key}={values[key]}")
                    seen.add(key)
                continue
        out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out).rstrip() + "\n")

patch_env(root_env, {
    "APP_ENV": "production",
    "DEBUG_MODE": "false",
    "ALLOW_DEBUG_AUTH_BYPASS": "false",
    "FRONTEND_PORT": "8082",
    "VITE_API_URL": "",
    "VITE_ENABLE_DEBUG_AUTH": "false",
    "VITE_VK_ID_APP_ID": "54626979",
    "VITE_VK_ID_REDIRECT_URI": "https://shamra1.pro",
    "VITE_VK_GROUP_ID": "$VkGroupId",
    "VITE_TELEGRAM_BOT_USERNAME": "Shamra1_bot",
    "VITE_PLAUSIBLE_DOMAIN": "shamra1.pro",
    "VITE_PLAUSIBLE_ENDPOINT": "",
    "VITE_PLAUSIBLE_CAPTURE_LOCALHOST": "false",
})
patch_env(backend_env, {
    "DEBUG_MODE": "false",
    "ALLOW_DEBUG_AUTH_BYPASS": "false",
    "TELEGRAM_USE_POLLING": "true",
    "TELEGRAM_START_RESPONSE_TIMEOUT_SECONDS": "4.0",
    "TELEGRAM_WEBHOOK_IP_ADDRESS": "",
    "VK_DIALOG_POLLING_ENABLED": "true",
    "VK_DIALOG_POLLING_INTERVAL_SECONDS": "1.0",
    "VK_DIALOG_POLLING_BATCH_SIZE": "20",
    "CORS_ALLOWED_ORIGINS": "https://shamra1.pro,https://www.shamra1.pro",
    "API_BASE_URL": "https://shamra1.pro",
    "FRONTEND_BASE_URL": "https://shamra1.pro/app",
    "YOOKASSA_RETURN_URL": "https://shamra1.pro/app",
    "VK_ID_APP_ID": "54626979",
    "VK_ID_REDIRECT_URI": "https://shamra1.pro",
    "SHAMRAI_GIT_SHA": "$releaseSha",
    "SHAMRAI_BUILD_TIME": "$releaseBuildTime",
})
PY

cd "`$stage_path"
docker compose -p "$ComposeProject" config -q

validate_state_directory() {
  state_path="`$1"
  if [ -L "`$state_path" ] || [ ! -d "`$state_path" ]; then
    echo "State path must be a regular directory: `$state_path" >&2
    return 1
  fi
  if [ "`$(realpath -e -- "`$state_path")" != "`$state_path" ] || [ "`$(stat -c '%u' -- "`$state_path")" != "0" ]; then
    echo "State directory must be canonical and root-owned: `$state_path" >&2
    return 1
  fi
  if [ -n "`$(find "`$state_path" -xdev ! -user root -print -quit)" ]; then
    echo "State directory contains a non-root-owned entry: `$state_path" >&2
    return 1
  fi
  while IFS= read -r state_link; do
    if [ "`$(basename "`$state_path")" != "db-backups" ]; then
      echo "Only db-backups latest pointers may be symlinks: `$state_link" >&2
      return 1
    fi
    link_name="`$(basename "`$state_link")"
    link_target="`$(readlink -- "`$state_link")"
    case "`$link_name:`$link_target" in
      latest.dump.age:daily/shamrai-db.*.dump.age|latest.manifest.json:daily/shamrai-db.*.manifest.json|latest.manifest.sig:daily/shamrai-db.*.manifest.sig) ;;
      *) echo "Unexpected backup pointer: `$state_link -> `$link_target" >&2; return 1 ;;
    esac
    if [[ ! "`$link_target" =~ ^daily/shamrai-db\.[0-9]{8}T[0-9]{6}Z\.(dump\.age|manifest\.json|manifest\.sig)$ ]]; then
      echo "Backup pointer target has an invalid shape: `$link_target" >&2
      return 1
    fi
    resolved_target="`$(realpath -e -- "`$state_link")" || return 1
    case "`$resolved_target" in "`$state_path/daily/"*) ;; *) return 1 ;; esac
    [ -f "`$resolved_target" ] && [ ! -L "`$resolved_target" ] && [ "`$(stat -c '%u' -- "`$resolved_target")" = "0" ] || return 1
  done < <(find "`$state_path" -xdev -type l -print)
}

move_state_directories() {
  source_root="`$1"
  target_root="`$2"
  for state_name in ops db-backups; do
    source_state="`$source_root/`$state_name"
    target_state="`$target_root/`$state_name"
    if [ -e "`$source_state" ] || [ -L "`$source_state" ]; then
      validate_state_directory "`$source_state"
      if [ -e "`$target_state" ] || [ -L "`$target_state" ]; then
        echo "Release unexpectedly contains reserved state path: `$target_state" >&2
        return 1
      fi
      mv "`$source_state" "`$target_state"
    fi
  done
}

harden_state_modes() {
  for state_name in ops db-backups; do
    state_path="$RemotePath/`$state_name"
    if [ -d "`$state_path" ]; then
      validate_state_directory "`$state_path"
      find "`$state_path" -xdev -type d -exec chmod 0700 {} +
      find "`$state_path" -xdev -type f -exec chmod 0600 {} +
    fi
  done
  [ ! -f "$RemotePath/ops/backup-db.sh" ] || chmod 0700 "$RemotePath/ops/backup-db.sh"
  [ ! -f "$RemotePath/ops/backup.env" ] || chmod 0600 "$RemotePath/ops/backup.env"
}

restore_backup_schedule() {
  if [ "`$backup_lock_held" = "1" ]; then
    flock -u 8 || true
    exec 8>&-
    backup_lock_held=0
  fi
  if [ "`$schedule_restore_allowed" != "1" ]; then
    systemctl stop shamrai-db-backup.timer 2>/dev/null || true
    echo 'CRITICAL: backup timer left stopped because canonical backup state was not restored safely.' >&2
    return 0
  fi
  if [ "`$backup_timer_enabled" = "1" ]; then
    systemctl enable shamrai-db-backup.timer >/dev/null
  fi
  if [ "`$backup_timer_active" = "1" ]; then
    systemctl start shamrai-db-backup.timer
  fi
}

restore_schedule_on_early_error() {
  status="`$?"
  trap - ERR
  restore_backup_schedule || true
  exit "`$status"
}

trap restore_schedule_on_early_error ERR

if systemctl is-enabled --quiet shamrai-db-backup.timer 2>/dev/null; then
  backup_timer_enabled=1
fi
if systemctl is-active --quiet shamrai-db-backup.timer 2>/dev/null; then
  backup_timer_active=1
fi
if [ "`$backup_timer_active" = "1" ]; then
  systemctl stop shamrai-db-backup.timer
fi
if [ -d "$RemotePath/ops" ]; then
  validate_state_directory "$RemotePath/ops"
  exec 8>"$RemotePath/ops/backup.lock"
  if ! flock -w 300 8; then
    echo 'Timed out waiting for the active Shamrai backup to finish.' >&2
    restore_backup_schedule || true
    exit 33
  fi
  backup_lock_held=1
fi

rollback_code() {
  status="`$?"
  trap - ERR
  trap restore_backup_schedule EXIT
  docker compose -p "$ComposeProject" logs backend --tail 200 >&2 || true
  if [ "`$schema_upgrade_committed" = "1" ] || [ -f "`$schema_upgrade_marker" ]; then
    echo 'Schema upgrade already committed; refusing pre-migration code rollback.' >&2
    rm -f "`$backup_marker"
    if [ -d "$RemotePath" ]; then
      cd "$RemotePath"
      docker compose -p "$ComposeProject" stop backend
    fi
    schedule_restore_allowed=1
    echo 'Post-migration rollback retained the new code and database; forward repair is required.' >&2
  else
    echo "Preview deploy failed; rolling back code snapshot." >&2
    if [ -d "`$backup_path" ]; then
      schedule_restore_allowed=0
      if [ -d "$RemotePath" ]; then
        rm -rf "`$failed_path"
        mv "$RemotePath" "`$failed_path"
      fi
      mv "`$backup_path" "$RemotePath"
      move_state_directories "`$failed_path" "$RemotePath"
      harden_state_modes
      cd "$RemotePath"
      docker compose -p "$ComposeProject" up -d --build
      schedule_restore_allowed=1
    else
      echo "No previous code snapshot exists; leaving the current canonical path untouched." >&2
    fi
  fi
  trap - EXIT
  restore_backup_schedule
  exit "`$status"
}

trap rollback_code ERR

if [ -d "$RemotePath" ]; then
  schedule_restore_allowed=0
  mv "$RemotePath" "`$backup_path"
  printf '%s\n' "`$backup_path" > "`$backup_marker"
fi
mv "`$stage_path" "$RemotePath"
if [ -d "`$backup_path" ]; then
  move_state_directories "`$backup_path" "$RemotePath"
fi
find "$RemotePath" \( -path "$RemotePath/ops" -o -path "$RemotePath/db-backups" \) -prune -o -type d -exec chmod 0755 {} +
find "$RemotePath" \( -path "$RemotePath/ops" -o -path "$RemotePath/db-backups" \) -prune -o -type f ! -name '.env' -exec chmod 0644 {} +
[ -f "$RemotePath/.env" ] && chmod 0600 "$RemotePath/.env"
[ -f "$RemotePath/backend/.env" ] && chmod 0600 "$RemotePath/backend/.env"
harden_state_modes
cd "$RemotePath"
docker compose -p "$ComposeProject" up -d postgres
$candidateImagePreparationBlock
backend_static_volume="${ComposeProject}_backend_static"
docker volume inspect "`$backend_static_volume" >/dev/null 2>&1 || docker volume create "`$backend_static_volume" >/dev/null
docker run --rm --user 0:0 --entrypoint sh -v "`$backend_static_volume:/target" "`$backend_image_id" -c 'mkdir -p /target/coupons && chown -R 10001:10001 /target'
protected_data_fingerprints() {
  docker compose -p "$ComposeProject" exec -T postgres psql -U shamrai -d shamrai -At -v ON_ERROR_STOP=1 <<'SQL'
WITH fingerprints(key, value) AS (
  SELECT 'bets', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))
  FROM (SELECT md5(to_jsonb(t)::text) AS row_hash FROM bets AS t) AS rows
  UNION ALL
  SELECT 'flat_subscription_credits', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))
  FROM (SELECT md5(to_jsonb(t)::text) AS row_hash FROM flat_subscription_credits AS t) AS rows
  UNION ALL
  SELECT 'flat_subscriptions_legacy', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))
  FROM (SELECT md5((to_jsonb(t) - 'revision')::text) AS row_hash FROM flat_subscriptions AS t) AS rows
  UNION ALL
  SELECT 'historical_stats_breakdowns', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))
  FROM (SELECT md5(to_jsonb(t)::text) AS row_hash FROM historical_stats_breakdowns AS t) AS rows
  UNION ALL
  SELECT 'historical_stats_details', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))
  FROM (SELECT md5(to_jsonb(t)::text) AS row_hash FROM historical_stats_details AS t) AS rows
  UNION ALL
  SELECT 'historical_stats_import_batches', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))
  FROM (SELECT md5(to_jsonb(t)::text) AS row_hash FROM historical_stats_import_batches AS t) AS rows
  UNION ALL
  SELECT 'historical_stats_monthly', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))
  FROM (SELECT md5(to_jsonb(t)::text) AS row_hash FROM historical_stats_monthly AS t) AS rows
  UNION ALL
  SELECT 'payment_attempts_legacy', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))
  FROM (
    SELECT md5((to_jsonb(t) - ARRAY[
      'checkout_intent_id', 'checkout_payload_hash', 'checkout_state', 'checkout_url',
      'checkout_creation_started_at', 'telegram_pre_checkout_query_id',
      'telegram_pre_checkout_user_id', 'telegram_pre_checkout_reserved_at',
      'purchase_type_snapshot', 'crowd_bet_id_snapshot', 'plan_name_snapshot',
      'entitlement_type_snapshot', 'target_flats_snapshot', 'match_count_snapshot',
      'discount_percent_snapshot'
    ]::text[])::text) AS row_hash
    FROM payment_attempts AS t
  ) AS rows
  UNION ALL
  SELECT 'quizzes_legacy', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))
  FROM (SELECT md5((to_jsonb(t) - ARRAY['created_at', 'is_active']::text[])::text) AS row_hash FROM quizzes AS t) AS rows
  UNION ALL
  SELECT 'subscription_plans_legacy', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))
  FROM (SELECT md5((to_jsonb(t) - 'is_hidden')::text) AS row_hash FROM subscription_plans AS t) AS rows
  UNION ALL
  SELECT 'subscriptions', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))
  FROM (SELECT md5(to_jsonb(t)::text) AS row_hash FROM subscriptions AS t) AS rows
  UNION ALL
  SELECT 'user_bets', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))
  FROM (SELECT md5(to_jsonb(t)::text) AS row_hash FROM user_bets AS t) AS rows
  UNION ALL
  SELECT 'users', md5(COALESCE(string_agg(row_hash, '' ORDER BY row_hash), ''))
  FROM (SELECT md5(to_jsonb(t)::text) AS row_hash FROM users AS t) AS rows
)
SELECT key || '=' || value FROM fingerprints ORDER BY key;
SQL
}
# Drain every old payment writer before the one-time checkout snapshot
# backfill.  Once Alembic commits, the old writer must never be restarted:
# attempts created by it would not contain immutable checkout snapshots.
docker compose -p "$ComposeProject" stop backend
pre_migration_protected_fingerprints="`$(protected_data_fingerprints)"
test -n "`$pre_migration_protected_fingerprints"
$migrationImageVerificationBlock
docker compose -p "$ComposeProject" run --rm --no-deps backend alembic upgrade head
schema_upgrade_committed=1
install -m 0600 /dev/null "`$schema_upgrade_marker"
post_migration_protected_fingerprints="`$(protected_data_fingerprints)"
if [ "`$post_migration_protected_fingerprints" != "`$pre_migration_protected_fingerprints" ]; then
  echo 'Protected production data changed during migrations.' >&2
  diff -u <(printf '%s\n' "`$pre_migration_protected_fingerprints") <(printf '%s\n' "`$post_migration_protected_fingerprints") >&2 || true
  exit 35
fi
migration_revision="`$(docker compose -p "$ComposeProject" exec -T postgres psql -U shamrai -d shamrai -At -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version')"
[ "`$migration_revision" = '20260802_0043' ] || { echo 'Unexpected Alembic revision after migration.' >&2; exit 36; }
migration_invariants="`$(docker compose -p "$ComposeProject" exec -T postgres psql -U shamrai -d shamrai -At -v ON_ERROR_STOP=1 -c "SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM subscription_plans WHERE is_hidden IS DISTINCT FROM FALSE) AND NOT EXISTS (SELECT 1 FROM subscription_plan_checkout_allowlist) AND NOT EXISTS (SELECT 1 FROM flat_subscriptions WHERE revision IS DISTINCT FROM 1) AND NOT EXISTS (SELECT 1 FROM payment_attempts WHERE checkout_state IS NULL) AND NOT EXISTS (SELECT 1 FROM quizzes WHERE is_active IS DISTINCT FROM TRUE) THEN 'ok' ELSE 'failed' END")"
[ "`$migration_invariants" = 'ok' ] || { echo 'Post-migration data invariants failed.' >&2; exit 37; }
echo 'protected_production_data_unchanged'
echo 'migration_data_invariants_ok'
$startApplicationBlock
health_ok=0
for attempt in {1..30}; do
  if curl -fsS http://127.0.0.1:8082/api/ready; then
    health_ok=1
    break
  fi
  sleep 1
done
if [ "`$health_ok" != "1" ]; then
  echo "Backend readiness check failed after 30 seconds" >&2
  exit 1
fi
schedule_restore_allowed=1
restore_backup_schedule
trap - ERR
exit 0
"@
  Set-Utf8NoBomLfContent -Path $remoteDeployScript -Content $remoteDeployContent

  $remotePublishContent = @"
set -Eeuo pipefail
umask 077
deploy_id="$remoteStageToken"
web_stage="$PublicWebRoot.stage.`$deploy_id"
web_backup="$PublicWebRoot.rollback.`$deploy_id"
web_failed="$PublicWebRoot.failed.`$deploy_id"
nginx_backup="$remoteStage/nginx-backup"
web_backup_marker="$remoteStage/web-backup.path"
nginx_backup_marker="$remoteStage/nginx-backup.path"
rm -rf "`$web_stage" "`$nginx_backup"
rm -f "`$web_backup_marker" "`$nginx_backup_marker"
mkdir -p "`$(dirname "$PublicWebRoot")" "`$web_stage" "`$nginx_backup"
tar -xzf "$remoteStage/web-dist.tar.gz" -C "`$web_stage"

restore_public() {
  status="`$?"
  trap - ERR
  echo "Public publish failed; rolling back web root and nginx snapshot." >&2
  if [ -d "`$web_backup" ]; then
    if [ -e "$PublicWebRoot" ]; then
      rm -rf "`$web_failed"
      mv "$PublicWebRoot" "`$web_failed"
    fi
    mv "`$web_backup" "$PublicWebRoot"
  else
    echo 'No public web snapshot exists; leaving the current public root untouched.' >&2
  fi
  if [ -f "`$nginx_backup_marker" ]; then
    marker_nginx_backup="`$(cat "`$nginx_backup_marker")"
    if [ "`$marker_nginx_backup" != "`$nginx_backup" ]; then
      echo 'Unexpected nginx rollback marker.' >&2
      exit 42
    fi
    if [ -f "`$nginx_backup/shamrai.conf" ]; then
      cp -a "`$nginx_backup/shamrai.conf" /etc/nginx/sites-available/shamrai.conf
    else
      rm -f /etc/nginx/sites-available/shamrai.conf
    fi
    rm -f /etc/nginx/sites-enabled/shamrai.conf
    if [ -f "`$nginx_backup/enabled-manifest" ]; then
      while IFS="`$(printf '\t')" read -r kind name target; do
        [ -n "`$name" ] || continue
        rm -f "/etc/nginx/sites-enabled/`$name"
        if [ "`$kind" = "symlink" ]; then
          ln -s "`$target" "/etc/nginx/sites-enabled/`$name"
        elif [ "`$kind" = "file" ]; then
          cp -a "`$nginx_backup/enabled-`$name" "/etc/nginx/sites-enabled/`$name"
        fi
      done < "`$nginx_backup/enabled-manifest"
    fi
    nginx -t
    systemctl reload nginx
  else
    echo 'No nginx snapshot marker exists; leaving live nginx configuration untouched.' >&2
  fi
  exit "`$status"
}

trap restore_public ERR

if [ -e "$PublicWebRoot" ]; then
  mv "$PublicWebRoot" "`$web_backup"
  printf '%s\n' "`$web_backup" > "`$web_backup_marker"
fi
if [ -f /etc/nginx/sites-available/shamrai.conf ]; then
  cp -a /etc/nginx/sites-available/shamrai.conf "`$nginx_backup/shamrai.conf"
fi
for enabled in /etc/nginx/sites-enabled/*; do
  [ -e "`$enabled" ] || continue
  if grep -qE 'server_name .*shamra1\.pro' "`$enabled"; then
    enabled_name="`$(basename "`$enabled")"
    if [ -L "`$enabled" ]; then
      printf 'symlink\t%s\t%s\n' "`$enabled_name" "`$(readlink "`$enabled")" >> "`$nginx_backup/enabled-manifest"
    else
      cp -a "`$enabled" "`$nginx_backup/enabled-`$enabled_name"
      printf 'file\t%s\t\n' "`$enabled_name" >> "`$nginx_backup/enabled-manifest"
    fi
  fi
done
printf '%s\n' "`$nginx_backup" > "`$nginx_backup_marker"

mv "`$web_stage" "$PublicWebRoot"
find "$PublicWebRoot" -type d -exec chmod 0755 {} +
find "$PublicWebRoot" -type f -exec chmod 0644 {} +
install -m 0644 "$remoteStage/shamrai.conf" /etc/nginx/sites-available/shamrai.conf
for enabled in /etc/nginx/sites-enabled/*; do
  [ -e "`$enabled" ] || continue
  if grep -qE 'server_name .*shamra1\.pro' "`$enabled"; then
    rm -f "`$enabled"
  fi
done
rm -f /etc/nginx/sites-enabled/shamrai.conf
ln -s /etc/nginx/sites-available/shamrai.conf /etc/nginx/sites-enabled/shamrai.conf
nginx -t
systemctl reload nginx
curl -fsS -I https://shamra1.pro/app/ | head -n 8
trap - ERR
"@
  Set-Utf8NoBomLfContent -Path $remotePublishScript -Content $remotePublishContent

  $remoteRollbackContent = @"
set -Eeuo pipefail
umask 077
code_backup_marker="$remoteStage/code-backup.path"
web_backup_marker="$remoteStage/web-backup.path"
nginx_backup_marker="$remoteStage/nginx-backup.path"
schema_upgrade_marker="$remoteStage/schema-upgrade.completed"
backup_timer_enabled=0
backup_timer_active=0
backup_lock_held=0
schedule_restore_allowed=1
post_migration_fail_closed=0

validate_state_directory() {
  state_path="`$1"
  [ ! -L "`$state_path" ] && [ -d "`$state_path" ] || return 1
  [ "`$(realpath -e -- "`$state_path")" = "`$state_path" ] || return 1
  [ "`$(stat -c '%u' -- "`$state_path")" = "0" ] || return 1
  [ -z "`$(find "`$state_path" -xdev ! -user root -print -quit)" ] || return 1
  while IFS= read -r state_link; do
    [ "`$(basename "`$state_path")" = "db-backups" ] || return 1
    link_name="`$(basename "`$state_link")"
    link_target="`$(readlink -- "`$state_link")"
    case "`$link_name:`$link_target" in
      latest.dump.age:daily/shamrai-db.*.dump.age|latest.manifest.json:daily/shamrai-db.*.manifest.json|latest.manifest.sig:daily/shamrai-db.*.manifest.sig) ;;
      *) return 1 ;;
    esac
    [[ "`$link_target" =~ ^daily/shamrai-db\.[0-9]{8}T[0-9]{6}Z\.(dump\.age|manifest\.json|manifest\.sig)$ ]] || return 1
    resolved_target="`$(realpath -e -- "`$state_link")" || return 1
    case "`$resolved_target" in "`$state_path/daily/"*) ;; *) return 1 ;; esac
    [ -f "`$resolved_target" ] && [ ! -L "`$resolved_target" ] && [ "`$(stat -c '%u' -- "`$resolved_target")" = "0" ] || return 1
  done < <(find "`$state_path" -xdev -type l -print)
}

move_rollback_state() {
  source_root="`$1"
  target_root="`$2"
  for state_name in ops db-backups; do
    source_state="`$source_root/`$state_name"
    target_state="`$target_root/`$state_name"
    if [ -e "`$source_state" ] || [ -L "`$source_state" ]; then
      validate_state_directory "`$source_state"
      [ ! -e "`$target_state" ] && [ ! -L "`$target_state" ]
      mv "`$source_state" "`$target_state"
      find "`$target_state" -xdev -type d -exec chmod 0700 {} +
      find "`$target_state" -xdev -type f -exec chmod 0600 {} +
    fi
  done
  [ ! -f "`$target_root/ops/backup-db.sh" ] || chmod 0700 "`$target_root/ops/backup-db.sh"
}

finish_backup_schedule() {
  status="`$?"
  trap - EXIT
  if [ "`$backup_lock_held" = "1" ]; then
    flock -u 8 || true
    exec 8>&-
  fi
  if [ "`$schedule_restore_allowed" = "1" ]; then
    [ "`$backup_timer_enabled" != "1" ] || systemctl enable shamrai-db-backup.timer >/dev/null || true
    [ "`$backup_timer_active" != "1" ] || systemctl start shamrai-db-backup.timer || true
  else
    systemctl stop shamrai-db-backup.timer 2>/dev/null || true
    echo 'CRITICAL: backup timer left stopped because rollback state is incomplete.' >&2
  fi
  exit "`$status"
}
trap finish_backup_schedule EXIT

systemctl is-enabled --quiet shamrai-db-backup.timer 2>/dev/null && backup_timer_enabled=1 || true
systemctl is-active --quiet shamrai-db-backup.timer 2>/dev/null && backup_timer_active=1 || true
[ "`$backup_timer_active" != "1" ] || systemctl stop shamrai-db-backup.timer
if [ -d "$RemotePath/ops" ]; then
  validate_state_directory "$RemotePath/ops"
  exec 8>"$RemotePath/ops/backup.lock"
  flock -w 300 8
  backup_lock_held=1
fi

if [ -f "`$schema_upgrade_marker" ]; then
  post_migration_fail_closed=1
  echo 'Schema upgrade already committed; refusing pre-migration code rollback.' >&2
  if [ ! -d "$RemotePath" ]; then
    echo 'Canonical post-migration code path is missing; manual forward repair is required.' >&2
    exit 43
  fi
  cd "$RemotePath"
  docker compose -p "$ComposeProject" stop backend
elif [ -f "`$code_backup_marker" ]; then
  code_backup="`$(cat "`$code_backup_marker")"
  if [ "`$code_backup" != "$RemotePath.rollback.$remoteStageToken" ]; then
    echo 'Unexpected code rollback marker.' >&2
    exit 40
  fi
  if [ -d "`$code_backup" ]; then
    failed_path="$RemotePath.failed.manual.`$(date +%Y%m%d%H%M%S)"
    schedule_restore_allowed=0
    if [ -d "$RemotePath" ]; then
      mv "$RemotePath" "`$failed_path"
    fi
    mv "`$code_backup" "$RemotePath"
    move_rollback_state "`$failed_path" "$RemotePath"
    cd "$RemotePath"
    docker compose -p "$ComposeProject" up -d --build
    schedule_restore_allowed=1
  fi
else
  cd "$RemotePath"
  docker compose -p "$ComposeProject" up -d --no-deps backend
fi

if [ -f "`$web_backup_marker" ]; then
  web_backup="`$(cat "`$web_backup_marker")"
  if [ "`$web_backup" != "$PublicWebRoot.rollback.$remoteStageToken" ]; then
    echo 'Unexpected public web rollback marker.' >&2
    exit 41
  fi
  if [ -d "`$web_backup" ]; then
    web_failed="$PublicWebRoot.failed.manual.`$(date +%Y%m%d%H%M%S)"
    if [ -e "$PublicWebRoot" ]; then
      mv "$PublicWebRoot" "`$web_failed"
    fi
    mv "`$web_backup" "$PublicWebRoot"
  fi
fi

if [ -f "`$nginx_backup_marker" ]; then
  nginx_backup="`$(cat "`$nginx_backup_marker")"
  if [ -f "`$nginx_backup/shamrai.conf" ]; then
    cp -a "`$nginx_backup/shamrai.conf" /etc/nginx/sites-available/shamrai.conf
  else
    rm -f /etc/nginx/sites-available/shamrai.conf
  fi
  rm -f /etc/nginx/sites-enabled/shamrai.conf
  if [ -f "`$nginx_backup/enabled-manifest" ]; then
    while IFS="`$(printf '\t')" read -r kind name target; do
      [ -n "`$name" ] || continue
      rm -f "/etc/nginx/sites-enabled/`$name"
      if [ "`$kind" = "symlink" ]; then
        ln -s "`$target" "/etc/nginx/sites-enabled/`$name"
      elif [ "`$kind" = "file" ]; then
        cp -a "`$nginx_backup/enabled-`$name" "/etc/nginx/sites-enabled/`$name"
      fi
    done < "`$nginx_backup/enabled-manifest"
  fi
  nginx -t
  systemctl reload nginx
fi

if [ "`$post_migration_fail_closed" = "1" ]; then
  echo 'Post-migration rollback retained the new code and database; forward repair is required.' >&2
else
  curl -fsS http://127.0.0.1:8082/api/health
fi
"@
  Set-Utf8NoBomLfContent -Path $remoteRollbackScript -Content $remoteRollbackContent
}

Invoke-Step "Upload server guard" {
  Copy-ToRemoteChecked $remoteGuardScript "$remoteStage/guard.sh"
}

Invoke-Step "Server port/project guard" {
  Invoke-RemoteChecked "bash $(ConvertTo-ShellSingleQuoted "$remoteStage/guard.sh")"
}

Invoke-Step "Upload archives and nginx config" {
  Copy-ToRemoteChecked $repoArchive "$remoteStage/repo.tar.gz"
  Copy-ToRemoteChecked $webArchive "$remoteStage/web-dist.tar.gz"
  Copy-ToRemoteChecked $nginxConfig "$remoteStage/shamrai.conf"
  Copy-ToRemoteChecked $remoteDeployScript "$remoteStage/deploy.sh"
  Copy-ToRemoteChecked $remotePublishScript "$remoteStage/publish.sh"
  Copy-ToRemoteChecked $remoteRollbackScript "$remoteStage/rollback.sh"
}

try {
  $finalBackupArtifactKey = ""
  if ($LocalEncryptedBackupGate) {
    Invoke-Step "Prebuild exact candidate images before backend downtime" {
      $prebuildProjectSuffix = $remoteStageToken.Split('.')[-1].ToLowerInvariant()
      $prebuildCommand = @"
set -Eeuo pipefail
prebuild='$remoteStage/prebuild'
prebuild_project='shamrai-prebuild-$prebuildProjectSuffix'
cleanup_prebuild() {
  rm -rf -- "`$prebuild"
}
trap cleanup_prebuild EXIT
rm -rf -- "`$prebuild"
mkdir -p "`$prebuild"
chmod 0700 "`$prebuild"
tar -xzf '$remoteStage/repo.tar.gz' -C "`$prebuild"
for runtime_env in '$RemotePath/.env' '$RemotePath/backend/.env'; do
  test -f "`$runtime_env"
  test ! -L "`$runtime_env"
done
cp -- '$RemotePath/.env' "`$prebuild/.env"
cp -- '$RemotePath/backend/.env' "`$prebuild/backend/.env"
chmod 0600 "`$prebuild/.env" "`$prebuild/backend/.env"
cd "`$prebuild"
docker compose -p "`$prebuild_project" build backend frontend
backend_image="`$(docker image inspect -f '{{.Id}}' "`$prebuild_project-backend")"
frontend_image="`$(docker image inspect -f '{{.Id}}' "`$prebuild_project-frontend")"
case "`$backend_image:`$frontend_image" in sha256:*:sha256:*) ;; *) echo 'Unable to resolve prebuilt candidate images.' >&2; exit 83 ;; esac
printf '%s\n' "`$backend_image" > '$remoteStage/prebuilt-backend-image-id'
printf '%s\n' "`$frontend_image" > '$remoteStage/prebuilt-frontend-image-id'
chmod 0600 '$remoteStage/prebuilt-backend-image-id' '$remoteStage/prebuilt-frontend-image-id'
"@
      $prebuildFencedCommand = New-FencedRemoteCommand `
        -Command $prebuildCommand `
        -OwnerToken $remoteStageToken `
        -InitializeOwner
      Invoke-RemoteChecked $prebuildFencedCommand
    }

    Invoke-Step "Create and restore-drill the final local encrypted production backup" {
      $releaseBackupAttestationPath = Join-Path $Workspace ".deploy\shamrai-release-backup-attestation.json"
      $releaseBackupVerifier = Join-Path $Workspace "scripts\verify-shamrai-release-backup.ps1"
      & $releaseBackupVerifier `
        -Workspace $Workspace `
        -Server $Server `
        -HostKeyFingerprint $expectedFingerprintMatch.Value `
        -RemotePath $RemotePath `
        -ComposeProject $ComposeProject `
        -SshKeyPath $SshKeyPath `
        -KnownHostsPath $knownHostsPath `
        -AttestationPath $releaseBackupAttestationPath `
        -NoHostKeyScan `
        -LeaveBackendStopped
      $localBackupEvidence = Read-ReleaseGateBootstrap $releaseBackupAttestationPath
      if ([string](Get-RequiredJsonProperty $localBackupEvidence "status") -cne "passed" -or
          [bool](Get-RequiredJsonProperty $localBackupEvidence "plaintext_dump_persisted") -or
          -not [bool](Get-RequiredJsonProperty $localBackupEvidence "isolated_restore") -or
          [string](Get-RequiredJsonProperty $localBackupEvidence "alembic_revision") -cne "20260802_0041" -or
          [string](Get-RequiredJsonProperty $localBackupEvidence "normalized_schema_hash") -notmatch '^[0-9a-f]{64}$') {
        throw "Local encrypted release backup attestation is invalid."
      }
    }
  } else {
    Invoke-Step "Quiesce backend and create the final encrypted database backup" {
      $finalBackupCommand = @"
set -Eeuo pipefail
cd '$RemotePath'
systemctl is-enabled --quiet shamrai-db-backup.timer
systemctl is-active --quiet shamrai-db-backup.timer
test -x '$RemotePath/ops/backup-db.sh'
test -d '$RemotePath/db-backups/daily'
restart_timer_on_error() {
  systemctl start shamrai-db-backup.timer >/dev/null 2>&1 || true
}
trap restart_timer_on_error ERR
systemctl stop shamrai-db-backup.timer
for attempt in {1..300}; do
  if ! systemctl is-active --quiet shamrai-db-backup.service; then
    break
  fi
  sleep 1
done
if systemctl is-active --quiet shamrai-db-backup.service; then
  echo 'Timed out waiting for the active database backup service.' >&2
  exit 81
fi
previous_target="`$(readlink -- '$RemotePath/db-backups/latest.dump.age' 2>/dev/null || true)"
docker compose -p '$ComposeProject' stop backend
systemctl reset-failed shamrai-db-backup.service >/dev/null 2>&1 || true
systemctl start shamrai-db-backup.service
latest_target="`$(readlink -- '$RemotePath/db-backups/latest.dump.age')"
manifest_target="`$(readlink -- '$RemotePath/db-backups/latest.manifest.json')"
signature_target="`$(readlink -- '$RemotePath/db-backups/latest.manifest.sig')"
case "`$latest_target" in daily/shamrai-db.[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.dump.age) ;; *) echo 'Final backup link has an invalid target.' >&2; exit 82 ;; esac
[ "`$latest_target" != "`$previous_target" ] || { echo 'Final backup did not create a new encrypted artifact.' >&2; exit 82; }
expected_manifest="`${latest_target%.dump.age}.manifest.json"
expected_signature="`${latest_target%.dump.age}.manifest.sig"
[ "`$manifest_target" = "`$expected_manifest" ] || { echo 'Final backup manifest link is not bound to the ciphertext.' >&2; exit 82; }
[ "`$signature_target" = "`$expected_signature" ] || { echo 'Final backup signature link is not bound to the ciphertext.' >&2; exit 82; }
test -s "$RemotePath/db-backups/`$latest_target"
test -s "$RemotePath/db-backups/`$manifest_target"
test -s "$RemotePath/db-backups/`$signature_target"
systemctl start shamrai-db-backup.timer
systemctl is-active --quiet shamrai-db-backup.timer
trap - ERR
printf 'FINAL_BACKUP_BASENAME=%s\n' "`${latest_target##*/}"
"@
      $fencedBackupCommand = New-FencedRemoteCommand `
        -Command $finalBackupCommand `
        -OwnerToken $remoteStageToken `
        -InitializeOwner
      $backupOutput = Invoke-RemoteOutputChecked $fencedBackupCommand
      $backupMatches = @(
        $backupOutput -split "`n" |
          ForEach-Object { $_.Trim() } |
          Where-Object { $_ -match '^FINAL_BACKUP_BASENAME=shamrai-db\.\d{8}T\d{6}Z\.dump\.age$' }
      )
      if ($backupMatches.Count -ne 1) {
        throw "Final backup did not return one validated encrypted artifact name."
      }
      $backupBasename = $backupMatches[0].Substring("FINAL_BACKUP_BASENAME=".Length)
      $finalBackupArtifactKey = ($releaseGateContext.Prefix.Trim("/") + "/daily/" + $backupBasename).TrimStart("/")
      Write-Host "final_backup_artifact=$finalBackupArtifactKey"
    }

    Invoke-Step "Restore-drill the quiesced final backup before migration" {
      $restoreDrill = Join-Path $Workspace "scripts\run-shamrai-restore-drill.ps1"
      $restoreDrillArguments = @{
        Workspace = $Workspace
        Server = $Server
        HostKeyFingerprint = $expectedFingerprintMatch.Value
        RemotePath = $RemotePath
        ComposeProject = $ComposeProject
        ArtifactSource = "S3"
        OffHostArtifactKey = $finalBackupArtifactKey
        OffHostS3Bucket = $releaseGateContext.Bucket
        OffHostS3Prefix = $releaseGateContext.Prefix
        OffHostS3EndpointUrl = $releaseGateContext.EndpointUrl
        OffHostS3Region = $releaseGateContext.Region
        SshKeyPath = $SshKeyPath
        KnownHostsPath = $knownHostsPath
        MaximumLatestAgeHours = 1
        AttestationPath = $RestoreAttestationPath
        NoHostKeyScan = $true
      }
      & $restoreDrill @restoreDrillArguments
      $finalRestoreEvidence = Read-ReleaseGateBootstrap $RestoreAttestationPath
      if ([string](Get-RequiredJsonProperty $finalRestoreEvidence "artifact_key") -cne $finalBackupArtifactKey) {
        throw "Final restore attestation is not bound to the quiesced backup artifact."
      }
      $releaseGateContext = Assert-ReleaseGateAttestations `
        -S3Path $S3RetentionAttestationPath `
        -RestorePath $RestoreAttestationPath `
        -CiPath $CiAttestationPath `
        -ExpectedReleaseSha $releaseSha `
        -ExpectedRepository "inftverezovsky/Mini-Web-Shamrai-"
    }
  }

  Invoke-Step "Deploy exact code to the canonical preview under the release fence" {
    $deployCommand = New-FencedRemoteCommand `
      -Command "bash $(ConvertTo-ShellSingleQuoted "$remoteStage/deploy.sh")" `
      -OwnerToken $remoteStageToken
    Invoke-RemoteChecked $deployCommand
  }

  Invoke-Step "Verify preview APIs before any public frontend publish" {
    if ($LocalEncryptedBackupGate) {
      $candidateImageVerificationBlock = @"
expected_backend_image="`$(cat '$remoteStage/prebuilt-backend-image-id')"
expected_frontend_image="`$(cat '$remoteStage/prebuilt-frontend-image-id')"
running_backend="`$(docker inspect -f '{{.Image}}' "`$(docker compose -p '$ComposeProject' ps -q backend)")"
running_frontend="`$(docker inspect -f '{{.Image}}' "`$(docker compose -p '$ComposeProject' ps -q frontend)")"
[ "`$running_backend" = "`$expected_backend_image" ] || { echo 'Running backend differs from the prebuilt candidate.' >&2; exit 84; }
[ "`$running_frontend" = "`$expected_frontend_image" ] || { echo 'Running frontend differs from the prebuilt candidate.' >&2; exit 84; }
echo 'prebuilt_candidate_images_match_running_containers'
"@
    } else {
      $candidateImageVerificationBlock = "echo 's3_release_candidate_prebuild_not_requested'"
    }
    $previewVerification = @"
set -Eeuo pipefail
cd '$RemotePath'
docker compose -p '$ComposeProject' ps
$candidateImageVerificationBlock
curl -fsS http://127.0.0.1:8082/api/health >/dev/null
curl -fsS http://127.0.0.1:8082/api/ready >/dev/null
curl -fsS http://127.0.0.1:8082/api/version | python3 -c 'import json, sys; payload = json.load(sys.stdin); assert payload.get("git_sha") == sys.argv[1], payload; assert payload.get("build_time") == sys.argv[2], payload' '$releaseSha' '$releaseBuildTime'
curl -fsS http://127.0.0.1:8082/api/subscriptions/plans >/dev/null
curl -fsS http://127.0.0.1:8082/api/stats/global >/dev/null
docker compose -p '$ComposeProject' exec -T backend alembic current --check-heads
"@
    $previewCommand = New-FencedRemoteCommand `
      -Command $previewVerification `
      -OwnerToken $remoteStageToken
    Invoke-RemoteChecked $previewCommand
  }

  if (-not $PreviewOnly) {
    Invoke-Step "Publish public frontend after successful preview verification" {
      $publishCommand = New-FencedRemoteCommand `
        -Command "bash $(ConvertTo-ShellSingleQuoted "$remoteStage/publish.sh")" `
        -OwnerToken $remoteStageToken
      Invoke-RemoteChecked $publishCommand
    }
  }

  Invoke-Step "Health verification" {
    $expectedVkCode = ConvertTo-ShellSingleQuoted $ExpectedVkCallbackConfirmationCode
    if ($VkGroupId -notmatch "^\d+$") {
      throw "VK group id must be numeric for deploy verification."
    }
    $expectedAssetArgs = ($script:frontendPublicAssets | ForEach-Object { ConvertTo-ShellSingleQuoted $_ }) -join " "
    if (-not $PreviewOnly -and [string]::IsNullOrWhiteSpace($expectedAssetArgs)) {
      throw "No expected public frontend assets were captured from local dist."
    }
    $attestedS3Bucket = ConvertTo-ShellSingleQuoted $releaseGateContext.Bucket
    $attestedS3Prefix = ConvertTo-ShellSingleQuoted $releaseGateContext.Prefix
    $attestedS3EndpointUrl = ConvertTo-ShellSingleQuoted $releaseGateContext.EndpointUrl
    $attestedS3Region = ConvertTo-ShellSingleQuoted $releaseGateContext.Region
    $attestedWriterAccessKeySha256 = ConvertTo-ShellSingleQuoted $releaseGateContext.WriterAccessKeySha256
    $attestedManifestSigningPublicKeySha256 = ConvertTo-ShellSingleQuoted $releaseGateContext.ManifestSigningPublicKeySha256
    $vkCallbackPayload = ConvertTo-ShellSingleQuoted ('{"type":"confirmation","group_id":' + $VkGroupId + '}')
    if ($PreviewOnly) {
      $publicFrontendVerificationBlock = "echo 'preview_only_public_frontend_unchanged'"
    } else {
      $publicFrontendVerificationBlock = @"
curl -fsS https://shamra1.pro/release-manifest.json | python3 -c 'import json, sys; payload = json.load(sys.stdin); assert payload.get("git_sha") == sys.argv[1], payload; assert payload.get("build_time") == sys.argv[2], payload' '$releaseSha' '$releaseBuildTime'
html=""
for attempt in {1..15}; do
  html="`$(curl -fsS https://shamra1.pro/ || echo '')"
  if [ -n "`$html" ]; then
    break
  fi
  sleep 2
done

if [ -z "`$html" ]; then
  echo "Failed to fetch HTML after retries!"
  exit 1
fi

fail_count=0
for asset in $expectedAssetArgs; do
  echo "Checking asset: `$asset"
  if ! printf '%s' "`$html" | grep -F "`$asset" >/dev/null; then
    echo "Asset `$asset not found in HTML!"
    fail_count=`$((fail_count + 1))
  fi
  if ! curl -fsS -I "https://shamra1.pro/`$asset" >/dev/null; then
    echo "Asset `$asset not found on server!"
    fail_count=`$((fail_count + 1))
  fi
done

if [ "`$fail_count" -gt 0 ]; then
  echo "Asset validation failed with `$fail_count errors!"
  exit 1
fi
echo 'public_frontend_assets_match_dist'
"@
    }
    $remote = @"
set -e
cd '$RemotePath'
    docker compose -p '$ComposeProject' ps
    curl -fsS http://127.0.0.1:8082/api/health
    curl -fsS http://127.0.0.1:8082/api/ready
curl -fsS http://127.0.0.1:8082/api/version | python3 -c 'import json, sys; payload = json.load(sys.stdin); assert payload.get("git_sha") == sys.argv[1], payload; assert payload.get("build_time") == sys.argv[2], payload' '$releaseSha' '$releaseBuildTime'
if [ '$([int][bool]$LocalEncryptedBackupGate)' = '1' ]; then
  echo 'local_encrypted_backup_restore_gate_ok'
else
systemctl is-enabled --quiet shamrai-db-backup.timer
systemctl is-active --quiet shamrai-db-backup.timer
test -x '$RemotePath/ops/backup-db.sh'
test -f '$RemotePath/ops/backup.env'
test "`$(stat -c '%a:%u' -- '$RemotePath/ops/backup-db.sh')" = '700:0'
test "`$(stat -c '%a:%u' -- '$RemotePath/ops/backup.env')" = '600:0'
test -x /usr/local/sbin/shamrai-backup-with-systemd-credentials
test "`$(stat -c '%a:%u' -- /usr/local/sbin/shamrai-backup-with-systemd-credentials)" = '700:0'
for credential_file in \
  /etc/credstore.encrypted/shamrai-backup-aws-access-key-id.cred \
  /etc/credstore.encrypted/shamrai-backup-aws-secret-access-key.cred \
  /etc/credstore.encrypted/shamrai-backup-manifest-signing-private-key.cred; do
  test -s "`$credential_file"
  test ! -L "`$credential_file"
  test "`$(stat -c '%a:%u' -- "`$credential_file")" = '600:0'
done
service_definition="`$(systemctl cat shamrai-db-backup.service)"
printf '%s\n' "`$service_definition" | grep -Fx 'LoadCredentialEncrypted=aws_access_key_id:/etc/credstore.encrypted/shamrai-backup-aws-access-key-id.cred' >/dev/null
printf '%s\n' "`$service_definition" | grep -Fx 'LoadCredentialEncrypted=aws_secret_access_key:/etc/credstore.encrypted/shamrai-backup-aws-secret-access-key.cred' >/dev/null
printf '%s\n' "`$service_definition" | grep -Fx 'LoadCredentialEncrypted=manifest_signing_private_key:/etc/credstore.encrypted/shamrai-backup-manifest-signing-private-key.cred' >/dev/null
printf '%s\n' "`$service_definition" | grep -Fx 'ExecStart=/usr/local/sbin/shamrai-backup-with-systemd-credentials $RemotePath/ops/backup-db.sh' >/dev/null
set -a
source '$RemotePath/ops/backup.env'
set +a
test "`$SHAMRAI_BACKUP_S3_BUCKET" = $attestedS3Bucket
test "`$SHAMRAI_BACKUP_S3_PREFIX" = $attestedS3Prefix
test "`$SHAMRAI_BACKUP_S3_ENDPOINT_URL" = $attestedS3EndpointUrl
test "`$SHAMRAI_BACKUP_S3_REGION" = $attestedS3Region
writer_access_key_sha256="`$(systemd-creds decrypt --name=aws_access_key_id /etc/credstore.encrypted/shamrai-backup-aws-access-key-id.cred - | sha256sum | cut -d ' ' -f 1)"
test "`$writer_access_key_sha256" = $attestedWriterAccessKeySha256
configured_manifest_signing_public_key_sha256="`$(printf '%s' "`$SHAMRAI_BACKUP_MANIFEST_SIGNING_PUBLIC_KEY_BASE64" | base64 --decode | sha256sum | cut -d ' ' -f 1)"
derived_manifest_signing_public_key_sha256="`$(systemd-creds decrypt --name=manifest_signing_private_key /etc/credstore.encrypted/shamrai-backup-manifest-signing-private-key.cred - | openssl pkey -pubout -outform DER 2>/dev/null | sha256sum | cut -d ' ' -f 1)"
test "`$configured_manifest_signing_public_key_sha256" = $attestedManifestSigningPublicKeySha256
test "`$derived_manifest_signing_public_key_sha256" = $attestedManifestSigningPublicKeySha256
echo 'backup_schedule_state_ok'
fi
docker compose -p '$ComposeProject' exec -T backend python - <<'PY'
from src.core.config import settings
from src.services.telegram_bot import call_telegram_api
from src.services.vk_delivery import probe_vk_api, vk_delivery_configured, vk_group_id

errors = []

get_me = call_telegram_api("getMe", {}, settings.TELEGRAM_API_TIMEOUT_SECONDS, 1)
webhook_info = call_telegram_api("getWebhookInfo", {}, settings.TELEGRAM_API_TIMEOUT_SECONDS, 1)
if not settings.has_real_telegram_token:
    errors.append("telegram token missing")
if not get_me.get("ok"):
    errors.append("telegram getMe failed")
if not webhook_info.get("ok"):
    errors.append("telegram getWebhookInfo failed")

if settings.TELEGRAM_USE_POLLING:
    print("telegram_delivery_probe_ok mode=polling")
else:
    expected = f"{settings.API_BASE_URL.rstrip('/')}/api/telegram/webhook"
    actual = (webhook_info.get("result") or {}).get("url") or ""
    if expected and actual != expected:
        errors.append("telegram webhook mismatch")
    print("telegram_delivery_probe_ok mode=webhook")

if not settings.VK_ID_APP_ID.strip() or not settings.VK_ID_REDIRECT_URI.strip():
    errors.append("vk id config missing")
if not vk_group_id() or not vk_delivery_configured():
    errors.append("vk delivery config missing")
if not probe_vk_api():
    errors.append("vk api probe failed")

if errors:
    raise SystemExit("; ".join(errors))
print("vk_delivery_probe_ok")
PY
expected_vk_callback_confirmation=$expectedVkCode
vk_callback_payload=$vkCallbackPayload
runtime_vk_callback_confirmation="`$(python3 - <<'PY'
from pathlib import Path
path = Path("backend/.env")
values = []
if path.exists():
    for line in path.read_text().splitlines():
        if line.startswith("VK_CALLBACK_CONFIRMATION_CODE="):
            values.append(line.split("=", 1)[1].strip())
if len(values) > 1:
    raise SystemExit("duplicate VK_CALLBACK_CONFIRMATION_CODE entries in backend/.env")
print(values[-1] if values else "")
PY
)"
if [ -z "`$runtime_vk_callback_confirmation" ]; then
  echo 'VK callback confirmation code is missing in backend runtime env.' >&2
  exit 30
fi
actual_runtime_vk_callback_confirmation="`$(curl -fsS -X POST -H 'Content-Type: application/json' --data "`$vk_callback_payload" https://shamra1.pro/api/vk/callback)"
if [ "`$actual_runtime_vk_callback_confirmation" != "`$runtime_vk_callback_confirmation" ]; then
  echo 'VK callback runtime confirmation mismatch.' >&2
  exit 30
fi
echo 'vk_callback_runtime_confirmation_ok'
if [ -n "`$expected_vk_callback_confirmation" ]; then
  actual_vk_callback_confirmation="`$actual_runtime_vk_callback_confirmation"
  if [ "`$actual_vk_callback_confirmation" != "`$expected_vk_callback_confirmation" ]; then
    echo 'VK callback confirmation mismatch.' >&2
    exit 31
  fi
  echo 'vk_callback_dashboard_confirmation_ok'
else
  echo 'vk_callback_dashboard_confirmation_skipped expected_code_not_provided'
fi
curl -I -fsS http://shamra1.pro/ | head -n 8
curl -I -fsS https://shamra1.pro/ | head -n 8
curl -I -fsS https://shamra1.pro/app/ | head -n 8
curl -fsS https://shamra1.pro/api/ready >/dev/null
curl -fsS https://shamra1.pro/api/version | python3 -c 'import json, sys; payload = json.load(sys.stdin); assert payload.get("git_sha") == sys.argv[1], payload; assert payload.get("build_time") == sys.argv[2], payload' '$releaseSha' '$releaseBuildTime'
$publicFrontendVerificationBlock
"@
    $normalizedRemote = ((($remote -replace "`r`n", "`n") -replace "`r", "").TrimEnd("`n")) + "`n"
    $encodedRemote = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($normalizedRemote))
    $remoteCommand = "bash -c `"`$(printf '%s' '$encodedRemote' | base64 -d)`""
    $verifiedLifecycleCommand = New-FencedRemoteCommand `
      -Command $remoteCommand `
      -OwnerToken $remoteStageToken `
      -ReleaseOwner
    Invoke-RemoteChecked $verifiedLifecycleCommand
  }
} catch {
  Write-Warning "Deploy verification failed; attempting remote rollback."
  try {
    $rollbackLifecycleCommand = New-FencedRemoteCommand `
      -Command "bash $(ConvertTo-ShellSingleQuoted "$remoteStage/rollback.sh")" `
      -OwnerToken $remoteStageToken `
      -ReleaseOwner
    Invoke-RemoteChecked $rollbackLifecycleCommand
  } catch {
    Write-Warning "Remote rollback command also failed. Manual server inspection is required."
  }
  Write-Warning "Root-private recovery state was retained at $remoteStage for manual inspection."
  throw
}

Invoke-Step "Prune exact snapshots after committed release" {
  $expectedCodeBackup = "$RemotePath.rollback.$remoteStageToken"
  $expectedWebBackup = "$PublicWebRoot.rollback.$remoteStageToken"
  $cleanupCommand = "rm -rf -- " +
    (ConvertTo-ShellSingleQuoted $expectedCodeBackup) + " " +
    (ConvertTo-ShellSingleQuoted $expectedWebBackup)
  try {
    Invoke-RemoteChecked $cleanupCommand
  } catch {
    throw "Release is committed and healthy, but exact rollback snapshot cleanup failed. Do not roll back automatically."
  }
}

Invoke-Step "Remove validated remote release stage" {
  Invoke-RemoteChecked "rm -rf -- $(ConvertTo-ShellSingleQuoted $remoteStage)"
}

Write-Host ""
if ($PreviewOnly) {
  Write-Host "Canonical Shamrai preview deploy finished; public frontend was not published." -ForegroundColor Green
  Write-Host "Preview: http://82.147.67.245:8082/"
} else {
  Write-Host "Public Shamrai web deploy finished." -ForegroundColor Green
  Write-Host "Site: https://shamra1.pro/"
  Write-Host "Mini App: https://shamra1.pro/app"
}
} finally {
  $localDeployLockStream.Dispose()
}
