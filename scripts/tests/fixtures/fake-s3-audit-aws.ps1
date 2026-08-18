param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-OptionValue {
  param([Parameter(Mandatory = $true)][string]$Name)

  $index = [Array]::IndexOf($Arguments, $Name)
  if ($index -lt 0 -or $index + 1 -ge $Arguments.Count) {
    return ""
  }
  return $Arguments[$index + 1]
}

function Assert-SyntheticCredentialRole {
  param([Parameter(Mandatory = $true)][ValidateSet("audit", "writer")][string]$Role)

  $expectedAccessKey = if ($Role -eq "audit") {
    "synthetic-audit-access-key"
  } else {
    "synthetic-writer-access-key"
  }
  $expectedSecretKey = if ($Role -eq "audit") {
    "synthetic-audit-secret-key"
  } else {
    "synthetic-writer-secret-key"
  }
  if (
    $env:AWS_ACCESS_KEY_ID -ne $expectedAccessKey -or
    $env:AWS_SECRET_ACCESS_KEY -ne $expectedSecretKey
  ) {
    [Console]::Error.WriteLine("Synthetic $Role credentials were not mapped to the AWS child process.")
    exit 9
  }
}

function Write-ProbeLog {
  param(
    [Parameter(Mandatory = $true)][string]$Role,
    [Parameter(Mandatory = $true)][string]$Operation,
    [string]$Detail = ""
  )

  if ([string]::IsNullOrWhiteSpace($env:SHAMRAI_FAKE_S3_LOG_PATH)) {
    return
  }
  $line = "$Role`t$Operation`t$Detail`n"
  [IO.File]::AppendAllText($env:SHAMRAI_FAKE_S3_LOG_PATH, $line, (New-Object Text.UTF8Encoding($false)))
}

function Deny-S3Operation {
  param([Parameter(Mandatory = $true)][string]$Operation)

  Write-Error `
    -Message "An error occurred (AccessDenied) when calling the $Operation operation: Access Denied" `
    -ErrorAction Continue
  exit 254
}

function Fail-S3Operation {
  param(
    [Parameter(Mandatory = $true)][string]$Code,
    [Parameter(Mandatory = $true)][string]$Operation,
    [Parameter(Mandatory = $true)][string]$Message
  )

  Write-Error `
    -Message "An error occurred ($Code) when calling the $Operation operation: $Message" `
    -ErrorAction Continue
  exit 254
}

function Get-ProbeState {
  $statePath = Join-Path $env:SHAMRAI_FAKE_S3_STATE_DIR "probe-state.json"
  if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) {
    [Console]::Error.WriteLine("Synthetic probe state does not exist.")
    exit 7
  }
  return Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
}

function Assert-SyntheticMissingAdminKey {
  param([Parameter(Mandatory = $true)][string]$CandidateKey)

  $state = Get-ProbeState
  if (
    [string]::IsNullOrWhiteSpace($CandidateKey) -or
    -not $CandidateKey.StartsWith("shamrai/daily/capability-probes/", [StringComparison]::Ordinal) -or
    $CandidateKey.IndexOf("admin-missing", [StringComparison]::Ordinal) -lt 0 -or
    $CandidateKey -eq $state.key
  ) {
    [Console]::Error.WriteLine("Synthetic administrative probe did not target a randomized missing object.")
    exit 7
  }
}

function Assert-SyntheticMissingAdminVersion {
  param([Parameter(Mandatory = $true)][string]$CandidateVersionId)

  if (-not $CandidateVersionId.StartsWith("invalid-shamrai-admin-version-", [StringComparison]::Ordinal)) {
    [Console]::Error.WriteLine("Synthetic administrative probe did not target a randomized missing object version.")
    exit 7
  }
}

function Assert-NoVersionId {
  param([Parameter(Mandatory = $true)][string]$CapabilityName)

  if (-not [string]::IsNullOrWhiteSpace((Get-OptionValue -Name "--version-id"))) {
    [Console]::Error.WriteLine("Synthetic $CapabilityName probe unexpectedly tested a version-specific IAM action.")
    exit 7
  }
}

