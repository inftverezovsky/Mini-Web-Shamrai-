$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$modulePath = Join-Path $repoRoot "scripts\lib\HiddenFlatE2EGate.psm1"
$scriptPath = Join-Path $repoRoot "scripts\verify-shamrai-hidden-flat-e2e.ps1"
Import-Module $modulePath -Force

$script:ExpectedSha = "0123456789abcdef0123456789abcdef01234567"
$script:ExpectedAttemptId = [Guid]"7f8fd4c8-dc22-49eb-a6af-bd0dcbb8f8bc"
$script:MinimumEvidenceTime = [datetimeoffset]"2026-08-02T11:00:00Z"

function New-ValidGateState {
  param(
    [string]$PaymentStatus = "succeeded",
    [bool]$PlanHidden = $true,
    [string]$FlatStatus = "completed",
    [string]$ProfitFlats = "1.250000",
    [int]$PendingBets = 0,
    [string]$BetStatus = "win",
    [string]$CreditEventType = "subscription_purchase"
  )

  $flatId = "cb114435-ad49-48b2-8715-8b72a48b41e8"
  $flat = [pscustomobject]@{
    id = $flatId
    status = $FlatStatus
    flat_amount_rub = "10000.00"
    target_flats = "1.00"
    profit_flats = $ProfitFlats
    remaining_flats = "0.00"
    pending_bets = $PendingBets
    revision = 5
    created_at = "2026-08-02T11:05:00Z"
    updated_at = "2026-08-02T11:15:00Z"
    completed_at = "2026-08-02T11:15:00Z"
    credits = @(
      [pscustomobject]@{
        event_type = $CreditEventType
        delta_target_flats = "1.00"
        created_at = "2026-08-02T11:05:30Z"
      }
    )
    bets = @(
      [pscustomobject]@{
        status = $BetStatus
        profit_flats = "1.250000"
        taken_at = "2026-08-02T11:10:00Z"
        settled_at = "2026-08-02T11:14:00Z"
      }
    )
  }

  return [pscustomobject]@{
    Version = [pscustomobject]@{
      git_sha = $script:ExpectedSha
      build_time = "2026-08-02T10:20:30Z"
    }
    Plans = @(
      [pscustomobject]@{
        id = 77
        entitlement_type = "flat"
        is_hidden = $PlanHidden
        is_active = $true
      }
    )
    Attempt = [pscustomobject]@{
      attempt_id = $script:ExpectedAttemptId.ToString()
      provider = "yookassa"
      payment_status = $PaymentStatus
      checkout_state = "completed"
      purchase_type = "subscription"
      plan_id = 77
      entitlement_type = "flat"
      target_flats = "1.00"
      amount = "1490.00"
      currency = "RUB"
      flat_setup_required = $false
      flat_subscription = [pscustomobject]@{
        id = $flatId
      }
      created_at = "2026-08-02T11:04:00Z"
      updated_at = "2026-08-02T11:05:30Z"
    }
    Flat = $flat
  }
}

function Invoke-Gate {
  param([Parameter(Mandatory)][object]$State)
  return Assert-HiddenFlatE2EApiState `
    -Version $State.Version `
    -Plans @($State.Plans) `
    -PaymentAttempt $State.Attempt `
    -FlatSubscription $State.Flat `
    -ExpectedGitSha $script:ExpectedSha `
    -HiddenPlanId 77 `
    -ExpectedPaymentAttemptId $script:ExpectedAttemptId `
    -BaseUrl "https://shamra1.pro" `
    -MinimumEvidenceTimeUtc $script:MinimumEvidenceTime
}

function Assert-ThrowsLike {
  param(
    [Parameter(Mandatory)][scriptblock]$Action,
    [Parameter(Mandatory)][string]$ExpectedFragment
  )
  try {
    & $Action
  } catch {
    if (-not $_.Exception.Message.Contains($ExpectedFragment)) {
      throw "Expected error containing '$ExpectedFragment', got '$($_.Exception.Message)'."
    }
    return
  }
  throw "Expected action to fail with '$ExpectedFragment'."
}

$attestation = Invoke-Gate -State (New-ValidGateState)
if ($attestation.status -ne "passed") { throw "Expected a passed attestation." }
if ($attestation.git_sha -ne $script:ExpectedSha) { throw "Attestation SHA mismatch." }
if ($attestation.payment_attempt_id -ne $script:ExpectedAttemptId.ToString()) { throw "Attempt id mismatch." }
if ($attestation.hidden_plan_id -ne 77) { throw "Plan id mismatch." }
if ($attestation.settled_bet_count -ne 1) { throw "Settled bet count mismatch." }
if ($attestation.minimum_evidence_time -ne "2026-08-02T11:00:00Z") { throw "Minimum evidence time mismatch." }
$serialized = $attestation | ConvertTo-Json -Depth 5
if ($serialized.Contains("header.payload.signature")) { throw "An authentication token entered the attestation." }

Assert-ThrowsLike -ExpectedFragment "payment status" -Action {
  Invoke-Gate -State (New-ValidGateState -PaymentStatus "pending")
}
Assert-ThrowsLike -ExpectedFragment "not hidden" -Action {
  Invoke-Gate -State (New-ValidGateState -PlanHidden $false)
}
Assert-ThrowsLike -ExpectedFragment "internally inconsistent" -Action {
  Invoke-Gate -State (New-ValidGateState -ProfitFlats "0.990000")
}
Assert-ThrowsLike -ExpectedFragment "not settled" -Action {
  Invoke-Gate -State (New-ValidGateState -BetStatus "pending")
}
Assert-ThrowsLike -ExpectedFragment "one purchase credit" -Action {
  Invoke-Gate -State (New-ValidGateState -CreditEventType "manual_credit")
}
$staleState = New-ValidGateState
$staleState.Attempt.created_at = "2026-08-02T10:59:59Z"
Assert-ThrowsLike -ExpectedFragment "predates this release" -Action {
  Invoke-Gate -State $staleState
}

$temporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) ("shamrai-hidden-gate-test-" + [Guid]::NewGuid().ToString("N"))
[IO.Directory]::CreateDirectory($temporaryDirectory) | Out-Null
$temporaryAttestation = Join-Path $temporaryDirectory "stale.json"
[IO.File]::WriteAllText($temporaryAttestation, "stale", [Text.UTF8Encoding]::new($false))
$previousJwt = $env:SHAMRAI_HIDDEN_GATE_JWT
try {
  $env:SHAMRAI_HIDDEN_GATE_JWT = "not-a-jwt"
  Assert-ThrowsLike -ExpectedFragment "valid runtime JWT" -Action {
    & $scriptPath `
      -PaymentAttemptId $script:ExpectedAttemptId `
      -HiddenPlanId 77 `
      -ExpectedGitSha $script:ExpectedSha `
      -MinimumEvidenceTimeUtc $script:MinimumEvidenceTime `
      -BaseUrl "http://127.0.0.1:9" `
      -AttestationPath $temporaryAttestation
  }
  if (Test-Path -LiteralPath $temporaryAttestation) {
    throw "The verifier did not remove a stale attestation after failure."
  }
} finally {
  $env:SHAMRAI_HIDDEN_GATE_JWT = $previousJwt
  Remove-Item -LiteralPath $temporaryDirectory -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "hidden_flat_e2e_gate_tests_ok"
