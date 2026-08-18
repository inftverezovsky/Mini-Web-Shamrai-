param(
  [string]$Bucket = $env:SHAMRAI_BACKUP_S3_BUCKET,
  [string]$Prefix = $env:SHAMRAI_BACKUP_S3_PREFIX,
  [string]$EndpointUrl = $env:SHAMRAI_BACKUP_S3_ENDPOINT_URL,
  [string]$StsEndpointUrl = $env:SHAMRAI_BACKUP_S3_STS_ENDPOINT_URL,
  [string]$Region = $env:SHAMRAI_BACKUP_S3_REGION,
  [string]$WriterPolicyPath = $env:SHAMRAI_BACKUP_S3_WRITER_POLICY_PATH,
  [string]$AwsExecutable = "aws",
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:MinimumCurrentRetentionDays = 35
$script:MinimumNoncurrentRetentionDays = 90
$script:AttestationSchemaVersion = 3
$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$script:DeployDirectory = Join-Path $script:RepoRoot ".deploy"
$script:AttestationPath = Join-Path $script:DeployDirectory "s3-retention-attestation.json"

function Get-JsonPropertyValue {
  param(
    [AllowNull()]$Object,
    [Parameter(Mandatory = $true)][string]$Name,
    $DefaultValue = $null
  )

  if ($null -eq $Object) {
    return $DefaultValue
  }

  $property = $Object.PSObject.Properties |
    Where-Object { $_.Name -ieq $Name } |
    Select-Object -First 1
  if ($null -eq $property) {
    return $DefaultValue
  }
  return $property.Value
}

function ConvertTo-StringArray {
  param([AllowNull()]$Value)

  if ($null -eq $Value) {
    return @()
  }

  return @($Value) | ForEach-Object {
    if ($null -eq $_) {
      throw "Policy contains a null string value."
    }
    [string]$_
  }
}

function Assert-SafeBucketAndPrefix {
  param(
    [Parameter(Mandatory = $true)][string]$BucketName,
    [Parameter(Mandatory = $true)][string]$ObjectPrefix
  )

  if (
    $BucketName.Length -lt 3 -or
    $BucketName.Length -gt 63 -or
    $BucketName -notmatch '^[a-z0-9][a-z0-9.-]*[a-z0-9]$' -or
    $BucketName.Contains("..") -or
    $BucketName -match '^\d{1,3}(\.\d{1,3}){3}$'
  ) {
    throw "Bucket must be a valid S3-compatible bucket name."
  }

  if (
    $ObjectPrefix.Length -lt 1 -or
    $ObjectPrefix.Length -gt 256 -or
    $ObjectPrefix -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*$' -or
    $ObjectPrefix -match '(^|/)\.\.?(/|$)' -or
    $ObjectPrefix.Contains("//")
  ) {
    throw "Prefix must contain only safe key characters and no traversal segments."
  }
}

function Get-NormalizedEndpointUrl {
  param([AllowEmptyString()][string]$Value)

  if ([string]::IsNullOrWhiteSpace($Value)) {
    return ""
  }

  $uri = $null
  if (
    -not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$uri) -or
    $uri.Scheme -ne "https" -or
    -not [string]::IsNullOrEmpty($uri.UserInfo) -or
    -not [string]::IsNullOrEmpty($uri.Query) -or
    -not [string]::IsNullOrEmpty($uri.Fragment)
  ) {
    throw "EndpointUrl must be an absolute HTTPS URL without credentials, query, or fragment."
  }

  return $uri.AbsoluteUri.TrimEnd("/")
}

