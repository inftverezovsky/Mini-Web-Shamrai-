[CmdletBinding()]
param(
  [Parameter(Mandatory)]
  [Guid]$PaymentAttemptId,

  [Parameter(Mandatory)]
  [ValidateRange(1, [int]::MaxValue)]
  [int]$HiddenPlanId,

  [Parameter(Mandatory)]
  [ValidatePattern('^[0-9a-fA-F]{40}$')]
  [string]$ExpectedGitSha,

  [ValidateNotNullOrEmpty()]
  [string]$BaseUrl = "https://shamra1.pro",

  [Parameter(Mandatory)]
  [datetimeoffset]$MinimumEvidenceTimeUtc,

  [ValidateRange(1, 120)]
  [int]$RequestTimeoutSeconds = 20,

  [string]$AttestationPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$workspace = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ([string]::IsNullOrWhiteSpace($AttestationPath)) {
  $AttestationPath = Join-Path $workspace ".deploy\hidden-flat-e2e-attestation.json"
}
$AttestationPath = [IO.Path]::GetFullPath($AttestationPath)

function Remove-StaleAttestation {
  if (Test-Path -LiteralPath $AttestationPath -PathType Leaf) {
    Remove-Item -LiteralPath $AttestationPath -Force
  }
}

function Get-JsonApiResponse {
  param(
    [Parameter(Mandatory)]
    [System.Net.Http.HttpClient]$Client,

    [Parameter(Mandatory)]
    [Uri]$Uri,

    [Parameter(Mandatory)]
    [bool]$Authenticated,

    [string]$Jwt
  )

  $request = [System.Net.Http.HttpRequestMessage]::new([System.Net.Http.HttpMethod]::Get, $Uri)
  try {
    $request.Headers.Accept.ParseAdd("application/json")
    if ($Authenticated) {
      $request.Headers.Authorization = [System.Net.Http.Headers.AuthenticationHeaderValue]::new("Bearer", $Jwt)
    }
    $response = $Client.SendAsync($request).GetAwaiter().GetResult()
    try {
      if (-not $response.IsSuccessStatusCode) {
        throw "Hidden flat E2E request failed with HTTP $([int]$response.StatusCode) for $($Uri.AbsolutePath)."
      }
      $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
      if ([Text.Encoding]::UTF8.GetByteCount($content) -gt 2MB) {
        throw "Hidden flat E2E response is unexpectedly large for $($Uri.AbsolutePath)."
      }
      try {
        return $content | ConvertFrom-Json
      } catch {
        throw "Hidden flat E2E response is not valid JSON for $($Uri.AbsolutePath)."
      }
    } finally {
      $response.Dispose()
    }
  } finally {
    $request.Dispose()
  }
}

try {
  Remove-StaleAttestation

  $jwt = [string]$env:SHAMRAI_HIDDEN_GATE_JWT
  if (
    [string]::IsNullOrWhiteSpace($jwt) -or
    $jwt.Length -gt 8192 -or
    $jwt -notmatch '^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$'
  ) {
    throw "SHAMRAI_HIDDEN_GATE_JWT must contain a valid runtime JWT. Do not store it in repository files."
  }

  $baseUri = [Uri]$BaseUrl
  if (-not $baseUri.IsAbsoluteUri -or $baseUri.Query -or $baseUri.Fragment -or $baseUri.UserInfo) {
    throw "BaseUrl must be an absolute origin without credentials, query, or fragment."
  }
  $isLoopback = $baseUri.IsLoopback -or $baseUri.Host -in @("localhost", "127.0.0.1", "::1")
  if ($baseUri.Scheme -ne "https" -and -not ($baseUri.Scheme -eq "http" -and $isLoopback)) {
    throw "BaseUrl must use HTTPS; HTTP is accepted only for a loopback test origin."
  }
  $origin = $baseUri.GetLeftPart([UriPartial]::Authority).TrimEnd("/")

  $handler = [System.Net.Http.HttpClientHandler]::new()
  $handler.AllowAutoRedirect = $false
  $client = [System.Net.Http.HttpClient]::new($handler)
  try {
    $client.Timeout = [TimeSpan]::FromSeconds($RequestTimeoutSeconds)
    $version = Get-JsonApiResponse -Client $client -Uri ([Uri]"$origin/api/version") -Authenticated $false
    $plans = @(Get-JsonApiResponse -Client $client -Uri ([Uri]"$origin/api/subscriptions/plans") -Authenticated $true -Jwt $jwt)
    $attempt = Get-JsonApiResponse -Client $client -Uri ([Uri]"$origin/api/payments/attempts/$($PaymentAttemptId.ToString().ToLowerInvariant())") -Authenticated $true -Jwt $jwt
    $flat = Get-JsonApiResponse -Client $client -Uri ([Uri]"$origin/api/subscriptions/flats/current") -Authenticated $true -Jwt $jwt
  } finally {
    $client.Dispose()
    $handler.Dispose()
  }

  Import-Module (Join-Path $PSScriptRoot "lib\HiddenFlatE2EGate.psm1") -Force
  $attestation = Assert-HiddenFlatE2EApiState `
    -Version $version `
    -Plans $plans `
    -PaymentAttempt $attempt `
    -FlatSubscription $flat `
    -ExpectedGitSha $ExpectedGitSha.ToLowerInvariant() `
    -HiddenPlanId $HiddenPlanId `
    -ExpectedPaymentAttemptId $PaymentAttemptId `
    -BaseUrl $origin `
    -MinimumEvidenceTimeUtc $MinimumEvidenceTimeUtc

  $parentDirectory = Split-Path -Parent $AttestationPath
  [IO.Directory]::CreateDirectory($parentDirectory) | Out-Null
  $temporaryPath = Join-Path $parentDirectory ([IO.Path]::GetRandomFileName())
  try {
    $json = $attestation | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText($temporaryPath, "$json`n", [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporaryPath -Destination $AttestationPath -Force
  } finally {
    if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
      Remove-Item -LiteralPath $temporaryPath -Force
    }
  }

  Write-Host "hidden_flat_e2e_attestation=$AttestationPath"
} catch {
  Remove-StaleAttestation
  throw
} finally {
  $jwt = $null
}
