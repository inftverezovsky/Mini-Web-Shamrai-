Set-StrictMode -Version Latest

function Get-GateProperty {
  param(
    [Parameter(Mandatory)]
    [object]$InputObject,

    [Parameter(Mandatory)]
    [string]$Name
  )

  if ($InputObject -is [System.Collections.IDictionary]) {
    if (-not $InputObject.Contains($Name)) {
      throw "API response is missing required field '$Name'."
    }
    return $InputObject[$Name]
  }

  $property = $InputObject.PSObject.Properties[$Name]
  if ($null -eq $property) {
    throw "API response is missing required field '$Name'."
  }
  return $property.Value
}

function ConvertTo-GateDecimal {
  param(
    [Parameter(Mandatory)]
    [object]$Value,

    [Parameter(Mandatory)]
    [string]$Name
  )

  $parsed = [decimal]0
  $text = [Convert]::ToString($Value, [Globalization.CultureInfo]::InvariantCulture)
  if (
    [string]::IsNullOrWhiteSpace($text) -or
    -not [decimal]::TryParse(
      $text,
      [Globalization.NumberStyles]::Number,
      [Globalization.CultureInfo]::InvariantCulture,
      [ref]$parsed
    )
  ) {
    throw "API response field '$Name' is not a finite decimal value."
  }
  return $parsed
}