function Get-Sha256Hex {
  param([Parameter(Mandatory = $true)][string]$Value)

  $bytes = [Text.Encoding]::UTF8.GetBytes($Value)
  $sha256 = [Security.Cryptography.SHA256]::Create()
  try {
    return ([BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
  } finally {
    $sha256.Dispose()
  }
}

function Get-GitHubWorkflowProvenance {
  $emptyProvenance = [ordered]@{
    repository = ""
    workflow_run_id = ""
    workflow_run_attempt = ""
    git_sha = ""
  }
  if ($env:GITHUB_ACTIONS -ine "true") {
    return $emptyProvenance
  }

  $repository = [string]$env:GITHUB_REPOSITORY
  $workflowRunId = [string]$env:GITHUB_RUN_ID
  $workflowRunAttempt = [string]$env:GITHUB_RUN_ATTEMPT
  $gitSha = [string]$env:GITHUB_SHA
  if ($repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw "GITHUB_REPOSITORY is missing or invalid in GitHub Actions."
  }
  $parsedRunId = [UInt64]0
  if (
    $workflowRunId -notmatch '^[1-9][0-9]*$' -or
    -not [UInt64]::TryParse($workflowRunId, [ref]$parsedRunId)
  ) {
    throw "GITHUB_RUN_ID is missing or invalid in GitHub Actions."
  }
  $parsedRunAttempt = [UInt32]0
  if (
    $workflowRunAttempt -notmatch '^[1-9][0-9]*$' -or
    -not [UInt32]::TryParse($workflowRunAttempt, [ref]$parsedRunAttempt)
  ) {
    throw "GITHUB_RUN_ATTEMPT is missing or invalid in GitHub Actions."
  }
  if ($gitSha -notmatch '^[0-9a-fA-F]{40}$') {
    throw "GITHUB_SHA is missing or invalid in GitHub Actions."
  }

  return [ordered]@{
    repository = $repository
    workflow_run_id = $workflowRunId
    workflow_run_attempt = $workflowRunAttempt
    git_sha = $gitSha.ToLowerInvariant()
  }
}

function Test-PrefixPatternWithinScope {
  param(
    [Parameter(Mandatory = $true)][string]$Candidate,
    [Parameter(Mandatory = $true)][string]$RequiredPrefix
  )

  $normalizedCandidate = $Candidate.TrimStart("/")
  if ($normalizedCandidate -eq $RequiredPrefix) {
    return $true
  }
  return $normalizedCandidate.StartsWith("$RequiredPrefix/", [StringComparison]::Ordinal)
}

function Assert-WriterPolicy {
  param(
    [Parameter(Mandatory = $true)][string]$PolicyJson,
    [Parameter(Mandatory = $true)][string]$BucketName,
    [Parameter(Mandatory = $true)][string]$ObjectPrefix
  )

  try {
    $policy = $PolicyJson | ConvertFrom-Json
  } catch {
    throw "SHAMRAI_BACKUP_S3_WRITER_POLICY_JSON is not valid JSON."
  }

  $statements = @(Get-JsonPropertyValue -Object $policy -Name "Statement" -DefaultValue @())
  if ($statements.Count -eq 0) {
    throw "Writer policy must contain at least one statement."
  }

  $requiredActions = @{
    "s3:putobject" = $false
    "s3:getobject" = $false
    "s3:listbucket" = $false
  }
  $requiredCoverage = @{
    "s3:putobject" = $false
    "s3:getobject" = $false
    "s3:listbucket" = $false
  }
  $allowedWriterActions = @($requiredActions.Keys)
  $bucketArn = "arn:aws:s3:::$BucketName"
  $objectArnRoot = "$bucketArn/$ObjectPrefix/"

  foreach ($statement in $statements) {
    $effect = [string](Get-JsonPropertyValue -Object $statement -Name "Effect" -DefaultValue "")
    if ($effect -ine "Allow") {
      continue
    }

    if ($null -ne (Get-JsonPropertyValue -Object $statement -Name "NotAction")) {
      throw "Writer policy Allow statements must not use NotAction."
    }
    if ($null -ne (Get-JsonPropertyValue -Object $statement -Name "NotResource")) {
      throw "Writer policy Allow statements must not use NotResource."
    }
    if (
      $null -ne (Get-JsonPropertyValue -Object $statement -Name "Principal") -or
      $null -ne (Get-JsonPropertyValue -Object $statement -Name "NotPrincipal")
    ) {
      throw "Writer evidence must be an identity policy and must not contain Principal or NotPrincipal."
    }

    $actions = @(ConvertTo-StringArray (Get-JsonPropertyValue -Object $statement -Name "Action" -DefaultValue @()))
    $resources = @(ConvertTo-StringArray (Get-JsonPropertyValue -Object $statement -Name "Resource" -DefaultValue @()))
    if ($actions.Count -eq 0 -or $resources.Count -eq 0) {
      throw "Every writer policy Allow statement must declare Action and Resource."
    }

    foreach ($actionRaw in $actions) {
      $action = $actionRaw.Trim().ToLowerInvariant()
      if (-not $allowedWriterActions.Contains($action)) {
        throw "Writer policy grants an action outside PutObject/GetObject/ListBucket."
      }

      if ($action -eq "s3:listbucket") {
        if (-not ($resources -ccontains $bucketArn)) {
          throw "s3:ListBucket must be scoped to the configured bucket ARN."
        }
        foreach ($resource in $resources) {
          if ($resource -ne $bucketArn -and -not $resource.StartsWith($objectArnRoot, [StringComparison]::Ordinal)) {
            throw "Writer policy contains a resource outside the configured bucket prefix."
          }
        }

        $condition = Get-JsonPropertyValue -Object $statement -Name "Condition"
        if ($null -eq $condition) {
          throw "s3:ListBucket must include a prefix condition."
        }
        $prefixValues = @()
        foreach ($operator in $condition.PSObject.Properties) {
          if ($operator.Name -notin @(
            "StringEquals",
            "StringLike",
            "ForAnyValue:StringEquals",
            "ForAllValues:StringEquals",
            "ForAnyValue:StringLike",
            "ForAllValues:StringLike"
          )) {
            continue
          }
          $prefixCondition = Get-JsonPropertyValue -Object $operator.Value -Name "s3:prefix"
          if ($null -ne $prefixCondition) {
            $operatorPrefixValues = @(ConvertTo-StringArray $prefixCondition)
            $prefixValues += $operatorPrefixValues
            if ($operator.Name -imatch 'StringLike$') {
              foreach ($operatorPrefixValue in $operatorPrefixValues) {
                if ($operatorPrefixValue -cin @("$ObjectPrefix/*", "$ObjectPrefix/daily/*")) {
                  $requiredCoverage[$action] = $true
                }
              }
            }
          }
        }
        if ($prefixValues.Count -eq 0) {
          throw "s3:ListBucket condition must constrain s3:prefix."
        }
        foreach ($prefixValue in $prefixValues) {
          if (-not (Test-PrefixPatternWithinScope -Candidate $prefixValue -RequiredPrefix $ObjectPrefix)) {
            throw "s3:ListBucket condition escapes the configured prefix."
          }
        }
      } else {
        $objectResources = @($resources | Where-Object {
          $_.StartsWith($objectArnRoot, [StringComparison]::Ordinal)
        })
        if ($objectResources.Count -eq 0) {
          throw "Object actions must be scoped below the configured bucket prefix."
        }
        foreach ($resource in $resources) {
          if ($resource -ne $bucketArn -and -not $resource.StartsWith($objectArnRoot, [StringComparison]::Ordinal)) {
            throw "Writer policy contains an object resource outside the configured prefix."
          }
          if ($resource -cin @("${objectArnRoot}*", "${objectArnRoot}daily/*")) {
            $requiredCoverage[$action] = $true
          }
        }
      }

      $requiredActions[$action] = $true
    }
  }

  foreach ($requiredAction in $requiredActions.Keys) {
    if (-not $requiredActions[$requiredAction]) {
      throw "Writer policy is missing required action $requiredAction."
    }
    if (-not $requiredCoverage[$requiredAction]) {
      throw "Writer policy action $requiredAction does not cover generated objects below $ObjectPrefix/daily/*."
    }
  }
}

function Test-StringSetExactly {
  param(
    [AllowNull()]$Actual,
    [Parameter(Mandatory = $true)][string[]]$Expected
  )

  $actualValues = @(ConvertTo-StringArray $Actual | ForEach-Object { $_.Trim().ToLowerInvariant() })
  $expectedValues = @($Expected | ForEach-Object { $_.Trim().ToLowerInvariant() })
  if (
    $actualValues.Count -ne $expectedValues.Count -or
    @($actualValues | Sort-Object -Unique).Count -ne $actualValues.Count
  ) {
    return $false
  }
  return @(Compare-Object ($actualValues | Sort-Object) ($expectedValues | Sort-Object)).Count -eq 0
}

function Test-ExactBucketPolicyPrincipal {
  param(
    [AllowNull()]$Principal,
    [Parameter(Mandatory = $true)][string]$ExpectedPrincipalArn
  )

  if ($Principal -is [string]) {
    return $Principal -ceq $ExpectedPrincipalArn
  }
  if ($null -eq $Principal) {
    return $false
  }

  $properties = @($Principal.PSObject.Properties)
  if ($properties.Count -ne 1 -or $properties[0].Name -ine "AWS") {
    return $false
  }
  $values = @(ConvertTo-StringArray $properties[0].Value)
  return $values.Count -eq 1 -and $values[0] -ceq $ExpectedPrincipalArn
}

function Get-StableWriterPrincipal {
  param([Parameter(Mandatory = $true)][string]$CallerArn)

  $normalizedArn = $CallerArn.Trim()
  if ($normalizedArn -cmatch '^arn:(?<partition>[A-Za-z0-9-]+):iam::(?<account>[A-Za-z0-9-]+):(?<type>user|role)/(?<name>[A-Za-z0-9+=,.@_/-]+)$') {
    $entityName = @($Matches.name -split '/')[-1]
    return [pscustomobject]@{
      CallerArn = $normalizedArn
      StableArn = $normalizedArn
      PrincipalType = "iam_$($Matches.type)"
      IamEntityType = $Matches.type
      IamEntityName = $entityName
    }
  }
  if ($normalizedArn -cmatch '^arn:(?<partition>[A-Za-z0-9-]+):sts::(?<account>[A-Za-z0-9-]+):assumed-role/(?<role>[A-Za-z0-9+=,.@_-]+)/(?<session>[A-Za-z0-9+=,.@_-]+)$') {
    return [pscustomobject]@{
      CallerArn = $normalizedArn
      StableArn = "arn:$($Matches.partition):iam::$($Matches.account):role/$($Matches.role)"
      PrincipalType = "assumed_role_to_iam_role"
      IamEntityType = "role"
      IamEntityName = $Matches.role
    }
  }
  throw "STS caller ARN is not a supported stable IAM user or role identity."
}

function Get-LiveBucketPolicyJson {
  param([Parameter(Mandatory = $true)]$Response)

  $policyValue = Get-JsonPropertyValue -Object $Response -Name "Policy"
  if ($null -eq $policyValue) {
    throw "Read-only S3 audit operation 'get-bucket-policy' did not return Policy."
  }
  $policyJson = if ($policyValue -is [string]) {
    [string]$policyValue
  } else {
    $policyValue | ConvertTo-Json -Depth 20 -Compress
  }
  if ([string]::IsNullOrWhiteSpace($policyJson)) {
    throw "Read-only S3 audit operation 'get-bucket-policy' returned an empty Policy."
  }
  return $policyJson
}

function Assert-ImmutableBucketDenyPolicy {
  param(
    [Parameter(Mandatory = $true)][string]$PolicyJson,
    [Parameter(Mandatory = $true)][string]$BucketName,
    [Parameter(Mandatory = $true)][string]$ObjectPrefix,
    [Parameter(Mandatory = $true)][string]$WriterPrincipalArn
  )

  try {
    $policy = $PolicyJson | ConvertFrom-Json
  } catch {
    throw "Live S3 bucket policy is not valid JSON."
  }

  $bucketArn = "arn:aws:s3:::$BucketName"
  $prefixObjectArn = "$bucketArn/$ObjectPrefix/*"
  $bucketObjectArn = "$bucketArn/*"
  $allowedActions = @("s3:PutObject", "s3:GetObject", "s3:ListBucket")
  $objectActions = @("s3:PutObject", "s3:GetObject")
  $requiredListPrefixes = @($ObjectPrefix, "$ObjectPrefix/*")
  $denyAllOtherActions = $false
  $denyOutsideObjectPrefix = $false
  $denyOutsideListPrefix = $false
  $denyMissingListPrefix = $false
  $statements = @(Get-JsonPropertyValue -Object $policy -Name "Statement" -DefaultValue @())
  foreach ($statement in $statements) {
    if ([string](Get-JsonPropertyValue -Object $statement -Name "Effect" -DefaultValue "") -ine "Deny") {
      continue
    }
    if (-not (Test-ExactBucketPolicyPrincipal `
      -Principal (Get-JsonPropertyValue -Object $statement -Name "Principal") `
      -ExpectedPrincipalArn $WriterPrincipalArn)) {
      continue
    }
    if ($null -ne (Get-JsonPropertyValue -Object $statement -Name "NotPrincipal")) {
      continue
    }

    $actions = @(ConvertTo-StringArray (Get-JsonPropertyValue -Object $statement -Name "Action" -DefaultValue @()))
    $notActions = @(ConvertTo-StringArray (Get-JsonPropertyValue -Object $statement -Name "NotAction" -DefaultValue @()))
    $resources = @(ConvertTo-StringArray (Get-JsonPropertyValue -Object $statement -Name "Resource" -DefaultValue @()))
    $notResources = @(ConvertTo-StringArray (Get-JsonPropertyValue -Object $statement -Name "NotResource" -DefaultValue @()))
    $condition = Get-JsonPropertyValue -Object $statement -Name "Condition"

    if (
      $actions.Count -eq 0 -and
      (Test-StringSetExactly -Actual $notActions -Expected $allowedActions) -and
      (Test-StringSetExactly -Actual $resources -Expected @($bucketArn, $bucketObjectArn)) -and
      $notResources.Count -eq 0 -and
      $null -eq $condition
    ) {
      $denyAllOtherActions = $true
      continue
    }

    if (
      (Test-StringSetExactly -Actual $actions -Expected $objectActions) -and
      $notActions.Count -eq 0 -and
      $resources.Count -eq 0 -and
      (Test-StringSetExactly -Actual $notResources -Expected @($prefixObjectArn)) -and
      $null -eq $condition
    ) {
      $denyOutsideObjectPrefix = $true
      continue
    }

    if (
      (Test-StringSetExactly -Actual $actions -Expected @("s3:ListBucket")) -and
      $notActions.Count -eq 0 -and
      (Test-StringSetExactly -Actual $resources -Expected @($bucketArn)) -and
      $notResources.Count -eq 0 -and
      $null -ne $condition
    ) {
      $conditionProperties = @($condition.PSObject.Properties)
      if ($conditionProperties.Count -ne 1) {
        continue
      }
      $conditionValues = $conditionProperties[0].Value
      $conditionValueProperties = @($conditionValues.PSObject.Properties)
      if (
        $conditionValueProperties.Count -ne 1 -or
        $conditionValueProperties[0].Name -ine "s3:prefix"
      ) {
        continue
      }
      if (
        $conditionProperties[0].Name -ieq "StringNotLike" -and
        (Test-StringSetExactly -Actual $conditionValueProperties[0].Value -Expected $requiredListPrefixes)
      ) {
        $denyOutsideListPrefix = $true
      } elseif (
        $conditionProperties[0].Name -ieq "Null" -and
        (Test-StringSetExactly -Actual $conditionValueProperties[0].Value -Expected @("true"))
      ) {
        $denyMissingListPrefix = $true
      }
    }
  }

  $missingRules = @(
    if (-not $denyAllOtherActions) { "deny-all-other-actions" }
    if (-not $denyOutsideObjectPrefix) { "deny-put-get-outside-prefix" }
    if (-not $denyOutsideListPrefix) { "deny-list-outside-prefix" }
    if (-not $denyMissingListPrefix) { "deny-list-without-prefix" }
  )
  if ($missingRules.Count -ne 0) {
    throw "Live S3 bucket policy is not bound to the exact writer principal or lacks required Deny rules: $($missingRules -join ', ')."
  }

  return [pscustomobject]@{
    Status = "passed"
    RequiredRuleCount = 4
  }
}

function Get-AwsPolicyDocumentJson {
  param(
    [Parameter(Mandatory = $true)]$Response,
    [Parameter(Mandatory = $true)][string]$ContainerName
  )

  $container = Get-JsonPropertyValue -Object $Response -Name $ContainerName
  $document = Get-JsonPropertyValue -Object $container -Name "Document"
  if ($null -eq $document) {
    throw "Read-only AWS IAM policy response is missing $ContainerName.Document."
  }
  $policyJson = if ($document -is [string]) {
    [Uri]::UnescapeDataString([string]$document)
  } else {
    $document | ConvertTo-Json -Depth 30 -Compress
  }
  if ([string]::IsNullOrWhiteSpace($policyJson)) {
    throw "Read-only AWS IAM policy response returned an empty policy document."
  }
  try {
    $policyJson | ConvertFrom-Json | Out-Null
  } catch {
    throw "Read-only AWS IAM policy response returned invalid policy JSON."
  }
  return $policyJson
}

function Assert-WriterPermissionBoundary {
  param(
    [Parameter(Mandatory = $true)][string]$PolicyJson,
    [Parameter(Mandatory = $true)][string]$BucketName,
    [Parameter(Mandatory = $true)][string]$ObjectPrefix
  )

  Assert-WriterPolicy -PolicyJson $PolicyJson -BucketName $BucketName -ObjectPrefix $ObjectPrefix
  $policy = $PolicyJson | ConvertFrom-Json
  $bucketArn = "arn:aws:s3:::$BucketName"
  $prefixObjectArn = "$bucketArn/$ObjectPrefix/*"
  $allowedActions = @("s3:PutObject", "s3:GetObject", "s3:ListBucket")
  $objectActions = @("s3:PutObject", "s3:GetObject")
  $requiredListPrefixes = @($ObjectPrefix, "$ObjectPrefix/*")
  $denyAllOtherActions = $false
  $denyObjectAccessOutsidePrefix = $false
  $denyListOtherBuckets = $false
  $denyListOutsidePrefix = $false
  $denyListWithoutPrefix = $false

  foreach ($statement in @(Get-JsonPropertyValue -Object $policy -Name "Statement" -DefaultValue @())) {
    if (
      [string](Get-JsonPropertyValue -Object $statement -Name "Effect" -DefaultValue "") -ine "Deny" -or
      $null -ne (Get-JsonPropertyValue -Object $statement -Name "Principal") -or
      $null -ne (Get-JsonPropertyValue -Object $statement -Name "NotPrincipal")
    ) {
      continue
    }
    $actions = @(ConvertTo-StringArray (Get-JsonPropertyValue -Object $statement -Name "Action" -DefaultValue @()))
    $notActions = @(ConvertTo-StringArray (Get-JsonPropertyValue -Object $statement -Name "NotAction" -DefaultValue @()))
    $resources = @(ConvertTo-StringArray (Get-JsonPropertyValue -Object $statement -Name "Resource" -DefaultValue @()))
    $notResources = @(ConvertTo-StringArray (Get-JsonPropertyValue -Object $statement -Name "NotResource" -DefaultValue @()))
    $condition = Get-JsonPropertyValue -Object $statement -Name "Condition"

    if (
      $actions.Count -eq 0 -and
      (Test-StringSetExactly -Actual $notActions -Expected $allowedActions) -and
      (Test-StringSetExactly -Actual $resources -Expected @("*")) -and
      $notResources.Count -eq 0 -and
      $null -eq $condition
    ) {
      $denyAllOtherActions = $true
      continue
    }
    if (
      (Test-StringSetExactly -Actual $actions -Expected $objectActions) -and
      $notActions.Count -eq 0 -and
      $resources.Count -eq 0 -and
      (Test-StringSetExactly -Actual $notResources -Expected @($prefixObjectArn)) -and
      $null -eq $condition
    ) {
      $denyObjectAccessOutsidePrefix = $true
      continue
    }
    if (
      (Test-StringSetExactly -Actual $actions -Expected @("s3:ListBucket")) -and
      $notActions.Count -eq 0 -and
      $resources.Count -eq 0 -and
      (Test-StringSetExactly -Actual $notResources -Expected @($bucketArn)) -and
      $null -eq $condition
    ) {
      $denyListOtherBuckets = $true
      continue
    }
    if (
      (Test-StringSetExactly -Actual $actions -Expected @("s3:ListBucket")) -and
      $notActions.Count -eq 0 -and
      (Test-StringSetExactly -Actual $resources -Expected @($bucketArn)) -and
      $notResources.Count -eq 0 -and
      $null -ne $condition
    ) {
      $conditionProperties = @($condition.PSObject.Properties)
      if ($conditionProperties.Count -ne 1) {
        continue
      }
      $conditionValues = $conditionProperties[0].Value
      $conditionValueProperties = @($conditionValues.PSObject.Properties)
      if (
        $conditionValueProperties.Count -ne 1 -or
        $conditionValueProperties[0].Name -ine "s3:prefix"
      ) {
        continue
      }
      if (
        $conditionProperties[0].Name -ieq "StringNotLike" -and
        (Test-StringSetExactly -Actual $conditionValueProperties[0].Value -Expected $requiredListPrefixes)
      ) {
        $denyListOutsidePrefix = $true
      } elseif (
        $conditionProperties[0].Name -ieq "Null" -and
        (Test-StringSetExactly -Actual $conditionValueProperties[0].Value -Expected @("true"))
      ) {
        $denyListWithoutPrefix = $true
      }
    }
  }

  $missingRules = @(
    if (-not $denyAllOtherActions) { "deny-all-non-backup-actions" }
    if (-not $denyObjectAccessOutsidePrefix) { "deny-put-get-outside-prefix" }
    if (-not $denyListOtherBuckets) { "deny-list-other-buckets" }
    if (-not $denyListOutsidePrefix) { "deny-list-outside-prefix" }
    if (-not $denyListWithoutPrefix) { "deny-list-without-prefix" }
  )
  if ($missingRules.Count -ne 0) {
    throw "Live AWS IAM permissions boundary lacks required explicit Deny rules: $($missingRules -join ', ')."
  }
  return [pscustomobject]@{
    Status = "passed"
    RequiredRuleCount = 5
  }
}

function Get-LiveWriterPermissionBoundaryEvidence {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$ApiRegion,
    [Parameter(Mandatory = $true)]$WriterPrincipal
  )

  $entityOperation = if ($WriterPrincipal.IamEntityType -ceq "user") { "get-user" } else { "get-role" }
  $entityOption = if ($WriterPrincipal.IamEntityType -ceq "user") { "--user-name" } else { "--role-name" }
  $entityContainerName = if ($WriterPrincipal.IamEntityType -ceq "user") { "User" } else { "Role" }
  $entityResponse = Invoke-AwsIamJson `
    -Executable $Executable `
    -Operation $entityOperation `
    -ApiRegion $ApiRegion `
    -OperationArguments @($entityOption, $WriterPrincipal.IamEntityName)
  $entity = Get-JsonPropertyValue -Object $entityResponse -Name $entityContainerName
  if (
    $null -eq $entity -or
    [string](Get-JsonPropertyValue -Object $entity -Name "Arn" -DefaultValue "") -cne $WriterPrincipal.StableArn
  ) {
    throw "Live AWS IAM entity does not match the STS-derived stable writer principal."
  }
  $boundary = Get-JsonPropertyValue -Object $entity -Name "PermissionsBoundary"
  $boundaryArn = [string](Get-JsonPropertyValue -Object $boundary -Name "PermissionsBoundaryArn" -DefaultValue "")
  if (
    [string](Get-JsonPropertyValue -Object $boundary -Name "PermissionsBoundaryType" -DefaultValue "") -cne "Policy" -or
    $boundaryArn -notmatch '^arn:[A-Za-z0-9-]+:iam::[A-Za-z0-9-]+:policy/[A-Za-z0-9+=,.@_/-]+$'
  ) {
    throw "The live writer IAM entity must have a customer-managed permissions boundary."
  }

  $policyResponse = Invoke-AwsIamJson `
    -Executable $Executable `
    -Operation "get-policy" `
    -ApiRegion $ApiRegion `
    -OperationArguments @("--policy-arn", $boundaryArn)
  $policyMetadata = Get-JsonPropertyValue -Object $policyResponse -Name "Policy"
  $defaultVersionId = [string](Get-JsonPropertyValue -Object $policyMetadata -Name "DefaultVersionId" -DefaultValue "")
  if (
    [string](Get-JsonPropertyValue -Object $policyMetadata -Name "Arn" -DefaultValue "") -cne $boundaryArn -or
    $defaultVersionId -notmatch '^v[1-9][0-9]*$'
  ) {
    throw "Live writer permissions-boundary metadata is invalid."
  }
  $versionResponse = Invoke-AwsIamJson `
    -Executable $Executable `
    -Operation "get-policy-version" `
    -ApiRegion $ApiRegion `
    -OperationArguments @("--policy-arn", $boundaryArn, "--version-id", $defaultVersionId)
  $version = Get-JsonPropertyValue -Object $versionResponse -Name "PolicyVersion"
  if (
    (Get-JsonPropertyValue -Object $version -Name "IsDefaultVersion" -DefaultValue $false) -ne $true -or
    [string](Get-JsonPropertyValue -Object $version -Name "VersionId" -DefaultValue "") -cne $defaultVersionId
  ) {
    throw "Live writer permissions-boundary version is not the current default."
  }
  $policyJson = Get-AwsPolicyDocumentJson -Response $versionResponse -ContainerName "PolicyVersion"
  return [pscustomobject]@{
    BoundaryArn = $boundaryArn
    DefaultVersionId = $defaultVersionId
    PolicyJson = $policyJson
  }
}

function Assert-VersioningConfiguration {
  param([Parameter(Mandatory = $true)]$Configuration)

  $status = [string](Get-JsonPropertyValue -Object $Configuration -Name "Status" -DefaultValue "")
  if ($status -cne "Enabled") {
    throw "S3 bucket versioning must be Enabled."
  }
  return $status
}

function Assert-ObjectLockConfiguration {
  param([Parameter(Mandatory = $true)]$Configuration)

  $objectLock = Get-JsonPropertyValue -Object $Configuration -Name "ObjectLockConfiguration"
  $status = [string](Get-JsonPropertyValue -Object $objectLock -Name "ObjectLockEnabled" -DefaultValue "")
  if ($status -cne "Enabled") {
    throw "S3 Object Lock must be Enabled."
  }

  $rule = Get-JsonPropertyValue -Object $objectLock -Name "Rule"
  $retention = Get-JsonPropertyValue -Object $rule -Name "DefaultRetention"
  $mode = [string](Get-JsonPropertyValue -Object $retention -Name "Mode" -DefaultValue "")
  if ($mode -cne "COMPLIANCE") {
    throw "Default Object Lock retention must use COMPLIANCE mode."
  }

  $daysRaw = Get-JsonPropertyValue -Object $retention -Name "Days"
  $yearsRaw = Get-JsonPropertyValue -Object $retention -Name "Years"
  if ($null -ne $daysRaw -and $null -ne $yearsRaw) {
    throw "Default Object Lock retention must specify either Days or Years, not both."
  }
  if ($null -ne $daysRaw) {
    $retentionDays = [int]$daysRaw
  } elseif ($null -ne $yearsRaw) {
    $retentionDays = [int]$yearsRaw * 365
  } else {
    throw "Default Object Lock retention duration is missing."
  }
  if ($retentionDays -lt $script:MinimumCurrentRetentionDays) {
    throw "Default Object Lock retention must be at least $($script:MinimumCurrentRetentionDays) days."
  }

  return [pscustomobject]@{
    Status = $status
    Days = $retentionDays
  }
}

function Get-LifecycleRuleScope {
  param([Parameter(Mandatory = $true)]$Rule)

  $legacyPrefix = [string](Get-JsonPropertyValue -Object $Rule -Name "Prefix" -DefaultValue "")
  $filter = Get-JsonPropertyValue -Object $Rule -Name "Filter"
  $prefix = $legacyPrefix
  $conditional = $false

  if ($null -ne $filter) {
    $filterPrefix = Get-JsonPropertyValue -Object $filter -Name "Prefix"
    if ($null -ne $filterPrefix) {
      $prefix = [string]$filterPrefix
    }
    $andFilter = Get-JsonPropertyValue -Object $filter -Name "And"
    if ($null -ne $andFilter) {
      $andPrefix = Get-JsonPropertyValue -Object $andFilter -Name "Prefix"
      if ($null -ne $andPrefix) {
        $prefix = [string]$andPrefix
      }
      foreach ($property in $andFilter.PSObject.Properties) {
        if ($property.Name -ine "Prefix") {
          $conditional = $true
        }
      }
    }
    foreach ($property in $filter.PSObject.Properties) {
      if ($property.Name -notin @("Prefix", "And")) {
        $conditional = $true
      }
    }
  }

  if (
    -not [string]::IsNullOrEmpty($prefix) -and
    (
      $prefix -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*$' -or
      $prefix -match '(^|/)\.\.?(/|$)' -or
      $prefix.Contains("//")
    )
  ) {
    throw "Enabled lifecycle rule contains an unsupported or traversal-like prefix."
  }
  $normalizedPrefix = $prefix
  if ($normalizedPrefix.EndsWith("/", [StringComparison]::Ordinal)) {
    $normalizedPrefix = $normalizedPrefix.Substring(0, $normalizedPrefix.Length - 1)
  }

  return [pscustomobject]@{
    Prefix = $normalizedPrefix
    Conditional = $conditional
  }
}

function Test-PrefixScopesIntersect {
  param(
    [AllowEmptyString()][string]$RulePrefix,
    [Parameter(Mandatory = $true)][string]$RequiredPrefix
  )

  if ([string]::IsNullOrEmpty($RulePrefix)) {
    return $true
  }
  if ($RulePrefix -eq $RequiredPrefix) {
    return $true
  }
  return (
    $RequiredPrefix.StartsWith("$RulePrefix/", [StringComparison]::Ordinal) -or
    $RulePrefix.StartsWith("$RequiredPrefix/", [StringComparison]::Ordinal)
  )
}

function Test-RuleCoversRequiredPrefix {
  param(
    [AllowEmptyString()][string]$RulePrefix,
    [Parameter(Mandatory = $true)][string]$RequiredPrefix,
    [Parameter(Mandatory = $true)][bool]$Conditional
  )

  if ($Conditional) {
    return $false
  }
  if ([string]::IsNullOrEmpty($RulePrefix)) {
    return $true
  }
  return (
    $RulePrefix -eq $RequiredPrefix -or
    $RequiredPrefix.StartsWith("$RulePrefix/", [StringComparison]::Ordinal)
  )
}

function Assert-LifecycleConfiguration {
  param(
    [Parameter(Mandatory = $true)]$Configuration,
    [Parameter(Mandatory = $true)][string]$ObjectPrefix
  )

  $rules = @(Get-JsonPropertyValue -Object $Configuration -Name "Rules" -DefaultValue @())
  $currentDays = @()
  $noncurrentDays = @()
  $currentCoverage = $false
  $noncurrentCoverage = $false

  foreach ($rule in $rules) {
    if ([string](Get-JsonPropertyValue -Object $rule -Name "Status" -DefaultValue "") -cne "Enabled") {
      continue
    }

    $scope = Get-LifecycleRuleScope -Rule $rule
    if (-not (Test-PrefixScopesIntersect -RulePrefix $scope.Prefix -RequiredPrefix $ObjectPrefix)) {
      continue
    }
    $coversRequiredPrefix = Test-RuleCoversRequiredPrefix `
      -RulePrefix $scope.Prefix `
      -RequiredPrefix $ObjectPrefix `
      -Conditional $scope.Conditional

    $expiration = Get-JsonPropertyValue -Object $rule -Name "Expiration"
    if ($null -ne $expiration) {
      if ($null -ne (Get-JsonPropertyValue -Object $expiration -Name "Date")) {
        throw "Applicable lifecycle rules must not use date-based current expiration."
      }
      $daysRaw = Get-JsonPropertyValue -Object $expiration -Name "Days"
      if ($null -ne $daysRaw) {
        $days = [int]$daysRaw
        if ($days -lt $script:MinimumCurrentRetentionDays) {
          throw "Applicable current-version lifecycle expiration is shorter than $($script:MinimumCurrentRetentionDays) days."
        }
        $currentDays += $days
        if ($coversRequiredPrefix) {
          $currentCoverage = $true
        }
      }
    }

    $noncurrentExpiration = Get-JsonPropertyValue -Object $rule -Name "NoncurrentVersionExpiration"
    if ($null -ne $noncurrentExpiration) {
      $noncurrentDaysRaw = Get-JsonPropertyValue -Object $noncurrentExpiration -Name "NoncurrentDays"
      if ($null -ne $noncurrentDaysRaw) {
        $days = [int]$noncurrentDaysRaw
        if ($days -lt $script:MinimumNoncurrentRetentionDays) {
          throw "Applicable noncurrent-version lifecycle expiration is shorter than $($script:MinimumNoncurrentRetentionDays) days."
        }
        $noncurrentDays += $days
        if ($coversRequiredPrefix) {
          $noncurrentCoverage = $true
        }
      }
    }
  }

  if (-not $currentCoverage -or $currentDays.Count -eq 0) {
    throw "No unconditional lifecycle rule retains current objects across the configured prefix."
  }
  if (-not $noncurrentCoverage -or $noncurrentDays.Count -eq 0) {
    throw "No unconditional lifecycle rule retains noncurrent versions across the configured prefix."
  }

  return [pscustomobject]@{
    CurrentDays = [int](($currentDays | Measure-Object -Minimum).Minimum)
    NoncurrentDays = [int](($noncurrentDays | Measure-Object -Minimum).Minimum)
  }
}

function Invoke-AwsOperation {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Operation,
    [Parameter(Mandatory = $true)][string]$BucketName,
    [AllowEmptyString()][string]$ApiEndpoint,
    [Parameter(Mandatory = $true)][string]$ApiRegion,
    [string[]]$OperationArguments = @()
  )

  $arguments = @(
    "s3api",
    $Operation,
    "--bucket", $BucketName
  )
  $arguments += $OperationArguments
  $arguments += @("--region", $ApiRegion, "--output", "json", "--no-cli-pager")
  if (-not [string]::IsNullOrEmpty($ApiEndpoint)) {
    $arguments += @("--endpoint-url", $ApiEndpoint)
  }

  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = 0
    $output = & $Executable @arguments 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  return [pscustomobject]@{
    ExitCode = $exitCode
    Output = ($output -join "`n")
  }
}

function Invoke-AwsJson {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Operation,
    [Parameter(Mandatory = $true)][string]$BucketName,
    [AllowEmptyString()][string]$ApiEndpoint,
    [Parameter(Mandatory = $true)][string]$ApiRegion,
    [string[]]$OperationArguments = @()
  )

  $result = Invoke-AwsOperation `
    -Executable $Executable `
    -Operation $Operation `
    -BucketName $BucketName `
    -ApiEndpoint $ApiEndpoint `
    -ApiRegion $ApiRegion `
    -OperationArguments $OperationArguments
  if ($result.ExitCode -ne 0) {
    throw "Read-only S3 audit operation '$Operation' failed with exit code $($result.ExitCode)."
  }

  try {
    return ($result.Output | ConvertFrom-Json)
  } catch {
    throw "Read-only S3 audit operation '$Operation' returned invalid JSON."
  }
}

