param(
  [string]$Server = "root@82.147.67.245",
  [string]$HostKey = "ssh-ed25519 255 SHA256:xdVRtRaXWqK6eAIsE3VwD0o2H6GJDcCm65L1ZUBjuMw",
  [string]$RemotePath = "/opt/shamrai-mini-app",
  [string]$ComposeProject = "shamrai",
  [string]$VkGroupId = "239419819",
  [string]$ExpectedVkCallbackSecret = $env:SHAMRAI_EXPECTED_VK_CALLBACK_SECRET
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

if ([string]::IsNullOrWhiteSpace($ExpectedVkCallbackSecret)) {
  throw "Set SHAMRAI_EXPECTED_VK_CALLBACK_SECRET for this run. Do not store it in files."
}

if ($VkGroupId -notmatch "^\d+$") {
  throw "VK group id must be numeric."
}

$secretQuoted = ConvertTo-ShellSingleQuoted $ExpectedVkCallbackSecret
$remotePathQuoted = ConvertTo-ShellSingleQuoted $RemotePath
$composeProjectQuoted = ConvertTo-ShellSingleQuoted $ComposeProject
$vkGroupIdQuoted = ConvertTo-ShellSingleQuoted $VkGroupId

$remote = @"
set -e
CANON_PATH=$remotePathQuoted
CANON_PROJECT=$composeProjectQuoted
CANON_PORT='8082'
VK_GROUP_ID=$vkGroupIdQuoted
NEXT_VK_CALLBACK_SECRET=$secretQuoted

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

python3 - "`$NEXT_VK_CALLBACK_SECRET" backend/.env <<'PY'
from pathlib import Path
import sys

next_secret = sys.argv[1]
path = Path(sys.argv[2])
lines = path.read_text().splitlines() if path.exists() else []
out = []
replaced_count = 0
previous_lengths = []
for line in lines:
    if line.startswith("VK_CALLBACK_SECRET="):
        previous = line.split("=", 1)[1]
        previous_lengths.append(str(len(previous)))
        replaced_count += 1
        if replaced_count == 1:
            out.append(f"VK_CALLBACK_SECRET={next_secret}")
        continue
    out.append(line)
if replaced_count == 0:
    out.append(f"VK_CALLBACK_SECRET={next_secret}")
path.write_text("\n".join(out).rstrip() + "\n")
print(
    "vk_callback_secret_env_updated "
    f"replaced_count={replaced_count} previous_lens={','.join(previous_lengths) or 'none'} new_len={len(next_secret)}"
)
PY

docker compose -p "`$CANON_PROJECT" up -d --force-recreate backend
curl -fsS "http://127.0.0.1:`$CANON_PORT/api/health" >/dev/null
docker compose -p "`$CANON_PROJECT" exec -T backend python - <<'PY'
from src.core.config import settings
from src.services.vk_delivery import vk_delivery_configured, vk_group_id

errors = []
if not settings.VK_ID_APP_ID.strip() or not settings.VK_ID_REDIRECT_URI.strip():
    errors.append("vk id config missing")
if not vk_group_id() or not vk_delivery_configured():
    errors.append("vk delivery config missing")
if not settings.VK_CALLBACK_SECRET.strip():
    errors.append("vk callback secret missing")
if errors:
    raise SystemExit("; ".join(errors))
print("vk_runtime_config_probe_ok")
PY

docker compose -p "`$CANON_PROJECT" exec -T backend python - <<'PY'
import asyncio
from src.core.config import settings
from src.api import vk_callback

class FakeRequest:
    def __init__(self, payload):
        self.payload = payload
    async def json(self):
        return self.payload

async def main():
    response = await vk_callback.vk_callback(FakeRequest({
        "type": "wall_post_new",
        "group_id": 239419819,
        "secret": settings.VK_CALLBACK_SECRET.strip(),
        "object": {},
    }))
    if response.status_code != 200:
        raise SystemExit(f"synthetic callback secret check failed: {response.status_code}")
    print("vk_callback_secret_ok")

asyncio.run(main())
PY
echo "vk_callback_secret_mask `$(mask_value "`$NEXT_VK_CALLBACK_SECRET")"
"@

$normalizedRemote = ((($remote -replace "`r`n", "`n") -replace "`r", "").TrimEnd("`n")) + "`n"
$encodedRemote = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($normalizedRemote))
$remoteCommand = "printf '%s' '$encodedRemote' | base64 -d | sh"
Invoke-NativeChecked $plink -ssh $Server -pw $password -batch -no-antispoof -hostkey $HostKey $remoteCommand