function Get-SyntheticBucketPolicyResponse {
  param([AllowEmptyString()][string]$Mode)

  $writerPrincipal = if ($Mode -eq "wrong-bucket-principal") {
    "arn:aws:iam::123456789012:user/not-the-backup-writer"
  } else {
    "arn:aws:iam::123456789012:user/shamrai-backup-writer"
  }
  $allowedActions = if ($Mode -eq "broad-notaction-exception") {
    @("s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:PutBucketAcl")
  } else {
    @("s3:PutObject", "s3:GetObject", "s3:ListBucket")
  }
  $denyAllOtherActions = if ($Mode -eq "conditional-bucket-deny") {
    [ordered]@{
      Effect = "Deny"
      Principal = [ordered]@{ AWS = $writerPrincipal }
      NotAction = $allowedActions
      Resource = @("arn:aws:s3:::example-backups", "arn:aws:s3:::example-backups/*")
      Condition = [ordered]@{ StringEquals = [ordered]@{ "aws:SourceAccount" = "synthetic" } }
    }
  } else {
    [ordered]@{
      Effect = "Deny"
      Principal = [ordered]@{ AWS = $writerPrincipal }
      NotAction = $allowedActions
      Resource = @("arn:aws:s3:::example-backups", "arn:aws:s3:::example-backups/*")
    }
  }
  $denyOutsideObjectPrefix = [ordered]@{
    Effect = "Deny"
    Principal = [ordered]@{ AWS = $writerPrincipal }
    Action = @("s3:PutObject", "s3:GetObject")
    NotResource = "arn:aws:s3:::example-backups/shamrai/*"
  }
  $denyOutsideListPrefix = [ordered]@{
    Effect = "Deny"
    Principal = [ordered]@{ AWS = $writerPrincipal }
    Action = "s3:ListBucket"
    Resource = "arn:aws:s3:::example-backups"
    Condition = [ordered]@{
      StringNotLike = [ordered]@{ "s3:prefix" = @("shamrai", "shamrai/*") }
    }
  }
  $denyMissingListPrefix = [ordered]@{
    Effect = "Deny"
    Principal = [ordered]@{ AWS = $writerPrincipal }
    Action = "s3:ListBucket"
    Resource = "arn:aws:s3:::example-backups"
    Condition = [ordered]@{ Null = [ordered]@{ "s3:prefix" = "true" } }
  }
  $statements = if ($Mode -eq "incomplete-bucket-deny") {
    @($denyAllOtherActions, $denyOutsideObjectPrefix, $denyOutsideListPrefix)
  } else {
    @($denyAllOtherActions, $denyOutsideObjectPrefix, $denyOutsideListPrefix, $denyMissingListPrefix)
  }
  $policyJson = [ordered]@{
    Version = "2012-10-17"
    Statement = $statements
  } | ConvertTo-Json -Depth 8 -Compress
  return [ordered]@{ Policy = $policyJson } | ConvertTo-Json -Depth 8 -Compress
}

function Get-SyntheticWriterBoundaryPolicy {
  param([AllowEmptyString()][string]$Mode)

  $allowedActions = if ($Mode -eq "unsafe-boundary-notaction") {
    @("s3:PutObject", "s3:GetObject", "s3:ListBucket", "sts:AssumeRole")
  } else {
    @("s3:PutObject", "s3:GetObject", "s3:ListBucket")
  }
  $statements = @(
    [ordered]@{
      Effect = "Allow"
      Action = @("s3:PutObject", "s3:GetObject")
      Resource = "arn:aws:s3:::example-backups/shamrai/*"
    },
    [ordered]@{
      Effect = "Allow"
      Action = "s3:ListBucket"
      Resource = "arn:aws:s3:::example-backups"
      Condition = [ordered]@{
        StringLike = [ordered]@{ "s3:prefix" = @("shamrai", "shamrai/*") }
      }
    },
    [ordered]@{
      Effect = "Deny"
      NotAction = $allowedActions
      Resource = "*"
    },
    [ordered]@{
      Effect = "Deny"
      Action = @("s3:PutObject", "s3:GetObject")
      NotResource = "arn:aws:s3:::example-backups/shamrai/*"
    },
    [ordered]@{
      Effect = "Deny"
      Action = "s3:ListBucket"
      NotResource = "arn:aws:s3:::example-backups"
    },
    [ordered]@{
      Effect = "Deny"
      Action = "s3:ListBucket"
      Resource = "arn:aws:s3:::example-backups"
      Condition = [ordered]@{
        StringNotLike = [ordered]@{ "s3:prefix" = @("shamrai", "shamrai/*") }
      }
    },
    [ordered]@{
      Effect = "Deny"
      Action = "s3:ListBucket"
      Resource = "arn:aws:s3:::example-backups"
      Condition = [ordered]@{ Null = [ordered]@{ "s3:prefix" = "true" } }
    }
  )
  if ($Mode -eq "boundary-allows-other-bucket-list") {
    $statements = @($statements[0], $statements[1], $statements[2], $statements[3], $statements[5], $statements[6])
  }
  return [ordered]@{
    Version = "2012-10-17"
    Statement = $statements
  }
}