function Invoke-AwsStsCallerIdentity {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [AllowEmptyString()][string]$ApiEndpoint,
    [Parameter(Mandatory = $true)][string]$ApiRegion
  )

  $arguments = @(
    "sts",
    "get-caller-identity",
    "--region", $ApiRegion,
    "--output", "json",
    "--no-cli-pager"
  )
  if (-not [string]::IsNullOrEmpty($ApiEndpoint)) {
    $arguments += @("--endpoint-url", $ApiEndpoint)
  }

  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = 0
    $output = & $Executable @arguments 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  if ($exitCode -ne 0) {
    throw "Writer STS get-caller-identity failed with exit code $exitCode."
  }
  try {
    return ($output -join "`n") | ConvertFrom-Json
  } catch {
    throw "Writer STS get-caller-identity returned invalid JSON."
  }
}

function Invoke-AwsIamJson {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Operation,
    [Parameter(Mandatory = $true)][string]$ApiRegion,
    [string[]]$OperationArguments = @()
  )

  $arguments = @("iam", $Operation)
  $arguments += $OperationArguments
  $arguments += @("--region", $ApiRegion, "--output", "json", "--no-cli-pager")

  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $global:LASTEXITCODE = 0
    $output = & $Executable @arguments 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  if ($exitCode -ne 0) {
    throw "Read-only AWS IAM operation '$Operation' failed with exit code $exitCode."
  }
  try {
    return ($output -join "`n") | ConvertFrom-Json
  } catch {
    throw "Read-only AWS IAM operation '$Operation' returned invalid JSON."
  }
}