function ConvertTo-GateTimestamp {
  param(
    [Parameter(Mandatory)][object]$Value,
    [Parameter(Mandatory)][string]$Name
  )

  $parsed = [datetimeoffset]::MinValue
  if (-not [datetimeoffset]::TryParse(
    [string]$Value,
    [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::RoundtripKind,
    [ref]$parsed
  )) {
    throw "API response field '$Name' is not a valid timestamp."
  }
  return $parsed.ToUniversalTime()
}

function Assert-GateEqual {
  param(
    [Parameter(Mandatory)]
    [object]$Actual,

    [Parameter(Mandatory)]
    [object]$Expected,

    [Parameter(Mandatory)]
    [string]$Name
  )

  if ([string]$Actual -cne [string]$Expected) {
    throw "Hidden flat E2E gate failed: '$Name' does not match the expected value."
  }
}

function Test-GateTrue {
  param([object]$Value)
  return $Value -is [bool] -and $Value
}

function Assert-HiddenFlatE2EApiState {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory)]
    [object]$Version,

    [Parameter(Mandatory)]
    [object[]]$Plans,

    [Parameter(Mandatory)]
    [object]$PaymentAttempt,

    [Parameter(Mandatory)]
    [object]$FlatSubscription,

    [Parameter(Mandatory)]
    [ValidatePattern('^[0-9a-f]{40}$')]
    [string]$ExpectedGitSha,

    [Parameter(Mandatory)]
    [ValidateRange(1, [int]::MaxValue)]
    [int]$HiddenPlanId,

    [Parameter(Mandatory)]
    [Guid]$ExpectedPaymentAttemptId,

    [Parameter(Mandatory)]
    [ValidatePattern('^https?://')]
    [string]$BaseUrl,

    [Parameter(Mandatory)]
    [datetimeoffset]$MinimumEvidenceTimeUtc
  )

  $minimumEvidenceTime = $MinimumEvidenceTimeUtc.ToUniversalTime()

  $versionSha = [string](Get-GateProperty -InputObject $Version -Name "git_sha")
  Assert-GateEqual -Actual $versionSha.ToLowerInvariant() -Expected $ExpectedGitSha -Name "release SHA"
  $buildTime = [string](Get-GateProperty -InputObject $Version -Name "build_time")
  $parsedBuildTime = [datetimeoffset]::MinValue
  if (
    $buildTime -eq "unknown" -or
    -not [datetimeoffset]::TryParse(
      $buildTime,
      [Globalization.CultureInfo]::InvariantCulture,
      [Globalization.DateTimeStyles]::RoundtripKind,
      [ref]$parsedBuildTime
    )
  ) {
    throw "Hidden flat E2E gate failed: /api/version has no valid build timestamp."
  }

  $matchingPlans = @($Plans | Where-Object { [int](Get-GateProperty -InputObject $_ -Name "id") -eq $HiddenPlanId })
  if ($matchingPlans.Count -ne 1) {
    throw "Hidden flat E2E gate failed: the authenticated test client cannot see exactly one expected plan."
  }
  $plan = $matchingPlans[0]
  Assert-GateEqual -Actual (Get-GateProperty -InputObject $plan -Name "entitlement_type") -Expected "flat" -Name "plan entitlement"
  if (-not (Test-GateTrue (Get-GateProperty -InputObject $plan -Name "is_hidden"))) {
    throw "Hidden flat E2E gate failed: the tested plan is not hidden."
  }
  if (-not (Test-GateTrue (Get-GateProperty -InputObject $plan -Name "is_active"))) {
    throw "Hidden flat E2E gate failed: the tested hidden plan is archived."
  }

  Assert-GateEqual -Actual ([string](Get-GateProperty -InputObject $PaymentAttempt -Name "attempt_id")).ToLowerInvariant() -Expected $ExpectedPaymentAttemptId.ToString().ToLowerInvariant() -Name "payment attempt id"
  Assert-GateEqual -Actual (Get-GateProperty -InputObject $PaymentAttempt -Name "payment_status") -Expected "succeeded" -Name "payment status"
  Assert-GateEqual -Actual (Get-GateProperty -InputObject $PaymentAttempt -Name "checkout_state") -Expected "completed" -Name "checkout state"
  Assert-GateEqual -Actual (Get-GateProperty -InputObject $PaymentAttempt -Name "purchase_type") -Expected "subscription" -Name "purchase type"
  Assert-GateEqual -Actual ([int](Get-GateProperty -InputObject $PaymentAttempt -Name "plan_id")) -Expected $HiddenPlanId -Name "attempt plan"
  Assert-GateEqual -Actual (Get-GateProperty -InputObject $PaymentAttempt -Name "entitlement_type") -Expected "flat" -Name "attempt entitlement"
  Assert-GateEqual -Actual (Get-GateProperty -InputObject $PaymentAttempt -Name "currency") -Expected "RUB" -Name "payment currency"
  $paymentAmount = ConvertTo-GateDecimal -Value (Get-GateProperty -InputObject $PaymentAttempt -Name "amount") -Name "payment amount"
  if ($paymentAmount -le [decimal]0) {
    throw "Hidden flat E2E gate failed: payment amount is not positive."
  }
  $setupRequired = Get-GateProperty -InputObject $PaymentAttempt -Name "flat_setup_required"
  if ($setupRequired -isnot [bool] -or $setupRequired) {
    throw "Hidden flat E2E gate failed: the purchased flat still requires setup."
  }
  $attemptCreatedAt = ConvertTo-GateTimestamp -Value (Get-GateProperty -InputObject $PaymentAttempt -Name "created_at") -Name "attempt.created_at"
  $attemptUpdatedAt = ConvertTo-GateTimestamp -Value (Get-GateProperty -InputObject $PaymentAttempt -Name "updated_at") -Name "attempt.updated_at"
  if ($attemptCreatedAt -lt $minimumEvidenceTime -or $attemptUpdatedAt -lt $attemptCreatedAt) {
    throw "Hidden flat E2E gate failed: payment evidence predates this release."
  }

  Assert-GateEqual -Actual (Get-GateProperty -InputObject $FlatSubscription -Name "status") -Expected "completed" -Name "flat subscription status"
  $flatSubscriptionId = [string](Get-GateProperty -InputObject $FlatSubscription -Name "id")
  $embeddedFlat = Get-GateProperty -InputObject $PaymentAttempt -Name "flat_subscription"
  if ($null -eq $embeddedFlat) {
    throw "Hidden flat E2E gate failed: the paid attempt has no flat subscription progress."
  }
  Assert-GateEqual -Actual ([string](Get-GateProperty -InputObject $embeddedFlat -Name "id")) -Expected $flatSubscriptionId -Name "attempt flat subscription"

  $flatAmount = ConvertTo-GateDecimal -Value (Get-GateProperty -InputObject $FlatSubscription -Name "flat_amount_rub") -Name "flat_amount_rub"
  $targetFlats = ConvertTo-GateDecimal -Value (Get-GateProperty -InputObject $FlatSubscription -Name "target_flats") -Name "target_flats"
  $profitFlats = ConvertTo-GateDecimal -Value (Get-GateProperty -InputObject $FlatSubscription -Name "profit_flats") -Name "profit_flats"
  $remainingFlats = ConvertTo-GateDecimal -Value (Get-GateProperty -InputObject $FlatSubscription -Name "remaining_flats") -Name "remaining_flats"
  $attemptTarget = ConvertTo-GateDecimal -Value (Get-GateProperty -InputObject $PaymentAttempt -Name "target_flats") -Name "attempt target_flats"
  $pendingBets = [int](Get-GateProperty -InputObject $FlatSubscription -Name "pending_bets")
  $revision = [int](Get-GateProperty -InputObject $FlatSubscription -Name "revision")
  $flatCreatedAt = ConvertTo-GateTimestamp -Value (Get-GateProperty -InputObject $FlatSubscription -Name "created_at") -Name "flat.created_at"
  $flatCompletedAt = ConvertTo-GateTimestamp -Value (Get-GateProperty -InputObject $FlatSubscription -Name "completed_at") -Name "flat.completed_at"
  if ($flatAmount -lt [decimal]1 -or $targetFlats -le [decimal]0 -or $attemptTarget -le [decimal]0) {
    throw "Hidden flat E2E gate failed: flat amount or target is invalid."
  }
  if ($profitFlats -lt $targetFlats -or $remainingFlats -gt [decimal]0 -or $pendingBets -ne 0 -or $revision -lt 1) {
    throw "Hidden flat E2E gate failed: completed progress is internally inconsistent."
  }
  if ($attemptTarget -ne $targetFlats) {
    throw "Hidden flat E2E gate failed: the isolated subscription target differs from the immutable purchase snapshot."
  }
  if ($flatCreatedAt -lt $minimumEvidenceTime -or $flatCompletedAt -lt $flatCreatedAt) {
    throw "Hidden flat E2E gate failed: subscription evidence predates this release."
  }

  $purchaseCredits = @(
    @(Get-GateProperty -InputObject $FlatSubscription -Name "credits") |
      Where-Object { [string](Get-GateProperty -InputObject $_ -Name "event_type") -eq "subscription_purchase" }
  )
  if ($purchaseCredits.Count -ne 1) {
    throw "Hidden flat E2E gate failed: the isolated test subscription must contain one purchase credit."
  }
  $purchaseTarget = ConvertTo-GateDecimal -Value (Get-GateProperty -InputObject $purchaseCredits[0] -Name "delta_target_flats") -Name "purchase credit target"
  $purchaseCreditCreatedAt = ConvertTo-GateTimestamp -Value (Get-GateProperty -InputObject $purchaseCredits[0] -Name "created_at") -Name "purchase credit created_at"
  if ($purchaseTarget -ne $attemptTarget) {
    throw "Hidden flat E2E gate failed: purchase credit differs from the immutable payment snapshot."
  }
  if ($purchaseCreditCreatedAt -lt $minimumEvidenceTime) {
    throw "Hidden flat E2E gate failed: purchase credit predates this release."
  }

  $bets = @(Get-GateProperty -InputObject $FlatSubscription -Name "bets")
  if ($bets.Count -lt 1) {
    throw "Hidden flat E2E gate failed: no client stake was settled."
  }
  $positiveSettledBets = 0
  $latestSettledAt = [datetimeoffset]::MinValue
  foreach ($bet in $bets) {
    $betStatus = [string](Get-GateProperty -InputObject $bet -Name "status")
    if ($betStatus -notin @("win", "loss", "refund")) {
      throw "Hidden flat E2E gate failed: a client stake is not settled."
    }
    $betProfit = ConvertTo-GateDecimal -Value (Get-GateProperty -InputObject $bet -Name "profit_flats") -Name "bet profit_flats"
    if ($betProfit -gt [decimal]0) {
      $positiveSettledBets++
    }
    $takenAt = ConvertTo-GateTimestamp -Value (Get-GateProperty -InputObject $bet -Name "taken_at") -Name "bet taken_at"
    $settledAt = ConvertTo-GateTimestamp -Value (Get-GateProperty -InputObject $bet -Name "settled_at") -Name "bet settled_at"
    if ($takenAt -lt $minimumEvidenceTime -or $settledAt -lt $takenAt) {
      throw "Hidden flat E2E gate failed: settled stake evidence predates this release."
    }
    if ($settledAt -gt $latestSettledAt) {
      $latestSettledAt = $settledAt
    }
  }
  if ($positiveSettledBets -lt 1) {
    throw "Hidden flat E2E gate failed: the completed test has no winning settled stake."
  }

  $provider = [string](Get-GateProperty -InputObject $PaymentAttempt -Name "provider")
  if ($provider -notin @("yookassa", "tegro")) {
    throw "Hidden flat E2E gate failed: hidden purchase was not completed through an approved real provider."
  }

  return [ordered]@{
    schema_version = 1
    status = "passed"
    verified_at = [datetimeoffset]::UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
    base_url = $BaseUrl.TrimEnd("/")
    git_sha = $ExpectedGitSha
    build_time = $parsedBuildTime.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
    minimum_evidence_time = $minimumEvidenceTime.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
    payment_attempt_created_at = $attemptCreatedAt.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
    payment_attempt_id = $ExpectedPaymentAttemptId.ToString().ToLowerInvariant()
    payment_provider = $provider
    hidden_plan_id = $HiddenPlanId
    flat_subscription_id = $flatSubscriptionId
    flat_subscription_revision = $revision
    target_flats = $targetFlats.ToString("0.00", [Globalization.CultureInfo]::InvariantCulture)
    profit_flats = $profitFlats.ToString("0.000000", [Globalization.CultureInfo]::InvariantCulture)
    settled_bet_count = $bets.Count
    completed_at = $flatCompletedAt.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
    latest_settled_at = $latestSettledAt.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
  }
}

Export-ModuleMember -Function Assert-HiddenFlatE2EApiState