$mode = [string]$env:SHAMRAI_FAKE_S3_MODE
$service = if ($Arguments.Count -ge 1) { $Arguments[0] } else { "" }
$operation = if ($Arguments.Count -ge 2) { $Arguments[1] } else { "" }
if ($service -eq "sts" -and $operation -eq "get-caller-identity") {
  Assert-SyntheticCredentialRole -Role writer
  Write-ProbeLog -Role "writer" -Operation "sts-get-caller-identity"
  if ($mode -eq "unsupported-sts-principal") {
    '{"UserId":"synthetic","Account":"123456789012","Arn":"arn:aws:sts::123456789012:federated-user/unsupported"}'
  } else {
    '{"UserId":"synthetic","Account":"123456789012","Arn":"arn:aws:iam::123456789012:user/shamrai-backup-writer"}'
  }
  exit 0
}

if ($service -eq "iam") {
  Assert-SyntheticCredentialRole -Role audit
  Write-ProbeLog -Role "audit" -Operation "iam-$operation"
  $boundaryArn = "arn:aws:iam::123456789012:policy/shamrai-backup-boundary"
  switch ($operation) {
    "get-user" {
      if ((Get-OptionValue -Name "--user-name") -ne "shamrai-backup-writer") {
        [Console]::Error.WriteLine("Synthetic IAM lookup did not target the STS-derived writer user.")
        exit 7
      }
      $user = [ordered]@{
        Arn = "arn:aws:iam::123456789012:user/shamrai-backup-writer"
      }
      if ($mode -ne "missing-permissions-boundary") {
        $user.PermissionsBoundary = [ordered]@{
          PermissionsBoundaryType = "Policy"
          PermissionsBoundaryArn = $boundaryArn
        }
      }
      [ordered]@{ User = $user } | ConvertTo-Json -Depth 8 -Compress
    }
    "get-policy" {
      if ((Get-OptionValue -Name "--policy-arn") -ne $boundaryArn) {
        [Console]::Error.WriteLine("Synthetic IAM policy lookup used an unexpected boundary ARN.")
        exit 7
      }
      [ordered]@{
        Policy = [ordered]@{ Arn = $boundaryArn; DefaultVersionId = "v1" }
      } | ConvertTo-Json -Depth 8 -Compress
    }
    "get-policy-version" {
      if (
        (Get-OptionValue -Name "--policy-arn") -ne $boundaryArn -or
        (Get-OptionValue -Name "--version-id") -ne "v1"
      ) {
        [Console]::Error.WriteLine("Synthetic IAM policy-version lookup used unexpected coordinates.")
        exit 7
      }
      [ordered]@{
        PolicyVersion = [ordered]@{
          Document = Get-SyntheticWriterBoundaryPolicy -Mode $mode
          VersionId = "v1"
          IsDefaultVersion = $true
        }
      } | ConvertTo-Json -Depth 16 -Compress
    }
    default {
      [Console]::Error.WriteLine("Unexpected synthetic IAM operation: $operation")
      exit 8
    }
  }
  exit 0
}