function Invoke-WriterAwsJson {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Operation,
    [Parameter(Mandatory = $true)][string]$BucketName,
    [AllowEmptyString()][string]$ApiEndpoint,
    [Parameter(Mandatory = $true)][string]$ApiRegion,
    [Parameter(Mandatory = $true)][string]$CapabilityName,
    [string[]]$OperationArguments = @()
  )

  $result = Invoke-AwsOperation `
    -Executable $Executable `
    -Operation $Operation `
    -BucketName $BucketName `
    -ApiEndpoint $ApiEndpoint `
    -ApiRegion $ApiRegion `
    -OperationArguments $OperationArguments
  if ($result.ExitCode -ne 0) {
    throw "Writer capability probe '$CapabilityName' failed with exit code $($result.ExitCode)."
  }
  try {
    return ($result.Output | ConvertFrom-Json)
  } catch {
    throw "Writer capability probe '$CapabilityName' returned invalid JSON."
  }
}

function Assert-WriterAwsOperationDenied {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$Operation,
    [Parameter(Mandatory = $true)][string]$BucketName,
    [AllowEmptyString()][string]$ApiEndpoint,
    [Parameter(Mandatory = $true)][string]$ApiRegion,
    [Parameter(Mandatory = $true)][string]$CapabilityName,
    [string[]]$OperationArguments = @()
  )

  $result = Invoke-AwsOperation `
    -Executable $Executable `
    -Operation $Operation `
    -BucketName $BucketName `
    -ApiEndpoint $ApiEndpoint `
    -ApiRegion $ApiRegion `
    -OperationArguments $OperationArguments
  if ($result.ExitCode -eq 0) {
    throw "$CapabilityName was unexpectedly allowed for the backup writer identity."
  }
  if ($result.Output -notmatch '(?i)\b(AccessDenied|AccessDeniedException|Forbidden|Unauthorized|UnauthorizedOperation|AuthorizationFailure|NotAuthorized)\b') {
    throw "$CapabilityName did not return a recognizable authorization denial; refusing to treat a transport or provider error as least-privilege evidence."
  }
}

