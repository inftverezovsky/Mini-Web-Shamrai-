param(
  [string]$Server = "root@82.147.67.245",
  [string]$HostKey = "ssh-ed25519 255 SHA256:xdVRtRaXWqK6eAIsE3VwD0o2H6GJDcCm65L1ZUBjuMw",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [string]$VkGroupId = "239419819",
  [string]$ExpectedVkCallbackConfirmationCode = $env:SHAMRAI_EXPECTED_VK_CALLBACK_CONFIRMATION_CODE
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

$plink = Find-Tool @("C:\Program Files\PuTTY\plink.exe", "plink.exe")
if (-not $plink) {
  throw "PuTTY plink not found."
}

$password = $env:SHAMRAI_SSH_PASSWORD
if ([string]::IsNullOrWhiteSpace($password)) {
  throw "Set SHAMRAI_SSH_PASSWORD for this run. Do not store it in files."
}

if ([string]::IsNullOrWhiteSpace($ExpectedVkCallbackConfirmationCode)) {
  throw "Set SHAMRAI_EXPECTED_VK_CALLBACK_CONFIRMATION_CODE for this run. Do not store it in files."
}

if ($VkGroupId -notmatch "^\d+$") {
  throw "VK group id must be numeric."
}

$expectedQuoted = ConvertTo-ShellSingleQuoted $ExpectedVkCallbackConfirmationCode
$remotePathQuoted = ConvertTo-ShellSingleQuoted $RemotePath
$composeProjectQuoted = ConvertTo-ShellSingleQuoted $ComposeProject
$vkGroupIdQuoted = ConvertTo-ShellSingleQuoted $VkGroupId

$remote = @"
set -e
CANON_PATH=$remotePathQuoted
CANON_PROJECT=$composeProjectQuoted
CANON_PORT='8082'
VK_GROUP_ID=$vkGroupIdQuoted
EXPECTED_VK_CALLBACK_CONFIRMATION_CODE=$expectedQuoted

mask_value() {
  value="`$1"
  length="`$(printf '%s' "`$value" | wc -c | tr -d ' ')"
  if [ "`$length" -le 4 ]; then
    printf '<short> len=%s' "`$length"
    return
  fi
  prefix="`$(printf '%s' "`$value" | cut -c1-2)"
  suffix="`$(printf '%s' "`$value" | rev | cut -c1-2 | rev)"
  printf '%s******%s len=%s' "`$prefix" "`$suffix" "`$length"
}

echo 'Shamrai server inventory:'
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.project.working_dir"}}' || true
echo 'Listening ports 80/443/8000/8081/8082:'
ss -ltnp | grep -E ':(80|443|8000|8081|8082)\b' || true

conflicts="`$(
  docker ps --format '{{.Names}}\t{{.Ports}}\t{{.Label "com.docker.compose.project"}}' | while IFS="`$(printf '\t')" read -r name ports project; do
    case "`$ports" in
      *":`$CANON_PORT->"*)
        if [ "`$project" != "`$CANON_PROJECT" ]; then
          printf '%s\t%s\t%s\n' "`$name" "`$ports" "`$project"
        fi
        ;;
    esac
  done
)"
if [ -n "`$conflicts" ]; then
  echo "Port `$CANON_PORT is owned by a non-canonical container. Refusing to patch VK runtime." >&2
  echo "`$conflicts" >&2
  exit 20
fi

if [ ! -d "`$CANON_PATH" ]; then
  echo "Canonical path is missing: `$CANON_PATH" >&2
  exit 21
fi

cd "`$CANON_PATH"
if [ ! -f backend/.env ]; then
  cp backend/.env.example backend/.env
fi

python3 - "`$EXPECTED_VK_CALLBACK_CONFIRMATION_CODE" backend/.env <<'PY'
from pathlib import Path
import sys

expected = sys.argv[1]
path = Path(sys.argv[2])
lines = path.read_text().splitlines() if path.exists() else []
out = []
replaced_count = 0
previous_lengths = []
for line in lines:
    if line.startswith("VK_CALLBACK_CONFIRMATION_CODE="):
        previous = line.split("=", 1)[1]
        previous_lengths.append(str(len(previous)))
        replaced_count += 1
        if replaced_count == 1:
            out.append(f"VK_CALLBACK_CONFIRMATION_CODE={expected}")
        continue
    out.append(line)
if replaced_count == 0:
    out.append(f"VK_CALLBACK_CONFIRMATION_CODE={expected}")
path.write_text("\n".join(out).rstrip() + "\n")
print(
    "vk_callback_confirmation_env_updated "
    f"replaced_count={replaced_count} previous_lens={','.join(previous_lengths) or 'none'} new_len={len(expected)}"
)
PY

docker compose -p "`$CANON_PROJECT" up -d --force-recreate backend
curl -fsS "http://127.0.0.1:`$CANON_PORT/api/health" >/dev/null
curl -fsS "http://127.0.0.1:`$CANON_PORT/api/health/vk" >/dev/null

payload='{"type":"confirmation","group_id":'"`$VK_GROUP_ID"'}'
actual_local="`$(curl -fsS -X POST -H 'Content-Type: application/json' --data "`$payload" "http://127.0.0.1:`$CANON_PORT/api/vk/callback")"
if [ "`$actual_local" != "`$EXPECTED_VK_CALLBACK_CONFIRMATION_CODE" ]; then
  echo "Local VK callback confirmation mismatch after repair. actual=`$(mask_value "`$actual_local") expected=`$(mask_value "`$EXPECTED_VK_CALLBACK_CONFIRMATION_CODE")" >&2
  exit 31
fi

actual_public="`$(curl -fsS -X POST -H 'Content-Type: application/json' --data "`$payload" "https://shamra1.pro/api/vk/callback")"
if [ "`$actual_public" != "`$EXPECTED_VK_CALLBACK_CONFIRMATION_CODE" ]; then
  echo "Public VK callback confirmation mismatch after repair. actual=`$(mask_value "`$actual_public") expected=`$(mask_value "`$EXPECTED_VK_CALLBACK_CONFIRMATION_CODE")" >&2
  exit 32
fi

echo "vk_callback_confirmation_ok `$(mask_value "`$actual_public")"
"@

$normalizedRemote = ((($remote -replace "`r`n", "`n") -replace "`r", "").TrimEnd("`n")) + "`n"
$encodedRemote = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($normalizedRemote))
$remoteCommand = "printf '%s' '$encodedRemote' | base64 -d | sh"
Invoke-NativeChecked $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey $remoteCommand