$adminOperations = @(
  "get-bucket-versioning",
  "get-object-lock-configuration",
  "get-bucket-lifecycle-configuration",
  "get-bucket-policy"
)
if ($operation -in $adminOperations) {
  Assert-SyntheticCredentialRole -Role audit
  Write-ProbeLog -Role "audit" -Operation $operation
  switch ($operation) {
    "get-bucket-versioning" {
      '{"Status":"Enabled"}'
    }
    "get-object-lock-configuration" {
      '{"ObjectLockConfiguration":{"ObjectLockEnabled":"Enabled","Rule":{"DefaultRetention":{"Mode":"COMPLIANCE","Days":35}}}}'
    }
    "get-bucket-lifecycle-configuration" {
      '{"Rules":[{"ID":"shamrai-retention","Status":"Enabled","Filter":{"Prefix":"shamrai/"},"Expiration":{"Days":35},"NoncurrentVersionExpiration":{"NoncurrentDays":90}}]}'
    }
    "get-bucket-policy" {
      if ($mode -eq "missing-bucket-policy") {
        Fail-S3Operation -Code "NoSuchBucketPolicy" -Operation "GetBucketPolicy" -Message "The bucket policy does not exist"
      }
      Get-SyntheticBucketPolicyResponse -Mode $mode
    }
  }
  exit 0
}

Assert-SyntheticCredentialRole -Role writer
if ([string]::IsNullOrWhiteSpace($env:SHAMRAI_FAKE_S3_STATE_DIR)) {
  [Console]::Error.WriteLine("Synthetic probe state directory is missing.")
  exit 7
}
New-Item -ItemType Directory -Force -Path $env:SHAMRAI_FAKE_S3_STATE_DIR | Out-Null

$key = Get-OptionValue -Name "--key"
$prefix = Get-OptionValue -Name "--prefix"
$isInsideDailyPrefix = $key.StartsWith("shamrai/daily/", [StringComparison]::Ordinal)
$isInsideListPrefix = $prefix.StartsWith("shamrai/daily/", [StringComparison]::Ordinal)