function Set-AwsCredentialEnvironment {
  param(
    [Parameter(Mandatory = $true)][string]$AccessKeyId,
    [Parameter(Mandatory = $true)][string]$SecretAccessKey,
    [AllowEmptyString()][string]$SessionToken,
    [Parameter(Mandatory = $true)][string]$ApiRegion
  )

  [Environment]::SetEnvironmentVariable("AWS_ACCESS_KEY_ID", $AccessKeyId, "Process")
  [Environment]::SetEnvironmentVariable("AWS_SECRET_ACCESS_KEY", $SecretAccessKey, "Process")
  [Environment]::SetEnvironmentVariable("AWS_SESSION_TOKEN", $SessionToken, "Process")
  [Environment]::SetEnvironmentVariable("AWS_REGION", $ApiRegion, "Process")
  [Environment]::SetEnvironmentVariable("AWS_DEFAULT_REGION", $ApiRegion, "Process")
  [Environment]::SetEnvironmentVariable("AWS_PAGER", "", "Process")
}

function Get-FileContentMd5Base64 {
  param([Parameter(Mandatory = $true)][string]$Path)

  $stream = [IO.File]::OpenRead($Path)
  $md5 = [Security.Cryptography.MD5]::Create()
  try {
    return [Convert]::ToBase64String($md5.ComputeHash($stream))
  } finally {
    $md5.Dispose()
    $stream.Dispose()
  }
}

function Invoke-WriterCapabilityProbe {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][string]$BucketName,
    [Parameter(Mandatory = $true)][string]$ObjectPrefix,
    [AllowEmptyString()][string]$ApiEndpoint,
    [Parameter(Mandatory = $true)][string]$ApiRegion
  )

  $probeId = [Guid]::NewGuid().ToString("N")
  $probeKey = "$ObjectPrefix/daily/capability-probes/$probeId.bin"
  $outsidePrefixRoot = "shamrai-capability-denied-$probeId"
  if (
    $outsidePrefixRoot -eq $ObjectPrefix -or
    $outsidePrefixRoot.StartsWith("$ObjectPrefix/", [StringComparison]::Ordinal)
  ) {
    throw "Unable to construct a capability probe key outside the configured prefix."
  }
  $outsideKey = "$outsidePrefixRoot/probe.bin"
  $missingAdminKey = "$ObjectPrefix/daily/capability-probes/$probeId-admin-missing.bin"
  $missingAdminVersionId = "invalid-shamrai-admin-version-$probeId"
  $invalidUploadId = "invalid-shamrai-admin-probe-$probeId"
  $probeDirectory = Join-Path ([IO.Path]::GetTempPath()) "shamrai-s3-capability-$probeId"
  $probeBodyPath = Join-Path $probeDirectory "probe.bin"
  $downloadPath = Join-Path $probeDirectory "download.bin"
  New-Item -ItemType Directory -Path $probeDirectory | Out-Null
  try {
    $probeBody = "shamrai-s3-capability-probe-v1:$probeId"
    [IO.File]::WriteAllText($probeBodyPath, $probeBody, (New-Object Text.UTF8Encoding($false)))
    $probeSha256 = (Get-FileHash -LiteralPath $probeBodyPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $probeContentMd5 = Get-FileContentMd5Base64 -Path $probeBodyPath

    $putResult = Invoke-WriterAwsJson `
      -Executable $Executable `
      -Operation "put-object" `
      -BucketName $BucketName `
      -ApiEndpoint $ApiEndpoint `
      -ApiRegion $ApiRegion `
      -CapabilityName "PutObject" `
      -OperationArguments @(
        "--key", $probeKey,
        "--body", $probeBodyPath,
        "--content-md5", $probeContentMd5,
        "--content-type", "application/octet-stream",
        "--metadata", "sha256=$probeSha256"
      )
    $versionId = [string](Get-JsonPropertyValue -Object $putResult -Name "VersionId" -DefaultValue "")
    if ([string]::IsNullOrWhiteSpace($versionId) -or $versionId -in @("null", "None")) {
      throw "Writer capability probe 'PutObject' did not return a version id from the versioned bucket."
    }

    Invoke-WriterAwsJson `
      -Executable $Executable `
      -Operation "get-object" `
      -BucketName $BucketName `
      -ApiEndpoint $ApiEndpoint `
      -ApiRegion $ApiRegion `
      -CapabilityName "GetObject" `
      -OperationArguments @("--key", $probeKey, $downloadPath) | Out-Null
    $downloadSha256 = (Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($downloadSha256 -ne $probeSha256) {
      throw "Writer capability probe 'GetObject' returned content with a mismatched SHA-256."
    }

    $listResult = Invoke-WriterAwsJson `
      -Executable $Executable `
      -Operation "list-objects-v2" `
      -BucketName $BucketName `
      -ApiEndpoint $ApiEndpoint `
      -ApiRegion $ApiRegion `
      -CapabilityName "ListBucket" `
      -OperationArguments @("--prefix", $probeKey, "--max-keys", "1")
    $listedKeys = @(
      @(Get-JsonPropertyValue -Object $listResult -Name "Contents" -DefaultValue @()) |
        ForEach-Object { [string](Get-JsonPropertyValue -Object $_ -Name "Key" -DefaultValue "") }
    )
    if ($probeKey -notin $listedKeys) {
      throw "Writer capability probe 'ListBucket' did not return the object written under the configured prefix."
    }

    Assert-WriterAwsOperationDenied `
      -Executable $Executable `
      -Operation "delete-object" `
      -BucketName $BucketName `
      -ApiEndpoint $ApiEndpoint `
      -ApiRegion $ApiRegion `
      -CapabilityName "DeleteObject" `
      -OperationArguments @("--key", $probeKey)
    Assert-WriterAwsOperationDenied `
      -Executable $Executable `
      -Operation "delete-object" `
      -BucketName $BucketName `
      -ApiEndpoint $ApiEndpoint `
      -ApiRegion $ApiRegion `
      -CapabilityName "DeleteObjectVersion" `
      -OperationArguments @("--key", $missingAdminKey, "--version-id", $missingAdminVersionId)
    Assert-WriterAwsOperationDenied `
      -Executable $Executable `
      -Operation "put-object" `
      -BucketName $BucketName `
      -ApiEndpoint $ApiEndpoint `
      -ApiRegion $ApiRegion `
      -CapabilityName "outside-prefix PutObject" `
      -OperationArguments @(
        "--key", $outsideKey,
        "--body", $probeBodyPath,
        "--content-md5", $probeContentMd5,
        "--content-type", "application/octet-stream"
      )
    Assert-WriterAwsOperationDenied `
      -Executable $Executable `
      -Operation "get-object" `
      -BucketName $BucketName `
      -ApiEndpoint $ApiEndpoint `
      -ApiRegion $ApiRegion `
      -CapabilityName "outside-prefix GetObject" `
      -OperationArguments @("--key", $outsideKey, $downloadPath)
    Assert-WriterAwsOperationDenied `
      -Executable $Executable `
      -Operation "list-objects-v2" `
      -BucketName $BucketName `
      -ApiEndpoint $ApiEndpoint `
      -ApiRegion $ApiRegion `
      -CapabilityName "outside-prefix ListBucket" `
      -OperationArguments @("--prefix", "$outsidePrefixRoot/", "--max-keys", "1")

    $retentionUntil = [DateTimeOffset]::UtcNow.AddDays(1).ToString(
      "yyyy-MM-ddTHH:mm:ssZ",
      [Globalization.CultureInfo]::InvariantCulture
    )
    $adminProbeDefinitions = @(
      [pscustomobject]@{
        Operation = "put-object-retention"
        CapabilityName = "PutObjectRetention"
        OperationArguments = @(
          "--key", $missingAdminKey,
          "--version-id", $missingAdminVersionId,
          "--retention", "{`"Mode`":`"COMPLIANCE`",`"RetainUntilDate`":`"$retentionUntil`"}"
        )
      },
      [pscustomobject]@{
        Operation = "put-object-legal-hold"
        CapabilityName = "PutObjectLegalHold"
        OperationArguments = @(
          "--key", $missingAdminKey,
          "--version-id", $missingAdminVersionId,
          "--legal-hold", '{"Status":"ON"}'
        )
      },
      [pscustomobject]@{
        Operation = "put-object-acl"
        CapabilityName = "PutObjectAcl"
        OperationArguments = @(
          "--key", $missingAdminKey,
          "--acl", "private"
        )
      },
      [pscustomobject]@{
        Operation = "put-object-tagging"
        CapabilityName = "PutObjectTagging"
        OperationArguments = @(
          "--key", $missingAdminKey,
          "--tagging", '{"TagSet":[{"Key":"shamrai-capability-probe","Value":"deny"}]}'
        )
      },
      [pscustomobject]@{
        Operation = "delete-object-tagging"
        CapabilityName = "DeleteObjectTagging"
        OperationArguments = @(
          "--key", $missingAdminKey
        )
      },
      [pscustomobject]@{
        Operation = "abort-multipart-upload"
        CapabilityName = "AbortMultipartUpload"
        OperationArguments = @(
          "--key", $missingAdminKey,
          "--upload-id", $invalidUploadId
        )
      }
    )
    foreach ($adminProbe in $adminProbeDefinitions) {
      Assert-WriterAwsOperationDenied `
        -Executable $Executable `
        -Operation $adminProbe.Operation `
        -BucketName $BucketName `
        -ApiEndpoint $ApiEndpoint `
        -ApiRegion $ApiRegion `
        -CapabilityName $adminProbe.CapabilityName `
        -OperationArguments $adminProbe.OperationArguments
    }

    return [ordered]@{
      writer_capability_probe = "passed"
      writer_put_object_allowed = $true
      writer_get_object_allowed = $true
      writer_list_prefix_allowed = $true
      writer_delete_object_denied = $true
      writer_delete_object_version_denied = $true
      writer_outside_prefix_put_denied = $true
      writer_outside_prefix_get_denied = $true
      writer_outside_prefix_list_denied = $true
      writer_effective_permission_evidence = "live_aws_sts_iam_boundary_bucket_policy_v3+live_unconfounded_object_probes_v3"
      writer_delete_object_version_probe_target = "random_missing_key_and_version"
      writer_admin_probe_target = "random_missing_object_resources+aws_iam_boundary_v4"
      writer_admin_capability_probe = "passed"
      writer_put_object_retention_denied = $true
      writer_put_object_legal_hold_denied = $true
      writer_put_object_acl_denied = $true
      writer_put_object_tagging_denied = $true
      writer_delete_object_tagging_denied = $true
      writer_abort_multipart_upload_denied = $true
      writer_put_bucket_policy_denied = $true
      writer_delete_bucket_policy_denied = $true
      writer_delete_bucket_denied = $true
      writer_put_bucket_lifecycle_configuration_denied = $true
      writer_put_object_lock_configuration_denied = $true
      writer_put_bucket_versioning_denied = $true
    }
  } finally {
    $resolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $resolvedProbeDirectory = [IO.Path]::GetFullPath($probeDirectory)
    if ($resolvedProbeDirectory.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase)) {
      Remove-Item -Recurse -Force -LiteralPath $resolvedProbeDirectory -ErrorAction SilentlyContinue
    }
  }
}

function Write-Attestation {
  param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Attestation)

  New-Item -ItemType Directory -Force -Path $script:DeployDirectory | Out-Null
  $temporaryPath = Join-Path $script:DeployDirectory ("s3-retention-attestation.{0}.tmp" -f [Guid]::NewGuid().ToString("N"))
  try {
    $json = $Attestation | ConvertTo-Json -Depth 4
    $utf8WithoutBom = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($temporaryPath, "$json`n", $utf8WithoutBom)
    Move-Item -Force -LiteralPath $temporaryPath -Destination $script:AttestationPath
  } finally {
    if (Test-Path -LiteralPath $temporaryPath) {
      Remove-Item -Force -LiteralPath $temporaryPath
    }
  }
}

function Invoke-S3RetentionAudit {
  if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $script:DeployDirectory | Out-Null
    if (Test-Path -LiteralPath $script:AttestationPath) {
      Remove-Item -Force -LiteralPath $script:AttestationPath
    }
  }

  if ([string]::IsNullOrWhiteSpace($Bucket)) {
    throw "Bucket or SHAMRAI_BACKUP_S3_BUCKET is required."
  }
  $Bucket = $Bucket.Trim()
  if ([string]::IsNullOrWhiteSpace($Prefix)) {
    $Prefix = "shamrai"
  }
  $Prefix = $Prefix.Trim().Trim("/")
  Assert-SafeBucketAndPrefix -BucketName $Bucket -ObjectPrefix $Prefix

  $EndpointUrl = Get-NormalizedEndpointUrl -Value $EndpointUrl
  $StsEndpointUrl = Get-NormalizedEndpointUrl -Value $StsEndpointUrl
  if ([string]::IsNullOrEmpty($EndpointUrl)) {
    if (-not [string]::IsNullOrEmpty($StsEndpointUrl)) {
      throw "StsEndpointUrl must be empty when AWS default S3 endpoints are used."
    }
  } elseif ([string]::IsNullOrEmpty($StsEndpointUrl)) {
    $StsEndpointUrl = $EndpointUrl
  } elseif ($StsEndpointUrl -cne $EndpointUrl) {
    throw "For an S3-compatible provider, StsEndpointUrl must exactly match EndpointUrl."
  }
  if ([string]::IsNullOrWhiteSpace($Region)) {
    $Region = "us-east-1"
  }
  $Region = $Region.Trim()
  if ($Region -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,62}$') {
    throw "Region contains unsupported characters."
  }

  if ($DryRun) {
    Write-Host "DRY_RUN verify-shamrai-s3-retention"
    Write-Host "bucket=$Bucket"
    Write-Host "prefix=$Prefix"
    Write-Host "sts_endpoint_binding=same-origin-as-s3-or-aws-default"
    Write-Host "versioning_check=get-bucket-versioning"
    Write-Host "object_lock_check=get-object-lock-configuration"
    Write-Host "lifecycle_check=get-bucket-lifecycle-configuration"
    Write-Host "writer_identity_check=sts-get-caller-identity+iam-get-entity+iam-get-permissions-boundary"
    Write-Host "bucket_policy_check=get-bucket-policy+exact-writer-deny-boundary"
    Write-Host "writer_permissions_boundary_check=iam-get-user-or-role+get-policy+get-policy-version+exact-deny-boundary"
    Write-Host "writer_policy_source=runtime_env_or_temporary_file"
    Write-Host "audit_credentials_source=runtime_env:SHAMRAI_BACKUP_S3_AUDIT_*"
    Write-Host "writer_credentials_source=runtime_env:SHAMRAI_BACKUP_S3_*"
    Write-Host "writer_capability_probe=allow:PutObject,GetObject,ListBucket;deny:DeleteObject,DeleteObjectVersion,outside-prefix-PutObject,outside-prefix-GetObject,outside-prefix-ListBucket"
    Write-Host "writer_effective_permission_evidence=live_aws_sts_iam_boundary_bucket_policy_v3+live_unconfounded_object_probes_v3"
    Write-Host "writer_delete_object_version_probe_target=random_missing_key_and_version"
    Write-Host "writer_admin_probe_target=random_missing_object_resources+aws_iam_boundary_v4"
    Write-Host "writer_admin_capability_probe=live-deny:missing-object-admin-actions;bucket-policy+iam-boundary-deny:all-actions-except-prefix-PutObject,GetObject,ListBucket"
    Write-Host "github_provenance_source=GITHUB_REPOSITORY,GITHUB_RUN_ID,GITHUB_RUN_ATTEMPT,GITHUB_SHA"
    Write-Host "attestation=$($script:AttestationPath)"
    Write-Host "required_retention_days=current:$($script:MinimumCurrentRetentionDays),noncurrent:$($script:MinimumNoncurrentRetentionDays)"
    return
  }

  if (-not [string]::IsNullOrEmpty($EndpointUrl) -or -not [string]::IsNullOrEmpty($StsEndpointUrl)) {
    throw "Safe release attestation currently supports only AWS-managed default S3, STS, and IAM endpoints; custom S3-compatible providers require a provider-specific authoritative identity-policy verifier."
  }

  $auditAccessKey = $env:SHAMRAI_BACKUP_S3_AUDIT_ACCESS_KEY_ID
  $auditSecretKey = $env:SHAMRAI_BACKUP_S3_AUDIT_SECRET_ACCESS_KEY
  $auditSessionToken = $env:SHAMRAI_BACKUP_S3_AUDIT_SESSION_TOKEN
  $writerAccessKey = $env:SHAMRAI_BACKUP_S3_ACCESS_KEY_ID
  $writerSecretKey = $env:SHAMRAI_BACKUP_S3_SECRET_ACCESS_KEY
  $writerSessionToken = $env:SHAMRAI_BACKUP_S3_SESSION_TOKEN
  $writerPolicyJson = $env:SHAMRAI_BACKUP_S3_WRITER_POLICY_JSON
  if ([string]::IsNullOrWhiteSpace($auditAccessKey)) {
    throw "SHAMRAI_BACKUP_S3_AUDIT_ACCESS_KEY_ID is required at runtime."
  }
  if ([string]::IsNullOrWhiteSpace($auditSecretKey)) {
    throw "SHAMRAI_BACKUP_S3_AUDIT_SECRET_ACCESS_KEY is required at runtime."
  }
  if ([string]::IsNullOrWhiteSpace($writerAccessKey)) {
    throw "SHAMRAI_BACKUP_S3_ACCESS_KEY_ID is required at runtime for writer capability probes."
  }
  if ([string]::IsNullOrWhiteSpace($writerSecretKey)) {
    throw "SHAMRAI_BACKUP_S3_SECRET_ACCESS_KEY is required at runtime for writer capability probes."
  }
  if ($writerAccessKey -ceq $auditAccessKey) {
    throw "S3 audit and backup writer must use separate credential identities."
  }
  if (
    -not [string]::IsNullOrWhiteSpace($writerPolicyJson) -and
    -not [string]::IsNullOrWhiteSpace($WriterPolicyPath)
  ) {
    throw "Provide writer policy evidence through either SHAMRAI_BACKUP_S3_WRITER_POLICY_JSON or WriterPolicyPath, not both."
  }
  if (-not [string]::IsNullOrWhiteSpace($WriterPolicyPath)) {
    $policyFile = Get-Item -LiteralPath $WriterPolicyPath -Force -ErrorAction Stop
    if (
      $policyFile.PSIsContainer -or
      ($policyFile.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
      $policyFile.Length -lt 2 -or
      $policyFile.Length -gt 1048576
    ) {
      throw "WriterPolicyPath must be a regular, non-symlink JSON file no larger than 1 MiB."
    }
    $writerPolicyJson = [IO.File]::ReadAllText($policyFile.FullName, [Text.Encoding]::UTF8)
  }
  if ([string]::IsNullOrWhiteSpace($writerPolicyJson)) {
    throw "Writer policy evidence is required at runtime through SHAMRAI_BACKUP_S3_WRITER_POLICY_JSON or WriterPolicyPath."
  }
  if ($null -eq (Get-Command $AwsExecutable -ErrorAction SilentlyContinue)) {
    throw "AWS CLI executable was not found."
  }

  Assert-WriterPolicy -PolicyJson $writerPolicyJson -BucketName $Bucket -ObjectPrefix $Prefix
  $writerPolicySha256 = Get-Sha256Hex -Value $writerPolicyJson
  $writerAccessKeyIdSha256 = Get-Sha256Hex -Value $writerAccessKey
  $githubProvenance = Get-GitHubWorkflowProvenance

  $credentialEnvironmentNames = @(
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_PAGER"
  )
  $originalEnvironment = @{}
  foreach ($name in $credentialEnvironmentNames) {
    $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
  }

  try {
    Set-AwsCredentialEnvironment `
      -AccessKeyId $auditAccessKey `
      -SecretAccessKey $auditSecretKey `
      -SessionToken $auditSessionToken `
      -ApiRegion $Region

    $versioning = Invoke-AwsJson `
      -Executable $AwsExecutable `
      -Operation "get-bucket-versioning" `
      -BucketName $Bucket `
      -ApiEndpoint $EndpointUrl `
      -ApiRegion $Region
    $objectLock = Invoke-AwsJson `
      -Executable $AwsExecutable `
      -Operation "get-object-lock-configuration" `
      -BucketName $Bucket `
      -ApiEndpoint $EndpointUrl `
      -ApiRegion $Region
    $lifecycle = Invoke-AwsJson `
      -Executable $AwsExecutable `
      -Operation "get-bucket-lifecycle-configuration" `
      -BucketName $Bucket `
      -ApiEndpoint $EndpointUrl `
      -ApiRegion $Region
    $bucketPolicyResponse = Invoke-AwsJson `
      -Executable $AwsExecutable `
      -Operation "get-bucket-policy" `
      -BucketName $Bucket `
      -ApiEndpoint $EndpointUrl `
      -ApiRegion $Region

    $versioningStatus = Assert-VersioningConfiguration -Configuration $versioning
    $objectLockResult = Assert-ObjectLockConfiguration -Configuration $objectLock
    $lifecycleResult = Assert-LifecycleConfiguration -Configuration $lifecycle -ObjectPrefix $Prefix
    $bucketPolicyJson = Get-LiveBucketPolicyJson -Response $bucketPolicyResponse
    $bucketDenyPolicySha256 = Get-Sha256Hex -Value $bucketPolicyJson

    Set-AwsCredentialEnvironment `
      -AccessKeyId $writerAccessKey `
      -SecretAccessKey $writerSecretKey `
      -SessionToken $writerSessionToken `
      -ApiRegion $Region
    $writerCallerIdentity = Invoke-AwsStsCallerIdentity `
      -Executable $AwsExecutable `
      -ApiEndpoint $StsEndpointUrl `
      -ApiRegion $Region
    $writerCallerArn = [string](Get-JsonPropertyValue -Object $writerCallerIdentity -Name "Arn" -DefaultValue "")
    if ([string]::IsNullOrWhiteSpace($writerCallerArn)) {
      throw "Writer STS get-caller-identity did not return Arn."
    }
    $writerPrincipal = Get-StableWriterPrincipal -CallerArn $writerCallerArn
    $bucketDenyPolicyResult = Assert-ImmutableBucketDenyPolicy `
      -PolicyJson $bucketPolicyJson `
      -BucketName $Bucket `
      -ObjectPrefix $Prefix `
      -WriterPrincipalArn $writerPrincipal.StableArn
    $writerCallerArnSha256 = Get-Sha256Hex -Value $writerPrincipal.CallerArn
    $writerStablePrincipalArnSha256 = Get-Sha256Hex -Value $writerPrincipal.StableArn

    Set-AwsCredentialEnvironment `
      -AccessKeyId $auditAccessKey `
      -SecretAccessKey $auditSecretKey `
      -SessionToken $auditSessionToken `
      -ApiRegion $Region
    $writerBoundaryEvidence = Get-LiveWriterPermissionBoundaryEvidence `
      -Executable $AwsExecutable `
      -ApiRegion $Region `
      -WriterPrincipal $writerPrincipal
    $writerBoundaryResult = Assert-WriterPermissionBoundary `
      -PolicyJson $writerBoundaryEvidence.PolicyJson `
      -BucketName $Bucket `
      -ObjectPrefix $Prefix
    $writerBoundaryPolicySha256 = Get-Sha256Hex -Value $writerBoundaryEvidence.PolicyJson
    $writerBoundaryArnSha256 = Get-Sha256Hex -Value $writerBoundaryEvidence.BoundaryArn

    Set-AwsCredentialEnvironment `
      -AccessKeyId $writerAccessKey `
      -SecretAccessKey $writerSecretKey `
      -SessionToken $writerSessionToken `
      -ApiRegion $Region
    $writerCapabilities = Invoke-WriterCapabilityProbe `
      -Executable $AwsExecutable `
      -BucketName $Bucket `
      -ObjectPrefix $Prefix `
      -ApiEndpoint $EndpointUrl `
      -ApiRegion $Region

    Set-AwsCredentialEnvironment `
      -AccessKeyId $auditAccessKey `
      -SecretAccessKey $auditSecretKey `
      -SessionToken $auditSessionToken `
      -ApiRegion $Region
    $bucketPolicyAfterResponse = Invoke-AwsJson `
      -Executable $AwsExecutable `
      -Operation "get-bucket-policy" `
      -BucketName $Bucket `
      -ApiEndpoint $EndpointUrl `
      -ApiRegion $Region
    $bucketPolicyAfterJson = Get-LiveBucketPolicyJson -Response $bucketPolicyAfterResponse
    if ((Get-Sha256Hex -Value $bucketPolicyAfterJson) -cne $bucketDenyPolicySha256) {
      throw "Live S3 bucket policy changed during writer verification."
    }
    $writerBoundaryAfter = Get-LiveWriterPermissionBoundaryEvidence `
      -Executable $AwsExecutable `
      -ApiRegion $Region `
      -WriterPrincipal $writerPrincipal
    if (
      $writerBoundaryAfter.BoundaryArn -cne $writerBoundaryEvidence.BoundaryArn -or
      $writerBoundaryAfter.DefaultVersionId -cne $writerBoundaryEvidence.DefaultVersionId -or
      (Get-Sha256Hex -Value $writerBoundaryAfter.PolicyJson) -cne $writerBoundaryPolicySha256
    ) {
      throw "Live writer IAM permissions boundary changed during verification."
    }

    $attestation = [ordered]@{
      schema_version = $script:AttestationSchemaVersion
      status = "passed"
      verified_at = [DateTimeOffset]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ", [Globalization.CultureInfo]::InvariantCulture)
      bucket = $Bucket
      prefix = $Prefix
      endpoint_url = $EndpointUrl
      sts_endpoint_url = $StsEndpointUrl
      region = $Region
      versioning_status = $versioningStatus
      object_lock_status = $objectLockResult.Status
      default_retention_days = $objectLockResult.Days
      current_expiration_days = $lifecycleResult.CurrentDays
      noncurrent_expiration_days = $lifecycleResult.NoncurrentDays
      bucket_deny_policy_status = $bucketDenyPolicyResult.Status
      bucket_deny_policy_sha256 = $bucketDenyPolicySha256
      bucket_deny_policy_required_rule_count = $bucketDenyPolicyResult.RequiredRuleCount
      writer_policy_sha256 = $writerPolicySha256
      writer_access_key_id_sha256 = $writerAccessKeyIdSha256
      writer_identity_evidence = "aws_sts_and_iam_permissions_boundary_v1"
      writer_principal_type = $writerPrincipal.PrincipalType
      writer_caller_arn_sha256 = $writerCallerArnSha256
      writer_stable_principal_arn_sha256 = $writerStablePrincipalArnSha256
      writer_permissions_boundary_status = $writerBoundaryResult.Status
      writer_permissions_boundary_sha256 = $writerBoundaryPolicySha256
      writer_permissions_boundary_arn_sha256 = $writerBoundaryArnSha256
      writer_permissions_boundary_version_id = $writerBoundaryEvidence.DefaultVersionId
      writer_permissions_boundary_required_rule_count = $writerBoundaryResult.RequiredRuleCount
      repository = $githubProvenance.repository
      workflow_run_id = $githubProvenance.workflow_run_id
      workflow_run_attempt = $githubProvenance.workflow_run_attempt
      git_sha = $githubProvenance.git_sha
      writer_capability_probe = $writerCapabilities.writer_capability_probe
      writer_put_object_allowed = $writerCapabilities.writer_put_object_allowed
      writer_get_object_allowed = $writerCapabilities.writer_get_object_allowed
      writer_list_prefix_allowed = $writerCapabilities.writer_list_prefix_allowed
      writer_delete_object_denied = $writerCapabilities.writer_delete_object_denied
      writer_delete_object_version_denied = $writerCapabilities.writer_delete_object_version_denied
      writer_outside_prefix_put_denied = $writerCapabilities.writer_outside_prefix_put_denied
      writer_outside_prefix_get_denied = $writerCapabilities.writer_outside_prefix_get_denied
      writer_outside_prefix_list_denied = $writerCapabilities.writer_outside_prefix_list_denied
      writer_effective_permission_evidence = $writerCapabilities.writer_effective_permission_evidence
      writer_delete_object_version_probe_target = $writerCapabilities.writer_delete_object_version_probe_target
      writer_admin_probe_target = $writerCapabilities.writer_admin_probe_target
      writer_admin_capability_probe = $writerCapabilities.writer_admin_capability_probe
      writer_put_object_retention_denied = $writerCapabilities.writer_put_object_retention_denied
      writer_put_object_legal_hold_denied = $writerCapabilities.writer_put_object_legal_hold_denied
      writer_put_object_acl_denied = $writerCapabilities.writer_put_object_acl_denied
      writer_put_object_tagging_denied = $writerCapabilities.writer_put_object_tagging_denied
      writer_delete_object_tagging_denied = $writerCapabilities.writer_delete_object_tagging_denied
      writer_abort_multipart_upload_denied = $writerCapabilities.writer_abort_multipart_upload_denied
      writer_put_bucket_policy_denied = $writerCapabilities.writer_put_bucket_policy_denied
      writer_delete_bucket_policy_denied = $writerCapabilities.writer_delete_bucket_policy_denied
      writer_delete_bucket_denied = $writerCapabilities.writer_delete_bucket_denied
      writer_put_bucket_lifecycle_configuration_denied = $writerCapabilities.writer_put_bucket_lifecycle_configuration_denied
      writer_put_object_lock_configuration_denied = $writerCapabilities.writer_put_object_lock_configuration_denied
      writer_put_bucket_versioning_denied = $writerCapabilities.writer_put_bucket_versioning_denied
    }
    Write-Attestation -Attestation $attestation
  } finally {
    foreach ($name in $credentialEnvironmentNames) {
      [Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], "Process")
    }
    Clear-Variable -Name auditSecretKey, auditSessionToken, writerSecretKey, writerSessionToken -ErrorAction SilentlyContinue
  }

  Write-Host "status=passed"
  Write-Host "bucket=$Bucket"
  Write-Host "prefix=$Prefix"
  Write-Host "attestation=$($script:AttestationPath)"
}

Invoke-S3RetentionAudit