switch ($operation) {
  "put-object" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail $(if ($isInsideDailyPrefix) { "inside" } else { "outside" })
    if (-not $isInsideDailyPrefix) {
      if ($mode -eq "allow-outside-put") {
        '{"ETag":"\"outside\"","VersionId":"outside-version-1"}'
        exit 0
      }
      Deny-S3Operation -Operation "PutObject"
    }
    if ($mode -eq "deny-put") {
      Deny-S3Operation -Operation "PutObject"
    }
    $bodyPath = Get-OptionValue -Name "--body"
    if ([string]::IsNullOrWhiteSpace($bodyPath) -or -not (Test-Path -LiteralPath $bodyPath -PathType Leaf)) {
      [Console]::Error.WriteLine("Synthetic PutObject body is missing.")
      exit 7
    }
    $storedBodyPath = Join-Path $env:SHAMRAI_FAKE_S3_STATE_DIR "probe-body.bin"
    Copy-Item -Force -LiteralPath $bodyPath -Destination $storedBodyPath
    $state = [ordered]@{
      key = $key
      body_path = $storedBodyPath
      version_id = "fake-version-1"
    }
    [IO.File]::WriteAllText(
      (Join-Path $env:SHAMRAI_FAKE_S3_STATE_DIR "probe-state.json"),
      (($state | ConvertTo-Json -Compress) + "`n"),
      (New-Object Text.UTF8Encoding($false))
    )
    '{"ETag":"\"inside\"","VersionId":"fake-version-1"}'
  }
  "get-object" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail $(if ($isInsideDailyPrefix) { "inside" } else { "outside" })
    if (-not $isInsideDailyPrefix) {
      if ($mode -eq "allow-outside-get") {
        Write-Error `
          -Message "An error occurred (NoSuchKey) when calling the GetObject operation: The specified key does not exist" `
          -ErrorAction Continue
        exit 254
      }
      Deny-S3Operation -Operation "GetObject"
    }
    $state = Get-ProbeState
    if ($key -ne $state.key) {
      [Console]::Error.WriteLine("An error occurred (NoSuchKey) when calling the GetObject operation.")
      exit 254
    }
    $keyIndex = [Array]::IndexOf($Arguments, "--key")
    $destinationPath = if ($keyIndex -ge 0 -and $keyIndex + 2 -lt $Arguments.Count) {
      $Arguments[$keyIndex + 2]
    } else {
      ""
    }
    if ([string]::IsNullOrWhiteSpace($destinationPath) -or $destinationPath.StartsWith("--")) {
      [Console]::Error.WriteLine("Synthetic GetObject destination is missing.")
      exit 7
    }
    Copy-Item -Force -LiteralPath $state.body_path -Destination $destinationPath
    '{"AcceptRanges":"bytes","VersionId":"fake-version-1"}'
  }
  "list-objects-v2" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail $(if ($isInsideListPrefix) { "inside" } else { "outside" })
    if (-not $isInsideListPrefix) {
      if ($mode -eq "allow-outside-list") {
        '{"KeyCount":0,"Contents":[]}'
        exit 0
      }
      Deny-S3Operation -Operation "ListObjectsV2"
    }
    $state = Get-ProbeState
    [ordered]@{
      KeyCount = 1
      Contents = @([ordered]@{ Key = [string]$state.key })
    } | ConvertTo-Json -Depth 4 -Compress
  }
  "delete-object" {
    $versionId = Get-OptionValue -Name "--version-id"
    $deleteKind = if ([string]::IsNullOrWhiteSpace($versionId)) { "current" } else { "version" }
    Write-ProbeLog -Role "writer" -Operation $operation -Detail $deleteKind
    if ($deleteKind -eq "version") {
      Assert-SyntheticMissingAdminKey -CandidateKey $key
      Assert-SyntheticMissingAdminVersion -CandidateVersionId $versionId
    }
    if ($mode -eq "transient-delete-error") {
      [Console]::Error.WriteLine("Could not connect to the endpoint URL")
      exit 255
    }
    if ($mode -eq "allow-delete" -and $deleteKind -eq "current") {
      '{"DeleteMarker":true,"VersionId":"delete-marker-1"}'
      exit 0
    }
    if ($mode -eq "allow-delete-version" -and $deleteKind -eq "version") {
      Fail-S3Operation -Code "NoSuchVersion" -Operation "DeleteObjectVersion" -Message "The specified version does not exist"
    }
    Deny-S3Operation -Operation "DeleteObject"
  }
  "put-object-retention" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail "admin"
    Assert-SyntheticMissingAdminKey -CandidateKey $key
    Assert-SyntheticMissingAdminVersion -CandidateVersionId (Get-OptionValue -Name "--version-id")
    if ($mode -eq "allow-put-object-retention") {
      Fail-S3Operation -Code "NoSuchKey" -Operation "PutObjectRetention" -Message "The specified key does not exist"
    }
    Deny-S3Operation -Operation "PutObjectRetention"
  }
  "put-object-legal-hold" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail "admin"
    Assert-SyntheticMissingAdminKey -CandidateKey $key
    Assert-SyntheticMissingAdminVersion -CandidateVersionId (Get-OptionValue -Name "--version-id")
    if ($mode -eq "allow-put-object-legal-hold") {
      Fail-S3Operation -Code "NoSuchKey" -Operation "PutObjectLegalHold" -Message "The specified key does not exist"
    }
    Deny-S3Operation -Operation "PutObjectLegalHold"
  }
  "put-object-acl" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail "admin"
    Assert-SyntheticMissingAdminKey -CandidateKey $key
    Assert-NoVersionId -CapabilityName "PutObjectAcl"
    if ($mode -eq "allow-put-object-acl") {
      Fail-S3Operation -Code "NoSuchKey" -Operation "PutObjectAcl" -Message "The specified key does not exist"
    }
    Deny-S3Operation -Operation "PutObjectAcl"
  }
  "put-object-tagging" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail "admin"
    Assert-SyntheticMissingAdminKey -CandidateKey $key
    Assert-NoVersionId -CapabilityName "PutObjectTagging"
    if ($mode -eq "allow-put-object-tagging") {
      Fail-S3Operation -Code "NoSuchKey" -Operation "PutObjectTagging" -Message "The specified key does not exist"
    }
    Deny-S3Operation -Operation "PutObjectTagging"
  }
  "delete-object-tagging" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail "admin"
    Assert-SyntheticMissingAdminKey -CandidateKey $key
    Assert-NoVersionId -CapabilityName "DeleteObjectTagging"
    if ($mode -eq "allow-delete-object-tagging") {
      '{}'
      exit 0
    }
    Deny-S3Operation -Operation "DeleteObjectTagging"
  }
  "abort-multipart-upload" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail "admin"
    Assert-SyntheticMissingAdminKey -CandidateKey $key
    $uploadId = Get-OptionValue -Name "--upload-id"
    if (-not $uploadId.StartsWith("invalid-shamrai-admin-probe-", [StringComparison]::Ordinal)) {
      [Console]::Error.WriteLine("Synthetic AbortMultipartUpload probe did not use an invalid upload id.")
      exit 7
    }
    if ($mode -eq "allow-abort-multipart-upload") {
      Fail-S3Operation -Code "NoSuchUpload" -Operation "AbortMultipartUpload" -Message "The specified upload does not exist"
    }
    Deny-S3Operation -Operation "AbortMultipartUpload"
  }
  "put-bucket-policy" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail "admin"
    if (
      (Get-OptionValue -Name "--policy") -ne "{" -or
      (Get-OptionValue -Name "--content-md5") -ne "AAAAAAAAAAAAAAAAAAAAAA=="
    ) {
      [Console]::Error.WriteLine("Synthetic PutBucketPolicy probe was not guarded by malformed JSON and an invalid Content-MD5.")
      exit 7
    }
    if ($mode -eq "allow-put-bucket-policy") {
      Fail-S3Operation -Code "MalformedPolicy" -Operation "PutBucketPolicy" -Message "Policy has invalid JSON"
    }
    Deny-S3Operation -Operation "PutBucketPolicy"
  }
  "delete-bucket" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail "admin"
    Get-ProbeState | Out-Null
    if ($mode -eq "allow-delete-bucket") {
      Fail-S3Operation -Code "BucketNotEmpty" -Operation "DeleteBucket" -Message "The bucket you tried to delete is not empty"
    }
    Deny-S3Operation -Operation "DeleteBucket"
  }
  "put-bucket-lifecycle-configuration" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail "admin"
    $expectedLifecycleProbe = '{"Rules":[{"ID":"shamrai-deny-probe-no-action","Filter":{"Prefix":"shamrai-capability-denied/"},"Status":"Enabled"}]}'
    if ((Get-OptionValue -Name "--lifecycle-configuration") -ne $expectedLifecycleProbe) {
      [Console]::Error.WriteLine("Synthetic PutBucketLifecycleConfiguration probe did not use the shape-valid rule without an action.")
      exit 7
    }
    if ($mode -eq "allow-put-bucket-lifecycle-configuration") {
      Fail-S3Operation -Code "InvalidRequest" -Operation "PutBucketLifecycleConfiguration" -Message "At least one lifecycle action must be specified"
    }
    Deny-S3Operation -Operation "PutBucketLifecycleConfiguration"
  }
  "put-object-lock-configuration" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail "admin"
    $expectedObjectLockProbe = '{"ObjectLockEnabled":"Enabled","Rule":{"DefaultRetention":{"Mode":"COMPLIANCE"}}}'
    if (
      (Get-OptionValue -Name "--object-lock-configuration") -ne $expectedObjectLockProbe -or
      (Get-OptionValue -Name "--content-md5") -ne "AAAAAAAAAAAAAAAAAAAAAA=="
    ) {
      [Console]::Error.WriteLine("Synthetic PutObjectLockConfiguration probe was not guarded by a shape-valid incomplete retention rule and invalid Content-MD5.")
      exit 7
    }
    if ($mode -eq "allow-put-object-lock-configuration") {
      Fail-S3Operation -Code "InvalidRequest" -Operation "PutObjectLockConfiguration" -Message "Default retention requires Days or Years"
    }
    Deny-S3Operation -Operation "PutObjectLockConfiguration"
  }
  "put-bucket-versioning" {
    Write-ProbeLog -Role "writer" -Operation $operation -Detail "admin"
    if (
      (Get-OptionValue -Name "--versioning-configuration") -ne "{}" -or
      (Get-OptionValue -Name "--content-md5") -ne "AAAAAAAAAAAAAAAAAAAAAA=="
    ) {
      [Console]::Error.WriteLine("Synthetic PutBucketVersioning probe was not guarded by an empty configuration and invalid Content-MD5.")
      exit 7
    }
    if ($mode -eq "allow-put-bucket-versioning") {
      Fail-S3Operation -Code "BadDigest" -Operation "PutBucketVersioning" -Message "The Content-MD5 did not match the request body"
    }
    Deny-S3Operation -Operation "PutBucketVersioning"
  }
  default {
    [Console]::Error.WriteLine("Unexpected synthetic S3 operation: $operation")
    exit 8
  }
}
