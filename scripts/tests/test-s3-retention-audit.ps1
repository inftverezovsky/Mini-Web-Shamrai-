param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$auditScript = Join-Path $repoRoot "scripts/verify-shamrai-s3-retention.ps1"
$attestationPath = Join-Path $repoRoot ".deploy/s3-retention-attestation.json"
$workflowPath = Join-Path $repoRoot ".github/workflows/restore-drill.yml"
$runbookPath = Join-Path $repoRoot "docs/backup-restore.md"
$fakeAwsScript = Join-Path $repoRoot "scripts/tests/fixtures/fake-s3-audit-aws.ps1"
$powerShellExecutable = (Get-Process -Id $PID).Path

function ConvertTo-ComparableOutputText {
  param([Parameter(Mandatory = $true)][string]$Value)
  $ansiPattern = [regex]::Escape([string][char]27) + '\[[0-?]*[ -/]*[@-~]'
  $withoutAnsi = [regex]::Replace($Value, $ansiPattern, '')
  return (($withoutAnsi -replace '\s+', ' ').Trim())
}

function Assert-Contains {
  param([Parameter(Mandatory = $true)][string]$Actual, [Parameter(Mandatory = $true)][string]$Expected)
  if (-not (ConvertTo-ComparableOutputText $Actual).Contains((ConvertTo-ComparableOutputText $Expected))) {
    throw "Expected output to contain '$Expected'. Actual output:`n$Actual"
  }
}

function Assert-NotContains {
  param([Parameter(Mandatory = $true)][string]$Actual, [Parameter(Mandatory = $true)][string]$Unexpected)
  if ((ConvertTo-ComparableOutputText $Actual).Contains((ConvertTo-ComparableOutputText $Unexpected))) {
    throw "Expected output not to contain '$Unexpected'. Actual output:`n$Actual"
  }
}

function Assert-Throws {
  param(
    [Parameter(Mandatory = $true)][scriptblock]$Action,
    [Parameter(Mandatory = $true)][string]$Expected
  )

  try {
    & $Action
  } catch {
    if (-not $_.Exception.Message.Contains($Expected)) {
      throw "Expected error containing '$Expected', got '$($_.Exception.Message)'."
    }
    return
  }
  throw "Expected an error containing '$Expected'."
}

function Invoke-ScriptCapture {
  param([Parameter(Mandatory = $true)][string[]]$Arguments)

  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    $output = & $powerShellExecutable -NoLogo -NoProfile -File $auditScript @Arguments 2>&1
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  return [pscustomobject]@{
    ExitCode = $exitCode
    Output = ($output -join "`n")
  }
}

$secretEnvironmentNames = @(
  "SHAMRAI_BACKUP_S3_ACCESS_KEY_ID",
  "SHAMRAI_BACKUP_S3_SECRET_ACCESS_KEY",
  "SHAMRAI_BACKUP_S3_SESSION_TOKEN",
  "SHAMRAI_BACKUP_S3_AUDIT_ACCESS_KEY_ID",
  "SHAMRAI_BACKUP_S3_AUDIT_SECRET_ACCESS_KEY",
  "SHAMRAI_BACKUP_S3_AUDIT_SESSION_TOKEN",
  "SHAMRAI_BACKUP_S3_WRITER_POLICY_JSON",
  "SHAMRAI_FAKE_S3_STATE_DIR",
  "SHAMRAI_FAKE_S3_LOG_PATH",
  "SHAMRAI_FAKE_S3_MODE",
  "GITHUB_ACTIONS",
  "GITHUB_REPOSITORY",
  "GITHUB_RUN_ID",
  "GITHUB_RUN_ATTEMPT",
  "GITHUB_SHA"
)
$originalEnvironment = @{}
foreach ($name in $secretEnvironmentNames) {
  $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

try {
  $sentinelSecret = "must-not-appear-in-output"
  $env:SHAMRAI_BACKUP_S3_AUDIT_ACCESS_KEY_ID = $sentinelSecret
  $env:SHAMRAI_BACKUP_S3_AUDIT_SECRET_ACCESS_KEY = $sentinelSecret
  $env:SHAMRAI_BACKUP_S3_AUDIT_SESSION_TOKEN = $sentinelSecret
  $env:SHAMRAI_BACKUP_S3_ACCESS_KEY_ID = $sentinelSecret
  $env:SHAMRAI_BACKUP_S3_SECRET_ACCESS_KEY = $sentinelSecret
  $env:SHAMRAI_BACKUP_S3_SESSION_TOKEN = $sentinelSecret
  $env:SHAMRAI_BACKUP_S3_WRITER_POLICY_JSON = $sentinelSecret

  $attestationBefore = if (Test-Path -LiteralPath $attestationPath) {
    Get-Content -Raw -LiteralPath $attestationPath
  } else {
    $null
  }
  $dryRun = Invoke-ScriptCapture -Arguments @(
    "-DryRun",
    "-Bucket", "example-backups",
    "-Prefix", "shamrai",
    "-EndpointUrl", "https://s3.example.invalid",
    "-Region", "us-east-1"
  )
  if ($dryRun.ExitCode -ne 0) {
    throw "S3 retention audit DryRun failed:`n$($dryRun.Output)"
  }
  Assert-Contains -Actual $dryRun.Output -Expected "DRY_RUN verify-shamrai-s3-retention"
  Assert-Contains -Actual $dryRun.Output -Expected "versioning_check=get-bucket-versioning"
  Assert-Contains -Actual $dryRun.Output -Expected "object_lock_check=get-object-lock-configuration"
  Assert-Contains -Actual $dryRun.Output -Expected "lifecycle_check=get-bucket-lifecycle-configuration"
  Assert-Contains -Actual $dryRun.Output -Expected "writer_identity_check=sts-get-caller-identity+iam-get-entity+iam-get-permissions-boundary"
  Assert-Contains -Actual $dryRun.Output -Expected "bucket_policy_check=get-bucket-policy+exact-writer-deny-boundary"
  Assert-Contains -Actual $dryRun.Output -Expected "writer_permissions_boundary_check=iam-get-user-or-role+get-policy+get-policy-version+exact-deny-boundary"
  Assert-Contains -Actual $dryRun.Output -Expected "required_retention_days=current:35,noncurrent:90"
  Assert-Contains -Actual $dryRun.Output -Expected "s3-retention-attestation.json"
  Assert-NotContains -Actual $dryRun.Output -Unexpected $sentinelSecret

  $attestationAfter = if (Test-Path -LiteralPath $attestationPath) {
    Get-Content -Raw -LiteralPath $attestationPath
  } else {
    $null
  }
  if ($attestationAfter -ne $attestationBefore) {
    throw "DryRun changed the S3 retention attestation."
  }

  $insecureEndpoint = Invoke-ScriptCapture -Arguments @(
    "-DryRun",
    "-Bucket", "example-backups",
    "-Prefix", "shamrai",
    "-EndpointUrl", "http://s3.example.invalid"
  )
  if ($insecureEndpoint.ExitCode -eq 0) {
    throw "S3 audit accepted an insecure endpoint."
  }
  Assert-Contains -Actual $insecureEndpoint.Output -Expected "absolute HTTPS URL"

  $unboundStsEndpoint = Invoke-ScriptCapture -Arguments @(
    "-DryRun",
    "-Bucket", "example-backups",
    "-Prefix", "shamrai",
    "-EndpointUrl", "https://s3.example.invalid",
    "-StsEndpointUrl", "https://sts.attacker.invalid"
  )
  if ($unboundStsEndpoint.ExitCode -eq 0) {
    throw "S3 audit accepted an STS endpoint from an unrelated HTTPS origin."
  }
  Assert-Contains -Actual $unboundStsEndpoint.Output -Expected "must exactly match EndpointUrl"

  $tokens = $null
  $parseErrors = $null
  $ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $auditScript,
    [ref]$tokens,
    [ref]$parseErrors
  )
  if ($parseErrors.Count -ne 0) {
    throw "S3 audit script has PowerShell parse errors: $($parseErrors.Message -join '; ')"
  }

  foreach ($functionAst in $ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
  }, $true)) {
    Invoke-Expression $functionAst.Extent.Text
  }
  $script:MinimumCurrentRetentionDays = 35
  $script:MinimumNoncurrentRetentionDays = 90

  $env:GITHUB_ACTIONS = $null
  $env:GITHUB_REPOSITORY = "ignored/local-repository"
  $env:GITHUB_RUN_ID = "999"
  $env:GITHUB_RUN_ATTEMPT = "8"
  $env:GITHUB_SHA = "ffffffffffffffffffffffffffffffffffffffff"
  $localProvenance = Get-GitHubWorkflowProvenance
  foreach ($field in @("repository", "workflow_run_id", "workflow_run_attempt", "git_sha")) {
    if (-not [string]::IsNullOrEmpty([string]$localProvenance[$field])) {
      throw "Local S3 attestation provenance field '$field' must be empty outside GitHub Actions."
    }
  }

  $env:GITHUB_ACTIONS = "true"
  $env:GITHUB_REPOSITORY = "example/shamrai"
  $env:GITHUB_RUN_ID = "123456789"
  $env:GITHUB_RUN_ATTEMPT = "2"
  $env:GITHUB_SHA = "0123456789abcdef0123456789abcdef01234567"
  $githubProvenance = Get-GitHubWorkflowProvenance
  if (
    $githubProvenance.repository -ne "example/shamrai" -or
    $githubProvenance.workflow_run_id -ne "123456789" -or
    $githubProvenance.workflow_run_attempt -ne "2" -or
    $githubProvenance.git_sha -ne "0123456789abcdef0123456789abcdef01234567"
  ) {
    throw "GitHub Actions provenance was not captured exactly from the runtime environment."
  }
  $env:GITHUB_RUN_ID = ""
  Assert-Throws -Expected "GITHUB_RUN_ID" -Action { Get-GitHubWorkflowProvenance }
  $env:GITHUB_RUN_ID = "123456789"

  $validPolicy = @{
    Version = "2012-10-17"
    Statement = @(
      @{
        Effect = "Allow"
        Action = @("s3:PutObject", "s3:GetObject")
        Resource = "arn:aws:s3:::example-backups/shamrai/*"
      },
      @{
        Effect = "Allow"
        Action = "s3:ListBucket"
        Resource = "arn:aws:s3:::example-backups"
        Condition = @{
          StringLike = @{
            "s3:prefix" = @("shamrai", "shamrai/*")
          }
        }
      }
    )
  } | ConvertTo-Json -Depth 10 -Compress
  Assert-WriterPolicy -PolicyJson $validPolicy -BucketName "example-backups" -ObjectPrefix "shamrai"

  $validWriterBoundaryPolicy = [ordered]@{
    Version = "2012-10-17"
    Statement = @(
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
        NotAction = @("s3:PutObject", "s3:GetObject", "s3:ListBucket")
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
  } | ConvertTo-Json -Depth 16 -Compress
  $boundaryResult = Assert-WriterPermissionBoundary `
    -PolicyJson $validWriterBoundaryPolicy `
    -BucketName "example-backups" `
    -ObjectPrefix "shamrai"
  if ($boundaryResult.Status -cne "passed" -or $boundaryResult.RequiredRuleCount -ne 5) {
    throw "Valid writer permissions boundary produced incorrect evidence."
  }
  $unsafeBoundary = $validWriterBoundaryPolicy | ConvertFrom-Json
  $unsafeBoundary.Statement[2].NotAction = @("s3:PutObject", "s3:GetObject", "s3:ListBucket", "sts:AssumeRole")
  Assert-Throws -Expected "deny-all-non-backup-actions" -Action {
    Assert-WriterPermissionBoundary `
      -PolicyJson ($unsafeBoundary | ConvertTo-Json -Depth 16 -Compress) `
      -BucketName "example-backups" `
      -ObjectPrefix "shamrai"
  }

  $writerPrincipalArn = "arn:aws:iam::123456789012:user/shamrai-backup-writer"
  $validBucketDenyPolicy = [ordered]@{
    Version = "2012-10-17"
    Statement = @(
      [ordered]@{
        Effect = "Deny"
        Principal = [ordered]@{ AWS = $writerPrincipalArn }
        NotAction = @("s3:PutObject", "s3:GetObject", "s3:ListBucket")
        Resource = @("arn:aws:s3:::example-backups", "arn:aws:s3:::example-backups/*")
      },
      [ordered]@{
        Effect = "Deny"
        Principal = [ordered]@{ AWS = $writerPrincipalArn }
        Action = @("s3:PutObject", "s3:GetObject")
        NotResource = "arn:aws:s3:::example-backups/shamrai/*"
      },
      [ordered]@{
        Effect = "Deny"
        Principal = [ordered]@{ AWS = $writerPrincipalArn }
        Action = "s3:ListBucket"
        Resource = "arn:aws:s3:::example-backups"
        Condition = [ordered]@{
          StringNotLike = [ordered]@{ "s3:prefix" = @("shamrai", "shamrai/*") }
        }
      },
      [ordered]@{
        Effect = "Deny"
        Principal = [ordered]@{ AWS = $writerPrincipalArn }
        Action = "s3:ListBucket"
        Resource = "arn:aws:s3:::example-backups"
        Condition = [ordered]@{ Null = [ordered]@{ "s3:prefix" = "true" } }
      }
    )
  } | ConvertTo-Json -Depth 8 -Compress
  $bucketDenyResult = Assert-ImmutableBucketDenyPolicy `
    -PolicyJson $validBucketDenyPolicy `
    -BucketName "example-backups" `
    -ObjectPrefix "shamrai" `
    -WriterPrincipalArn $writerPrincipalArn
  if ($bucketDenyResult.Status -cne "passed" -or $bucketDenyResult.RequiredRuleCount -ne 4) {
    throw "Valid immutable bucket Deny policy produced incorrect evidence."
  }

  $wrongPrincipalBucketPolicy = $validBucketDenyPolicy.Replace($writerPrincipalArn, "arn:aws:iam::123456789012:user/other")
  Assert-Throws -Expected "exact writer principal" -Action {
    Assert-ImmutableBucketDenyPolicy `
      -PolicyJson $wrongPrincipalBucketPolicy `
      -BucketName "example-backups" `
      -ObjectPrefix "shamrai" `
      -WriterPrincipalArn $writerPrincipalArn
  }

  $userPrincipal = Get-StableWriterPrincipal -CallerArn $writerPrincipalArn
  if ($userPrincipal.StableArn -cne $writerPrincipalArn -or $userPrincipal.PrincipalType -cne "iam_user") {
    throw "IAM user caller principal normalization failed."
  }
  $assumedRolePrincipal = Get-StableWriterPrincipal `
    -CallerArn "arn:aws:sts::123456789012:assumed-role/shamrai-backup/session-1"
  if (
    $assumedRolePrincipal.StableArn -cne "arn:aws:iam::123456789012:role/shamrai-backup" -or
    $assumedRolePrincipal.PrincipalType -cne "assumed_role_to_iam_role"
  ) {
    throw "Assumed-role caller principal normalization failed."
  }
  Assert-Throws -Expected "not a supported stable IAM user or role" -Action {
    Get-StableWriterPrincipal -CallerArn "arn:aws:sts::123456789012:federated-user/unsupported"
  }

  foreach ($dangerousAction in @(
    "s3:DeleteObject",
    "s3:BypassGovernanceRetention",
    "s3:PutLifecycleConfiguration",
    "s3:*"
  )) {
    $dangerousPolicy = $validPolicy | ConvertFrom-Json
    $dangerousPolicy.Statement[0].Action = @("s3:PutObject", "s3:GetObject", $dangerousAction)
    Assert-Throws -Expected "outside PutObject/GetObject/ListBucket" -Action {
      Assert-WriterPolicy `
        -PolicyJson ($dangerousPolicy | ConvertTo-Json -Depth 10 -Compress) `
        -BucketName "example-backups" `
        -ObjectPrefix "shamrai"
    }
  }

  $unscopedListPolicy = $validPolicy | ConvertFrom-Json
  $unscopedListPolicy.Statement[1].PSObject.Properties.Remove("Condition")
  Assert-Throws -Expected "prefix condition" -Action {
    Assert-WriterPolicy `
      -PolicyJson ($unscopedListPolicy | ConvertTo-Json -Depth 10 -Compress) `
      -BucketName "example-backups" `
      -ObjectPrefix "shamrai"
  }

  $resourcePolicy = $validPolicy | ConvertFrom-Json
  $resourcePolicy.Statement[0] | Add-Member -NotePropertyName "Principal" -NotePropertyValue "*"
  Assert-Throws -Expected "identity policy" -Action {
    Assert-WriterPolicy `
      -PolicyJson ($resourcePolicy | ConvertTo-Json -Depth 10 -Compress) `
      -BucketName "example-backups" `
      -ObjectPrefix "shamrai"
  }

  $narrowWriterPolicy = $validPolicy | ConvertFrom-Json
  $narrowWriterPolicy.Statement[0].Resource = "arn:aws:s3:::example-backups/shamrai/daily/one.dump.age"
  Assert-Throws -Expected "does not cover generated objects" -Action {
    Assert-WriterPolicy `
      -PolicyJson ($narrowWriterPolicy | ConvertTo-Json -Depth 10 -Compress) `
      -BucketName "example-backups" `
      -ObjectPrefix "shamrai"
  }

  $dailyWriterPolicy = $validPolicy | ConvertFrom-Json
  $dailyWriterPolicy.Statement[0].Resource = "arn:aws:s3:::example-backups/shamrai/daily/*"
  $dailyWriterPolicy.Statement[1].Condition.StringLike."s3:prefix" = "shamrai/daily/*"
  Assert-WriterPolicy `
    -PolicyJson ($dailyWriterPolicy | ConvertTo-Json -Depth 10 -Compress) `
    -BucketName "example-backups" `
    -ObjectPrefix "shamrai"

  $narrowListPolicy = $validPolicy | ConvertFrom-Json
  $narrowListPolicy.Statement[1].Condition.StringLike."s3:prefix" = "shamrai/daily/one.manifest.json"
  Assert-Throws -Expected "does not cover generated objects" -Action {
    Assert-WriterPolicy `
      -PolicyJson ($narrowListPolicy | ConvertTo-Json -Depth 10 -Compress) `
      -BucketName "example-backups" `
      -ObjectPrefix "shamrai"
  }

  $literalWildcardListPolicy = $validPolicy | ConvertFrom-Json
  $literalWildcardListPolicy.Statement[1].Condition = [pscustomobject]@{
    StringEquals = [pscustomobject]@{ "s3:prefix" = "shamrai/daily/*" }
  }
  Assert-Throws -Expected "does not cover generated objects" -Action {
    Assert-WriterPolicy `
      -PolicyJson ($literalWildcardListPolicy | ConvertTo-Json -Depth 10 -Compress) `
      -BucketName "example-backups" `
      -ObjectPrefix "shamrai"
  }

  $validObjectLock = @{
    ObjectLockConfiguration = @{
      ObjectLockEnabled = "Enabled"
      Rule = @{ DefaultRetention = @{ Mode = "COMPLIANCE"; Days = 35 } }
    }
  } | ConvertTo-Json -Depth 8 | ConvertFrom-Json
  $objectLockResult = Assert-ObjectLockConfiguration -Configuration $validObjectLock
  if ($objectLockResult.Status -ne "Enabled" -or $objectLockResult.Days -ne 35) {
    throw "Valid Object Lock configuration produced incorrect evidence."
  }

  $governanceLock = @{
    ObjectLockConfiguration = @{
      ObjectLockEnabled = "Enabled"
      Rule = @{ DefaultRetention = @{ Mode = "GOVERNANCE"; Days = 35 } }
    }
  } | ConvertTo-Json -Depth 8 | ConvertFrom-Json
  Assert-Throws -Expected "COMPLIANCE" -Action {
    Assert-ObjectLockConfiguration -Configuration $governanceLock
  }

  $validLifecycle = @{
    Rules = @(
      @{
        ID = "shamrai-retention"
        Status = "Enabled"
        Filter = @{ Prefix = "shamrai/" }
        Expiration = @{ Days = 35 }
        NoncurrentVersionExpiration = @{ NoncurrentDays = 90 }
      },
      @{
        ID = "longer-daily-retention"
        Status = "Enabled"
        Filter = @{ Prefix = "shamrai/daily/" }
        Expiration = @{ Days = 40 }
        NoncurrentVersionExpiration = @{ NoncurrentDays = 100 }
      }
    )
  } | ConvertTo-Json -Depth 10 | ConvertFrom-Json
  $lifecycleResult = Assert-LifecycleConfiguration -Configuration $validLifecycle -ObjectPrefix "shamrai"
  if ($lifecycleResult.CurrentDays -ne 35 -or $lifecycleResult.NoncurrentDays -ne 90) {
    throw "Valid lifecycle configuration produced incorrect effective retention."
  }

  $unsafeLifecycle = @{
    Rules = @(
      @{
        Status = "Enabled"
        Filter = @{ Prefix = "shamrai/" }
        Expiration = @{ Days = 35 }
        NoncurrentVersionExpiration = @{ NoncurrentDays = 90 }
      },
      @{
        Status = "Enabled"
        Filter = @{ Prefix = "shamrai/daily/" }
        Expiration = @{ Days = 34 }
      }
    )
  } | ConvertTo-Json -Depth 10 | ConvertFrom-Json
  Assert-Throws -Expected "shorter than 35 days" -Action {
    Assert-LifecycleConfiguration -Configuration $unsafeLifecycle -ObjectPrefix "shamrai"
  }

  $unsafeNoncurrentLifecycle = @{
    Rules = @(
      @{
        Status = "Enabled"
        Filter = @{ Prefix = "shamrai/" }
        Expiration = @{ Days = 35 }
        NoncurrentVersionExpiration = @{ NoncurrentDays = 89 }
      }
    )
  } | ConvertTo-Json -Depth 10 | ConvertFrom-Json
  Assert-Throws -Expected "shorter than 90 days" -Action {
    Assert-LifecycleConfiguration -Configuration $unsafeNoncurrentLifecycle -ObjectPrefix "shamrai"
  }

  $attestationExisted = Test-Path -LiteralPath $attestationPath
  $previousAttestationBytes = if ($attestationExisted) {
    [IO.File]::ReadAllBytes($attestationPath)
  } else {
    $null
  }
  $policyTempPath = [IO.Path]::GetTempFileName()
  $fakeStateDirectory = Join-Path ([IO.Path]::GetTempPath()) ("shamrai-fake-s3-{0}" -f [Guid]::NewGuid().ToString("N"))
  New-Item -ItemType Directory -Path $fakeStateDirectory | Out-Null
  $fakeLogPath = Join-Path $fakeStateDirectory "operations.log"
  try {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $attestationPath) | Out-Null
    [IO.File]::WriteAllText($attestationPath, '{"status":"stale-passed"}', (New-Object Text.UTF8Encoding($false)))
    $invalidRealAudit = Invoke-ScriptCapture -Arguments @(
      "-Bucket", "x",
      "-Prefix", "shamrai",
      "-Region", "us-east-1",
      "-WriterPolicyPath", $policyTempPath,
      "-AwsExecutable", $fakeAwsScript
    )
    if ($invalidRealAudit.ExitCode -eq 0) {
      throw "Invalid real S3 audit unexpectedly succeeded."
    }
    if (Test-Path -LiteralPath $attestationPath) {
      throw "Invalid real S3 audit left a stale passed attestation."
    }

    $env:SHAMRAI_BACKUP_S3_AUDIT_ACCESS_KEY_ID = "synthetic-audit-access-key"
    $env:SHAMRAI_BACKUP_S3_AUDIT_SECRET_ACCESS_KEY = "synthetic-audit-secret-key"
    $env:SHAMRAI_BACKUP_S3_AUDIT_SESSION_TOKEN = $null
    $env:SHAMRAI_BACKUP_S3_ACCESS_KEY_ID = "synthetic-writer-access-key"
    $env:SHAMRAI_BACKUP_S3_SECRET_ACCESS_KEY = "synthetic-writer-secret-key"
    $env:SHAMRAI_BACKUP_S3_SESSION_TOKEN = $null
    $env:SHAMRAI_BACKUP_S3_WRITER_POLICY_JSON = $null
    $env:SHAMRAI_FAKE_S3_STATE_DIR = $fakeStateDirectory
    $env:SHAMRAI_FAKE_S3_LOG_PATH = $fakeLogPath
    $env:SHAMRAI_FAKE_S3_MODE = $null
    $narrowPolicyJson = $narrowWriterPolicy | ConvertTo-Json -Depth 10 -Compress
    [IO.File]::WriteAllText($policyTempPath, $narrowPolicyJson, (New-Object Text.UTF8Encoding($false)))
    $narrowSyntheticAudit = Invoke-ScriptCapture -Arguments @(
      "-Bucket", "example-backups",
      "-Prefix", "shamrai",
      "-Region", "us-east-1",
      "-WriterPolicyPath", $policyTempPath,
      "-AwsExecutable", $fakeAwsScript
    )
    if ($narrowSyntheticAudit.ExitCode -eq 0) {
      throw "Synthetic S3 audit accepted a writer policy scoped to only one object."
    }
    Assert-Contains -Actual $narrowSyntheticAudit.Output -Expected "does not cover generated objects below shamrai/daily/*"
    if (Test-Path -LiteralPath $attestationPath) {
      throw "Failed synthetic S3 audit left a stale passed attestation."
    }

    [IO.File]::WriteAllText($policyTempPath, $validPolicy, (New-Object Text.UTF8Encoding($false)))
    $unsupportedCustomProviderAudit = Invoke-ScriptCapture -Arguments @(
      "-Bucket", "example-backups",
      "-Prefix", "shamrai",
      "-EndpointUrl", "https://s3.example.invalid",
      "-Region", "us-east-1",
      "-WriterPolicyPath", $policyTempPath,
      "-AwsExecutable", $fakeAwsScript
    )
    if ($unsupportedCustomProviderAudit.ExitCode -eq 0) {
      throw "Real S3 retention audit accepted a custom provider without authoritative IAM verification."
    }
    Assert-Contains -Actual $unsupportedCustomProviderAudit.Output -Expected "only AWS-managed default S3, STS, and IAM endpoints"
    if (Test-Path -LiteralPath $attestationPath) {
      throw "Unsupported custom-provider audit left a stale passed attestation."
    }

    $syntheticAudit = Invoke-ScriptCapture -Arguments @(
      "-Bucket", "example-backups",
      "-Prefix", "shamrai",
      "-Region", "us-east-1",
      "-WriterPolicyPath", $policyTempPath,
      "-AwsExecutable", $fakeAwsScript
    )
    if ($syntheticAudit.ExitCode -ne 0) {
      throw "Synthetic S3 retention audit failed:`n$($syntheticAudit.Output)"
    }
    Assert-NotContains -Actual $syntheticAudit.Output -Unexpected "synthetic-audit-access-key"
    Assert-NotContains -Actual $syntheticAudit.Output -Unexpected "synthetic-audit-secret-key"
    Assert-NotContains -Actual $syntheticAudit.Output -Unexpected "synthetic-writer-access-key"
    Assert-NotContains -Actual $syntheticAudit.Output -Unexpected "synthetic-writer-secret-key"

    $probeLog = Get-Content -Raw -LiteralPath $fakeLogPath
    foreach ($expectedProbeOperation in @(
      "audit`tget-bucket-versioning`t",
      "audit`tget-object-lock-configuration`t",
      "audit`tget-bucket-lifecycle-configuration`t",
      "audit`tget-bucket-policy`t",
      "audit`tiam-get-user`t",
      "audit`tiam-get-policy`t",
      "audit`tiam-get-policy-version`t",
      "writer`tsts-get-caller-identity`t",
      "writer`tput-object`tinside",
      "writer`tget-object`tinside",
      "writer`tlist-objects-v2`tinside",
      "writer`tdelete-object`tcurrent",
      "writer`tdelete-object`tversion",
      "writer`tput-object`toutside",
      "writer`tget-object`toutside",
      "writer`tlist-objects-v2`toutside",
      "writer`tput-object-retention`tadmin",
      "writer`tput-object-legal-hold`tadmin",
      "writer`tput-object-acl`tadmin",
      "writer`tput-object-tagging`tadmin",
      "writer`tdelete-object-tagging`tadmin",
      "writer`tabort-multipart-upload`tadmin"
    )) {
      Assert-Contains -Actual $probeLog -Expected $expectedProbeOperation
    }
    Assert-NotContains -Actual $probeLog -Unexpected "synthetic-audit-access-key"
    Assert-NotContains -Actual $probeLog -Unexpected "synthetic-writer-access-key"
    Assert-NotContains -Actual $probeLog -Unexpected "synthetic-writer-secret-key"

    $attestation = Get-Content -Raw -LiteralPath $attestationPath | ConvertFrom-Json
    $expectedFields = @(
      "schema_version",
      "status",
      "verified_at",
      "bucket",
      "prefix",
      "endpoint_url",
      "sts_endpoint_url",
      "region",
      "versioning_status",
      "object_lock_status",
      "default_retention_days",
      "current_expiration_days",
      "noncurrent_expiration_days",
      "bucket_deny_policy_status",
      "bucket_deny_policy_sha256",
      "bucket_deny_policy_required_rule_count",
      "writer_policy_sha256",
      "writer_access_key_id_sha256",
      "writer_identity_evidence",
      "writer_principal_type",
      "writer_caller_arn_sha256",
      "writer_stable_principal_arn_sha256",
      "writer_permissions_boundary_status",
      "writer_permissions_boundary_sha256",
      "writer_permissions_boundary_arn_sha256",
      "writer_permissions_boundary_version_id",
      "writer_permissions_boundary_required_rule_count",
      "repository",
      "workflow_run_id",
      "workflow_run_attempt",
      "git_sha",
      "writer_capability_probe",
      "writer_put_object_allowed",
      "writer_get_object_allowed",
      "writer_list_prefix_allowed",
      "writer_delete_object_denied",
      "writer_delete_object_version_denied",
      "writer_outside_prefix_put_denied",
      "writer_outside_prefix_get_denied",
      "writer_outside_prefix_list_denied",
      "writer_effective_permission_evidence",
      "writer_delete_object_version_probe_target",
      "writer_admin_probe_target",
      "writer_admin_capability_probe",
      "writer_put_object_retention_denied",
      "writer_put_object_legal_hold_denied",
      "writer_put_object_acl_denied",
      "writer_put_object_tagging_denied",
      "writer_delete_object_tagging_denied",
      "writer_abort_multipart_upload_denied",
      "writer_put_bucket_policy_denied",
      "writer_delete_bucket_policy_denied",
      "writer_delete_bucket_denied",
      "writer_put_bucket_lifecycle_configuration_denied",
      "writer_put_object_lock_configuration_denied",
      "writer_put_bucket_versioning_denied"
    )
    $actualFields = @($attestation.PSObject.Properties.Name)
    if (@(Compare-Object ($expectedFields | Sort-Object) ($actualFields | Sort-Object)).Count -ne 0) {
      throw "S3 attestation schema fields differ from the required schema."
    }
    if (
      $attestation.schema_version -ne 3 -or
      $attestation.status -ne "passed" -or
      $attestation.bucket -ne "example-backups" -or
      $attestation.prefix -ne "shamrai" -or
      $attestation.endpoint_url -ne "" -or
      $attestation.sts_endpoint_url -ne "" -or
      $attestation.region -ne "us-east-1" -or
      $attestation.versioning_status -ne "Enabled" -or
      $attestation.object_lock_status -ne "Enabled" -or
      $attestation.default_retention_days -ne 35 -or
      $attestation.current_expiration_days -ne 35 -or
      $attestation.noncurrent_expiration_days -ne 90 -or
      $attestation.bucket_deny_policy_status -ne "passed" -or
      $attestation.bucket_deny_policy_sha256 -ne (Get-Sha256Hex -Value $validBucketDenyPolicy) -or
      $attestation.bucket_deny_policy_required_rule_count -ne 4 -or
      $attestation.writer_policy_sha256 -ne (Get-Sha256Hex -Value $validPolicy) -or
      $attestation.writer_access_key_id_sha256 -ne (Get-Sha256Hex -Value "synthetic-writer-access-key") -or
      $attestation.writer_identity_evidence -ne "aws_sts_and_iam_permissions_boundary_v1" -or
      $attestation.writer_principal_type -ne "iam_user" -or
      $attestation.writer_caller_arn_sha256 -ne (Get-Sha256Hex -Value $writerPrincipalArn) -or
      $attestation.writer_stable_principal_arn_sha256 -ne (Get-Sha256Hex -Value $writerPrincipalArn) -or
      $attestation.writer_permissions_boundary_status -ne "passed" -or
      $attestation.writer_permissions_boundary_sha256 -ne (Get-Sha256Hex -Value $validWriterBoundaryPolicy) -or
      $attestation.writer_permissions_boundary_arn_sha256 -ne (Get-Sha256Hex -Value "arn:aws:iam::123456789012:policy/shamrai-backup-boundary") -or
      $attestation.writer_permissions_boundary_version_id -ne "v1" -or
      $attestation.writer_permissions_boundary_required_rule_count -ne 5 -or
      $attestation.repository -ne "example/shamrai" -or
      $attestation.workflow_run_id -ne "123456789" -or
      $attestation.workflow_run_attempt -ne "2" -or
      $attestation.git_sha -ne "0123456789abcdef0123456789abcdef01234567" -or
      $attestation.writer_capability_probe -ne "passed" -or
      $attestation.writer_put_object_allowed -ne $true -or
      $attestation.writer_get_object_allowed -ne $true -or
      $attestation.writer_list_prefix_allowed -ne $true -or
      $attestation.writer_delete_object_denied -ne $true -or
      $attestation.writer_delete_object_version_denied -ne $true -or
      $attestation.writer_outside_prefix_put_denied -ne $true -or
      $attestation.writer_outside_prefix_get_denied -ne $true -or
      $attestation.writer_outside_prefix_list_denied -ne $true -or
      $attestation.writer_effective_permission_evidence -ne "live_aws_sts_iam_boundary_bucket_policy_v3+live_unconfounded_object_probes_v3" -or
      $attestation.writer_delete_object_version_probe_target -ne "random_missing_key_and_version" -or
      $attestation.writer_admin_probe_target -ne "random_missing_object_resources+aws_iam_boundary_v4" -or
      $attestation.writer_admin_capability_probe -ne "passed" -or
      $attestation.writer_put_object_retention_denied -ne $true -or
      $attestation.writer_put_object_legal_hold_denied -ne $true -or
      $attestation.writer_put_object_acl_denied -ne $true -or
      $attestation.writer_put_object_tagging_denied -ne $true -or
      $attestation.writer_delete_object_tagging_denied -ne $true -or
      $attestation.writer_abort_multipart_upload_denied -ne $true -or
      $attestation.writer_put_bucket_policy_denied -ne $true -or
      $attestation.writer_delete_bucket_policy_denied -ne $true -or
      $attestation.writer_delete_bucket_denied -ne $true -or
      $attestation.writer_put_bucket_lifecycle_configuration_denied -ne $true -or
      $attestation.writer_put_object_lock_configuration_denied -ne $true -or
      $attestation.writer_put_bucket_versioning_denied -ne $true
    ) {
      throw "S3 attestation values do not match verified synthetic evidence."
    }
    $attestationText = Get-Content -Raw -LiteralPath $attestationPath
    Assert-NotContains -Actual $attestationText -Unexpected "synthetic-audit-access-key"
    Assert-NotContains -Actual $attestationText -Unexpected "synthetic-audit-secret-key"
    Assert-NotContains -Actual $attestationText -Unexpected "synthetic-writer-access-key"
    Assert-NotContains -Actual $attestationText -Unexpected "synthetic-writer-secret-key"
    Assert-NotContains -Actual $attestationText -Unexpected $writerPrincipalArn
    Assert-NotContains -Actual $attestationText -Unexpected '"Statement"'

    foreach ($negativeBucketPolicy in @(
      @{ Mode = "missing-bucket-policy"; Expected = "get-bucket-policy' failed" },
      @{ Mode = "conditional-bucket-deny"; Expected = "deny-all-other-actions" },
      @{ Mode = "incomplete-bucket-deny"; Expected = "deny-list-without-prefix" },
      @{ Mode = "wrong-bucket-principal"; Expected = "exact writer principal" },
      @{ Mode = "broad-notaction-exception"; Expected = "deny-all-other-actions" },
      @{ Mode = "unsupported-sts-principal"; Expected = "not a supported stable IAM user or role" },
      @{ Mode = "missing-permissions-boundary"; Expected = "must have a customer-managed permissions boundary" },
      @{ Mode = "unsafe-boundary-notaction"; Expected = "deny-all-non-backup-actions" },
      @{ Mode = "boundary-allows-other-bucket-list"; Expected = "deny-list-other-buckets" }
    )) {
      $env:SHAMRAI_FAKE_S3_MODE = $negativeBucketPolicy.Mode
      $failedBucketPolicyAudit = Invoke-ScriptCapture -Arguments @(
        "-Bucket", "example-backups",
        "-Prefix", "shamrai",
        "-Region", "us-east-1",
        "-WriterPolicyPath", $policyTempPath,
        "-AwsExecutable", $fakeAwsScript
      )
      if ($failedBucketPolicyAudit.ExitCode -eq 0) {
        throw "Synthetic S3 audit accepted unsafe live bucket policy mode '$($negativeBucketPolicy.Mode)'."
      }
      Assert-Contains -Actual $failedBucketPolicyAudit.Output -Expected $negativeBucketPolicy.Expected
      if (Test-Path -LiteralPath $attestationPath) {
        throw "Failed live bucket policy audit left a stale passed attestation."
      }
    }

    foreach ($negativeProbe in @(
      @{ Mode = "deny-put"; Expected = "Writer capability probe 'PutObject' failed" },
      @{ Mode = "allow-delete"; Expected = "DeleteObject was unexpectedly allowed" },
      @{ Mode = "allow-delete-version"; Expected = "DeleteObjectVersion did not return a recognizable authorization denial" },
      @{ Mode = "transient-delete-error"; Expected = "did not return a recognizable authorization denial" },
      @{ Mode = "allow-outside-put"; Expected = "outside-prefix PutObject was unexpectedly allowed" },
      @{ Mode = "allow-outside-get"; Expected = "outside-prefix GetObject did not return a recognizable authorization denial" },
      @{ Mode = "allow-outside-list"; Expected = "outside-prefix ListBucket was unexpectedly allowed" },
      @{ Mode = "allow-put-object-retention"; Expected = "PutObjectRetention did not return a recognizable authorization denial" },
      @{ Mode = "allow-put-object-legal-hold"; Expected = "PutObjectLegalHold did not return a recognizable authorization denial" },
      @{ Mode = "allow-put-object-acl"; Expected = "PutObjectAcl did not return a recognizable authorization denial" },
      @{ Mode = "allow-put-object-tagging"; Expected = "PutObjectTagging did not return a recognizable authorization denial" },
      @{ Mode = "allow-delete-object-tagging"; Expected = "DeleteObjectTagging was unexpectedly allowed" },
      @{ Mode = "allow-abort-multipart-upload"; Expected = "AbortMultipartUpload did not return a recognizable authorization denial" }
    )) {
      $env:SHAMRAI_FAKE_S3_MODE = $negativeProbe.Mode
      $failedProbe = Invoke-ScriptCapture -Arguments @(
        "-Bucket", "example-backups",
        "-Prefix", "shamrai",
        "-Region", "us-east-1",
        "-WriterPolicyPath", $policyTempPath,
        "-AwsExecutable", $fakeAwsScript
      )
      if ($failedProbe.ExitCode -eq 0) {
        throw "Synthetic S3 audit accepted unsafe capability mode '$($negativeProbe.Mode)'."
      }
      Assert-Contains -Actual $failedProbe.Output -Expected $negativeProbe.Expected
      Assert-NotContains -Actual $failedProbe.Output -Unexpected "synthetic-writer-access-key"
      Assert-NotContains -Actual $failedProbe.Output -Unexpected "synthetic-writer-secret-key"
      if (Test-Path -LiteralPath $attestationPath) {
        throw "Failed writer capability probe left a stale passed attestation."
      }
    }
  } finally {
    Remove-Item -Force -LiteralPath $policyTempPath -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force -LiteralPath $fakeStateDirectory -ErrorAction SilentlyContinue
    if ($attestationExisted) {
      [IO.File]::WriteAllBytes($attestationPath, $previousAttestationBytes)
    } else {
      Remove-Item -Force -LiteralPath $attestationPath -ErrorAction SilentlyContinue
    }
  }

  $source = Get-Content -Raw -LiteralPath $auditScript
  foreach ($field in @(
    "schema_version",
    "status",
    "verified_at",
    "bucket",
    "prefix",
    "endpoint_url",
    "sts_endpoint_url",
    "region",
    "versioning_status",
    "object_lock_status",
    "default_retention_days",
    "current_expiration_days",
    "noncurrent_expiration_days",
    "bucket_deny_policy_status",
    "bucket_deny_policy_sha256",
    "bucket_deny_policy_required_rule_count",
    "writer_policy_sha256",
    "writer_access_key_id_sha256",
    "writer_identity_evidence",
    "writer_principal_type",
    "writer_caller_arn_sha256",
    "writer_stable_principal_arn_sha256",
    "writer_permissions_boundary_status",
    "writer_permissions_boundary_sha256",
    "writer_permissions_boundary_arn_sha256",
    "writer_permissions_boundary_version_id",
    "writer_permissions_boundary_required_rule_count",
    "repository",
    "workflow_run_id",
    "workflow_run_attempt",
    "git_sha",
    "writer_capability_probe",
    "writer_put_object_allowed",
    "writer_get_object_allowed",
    "writer_list_prefix_allowed",
    "writer_delete_object_denied",
    "writer_delete_object_version_denied",
    "writer_outside_prefix_put_denied",
    "writer_outside_prefix_get_denied",
    "writer_outside_prefix_list_denied",
    "writer_effective_permission_evidence",
    "writer_delete_object_version_probe_target",
    "writer_admin_probe_target",
    "writer_admin_capability_probe",
    "writer_put_object_retention_denied",
    "writer_put_object_legal_hold_denied",
    "writer_put_object_acl_denied",
    "writer_put_object_tagging_denied",
    "writer_delete_object_tagging_denied",
    "writer_abort_multipart_upload_denied",
    "writer_put_bucket_policy_denied",
    "writer_delete_bucket_policy_denied",
    "writer_delete_bucket_denied",
    "writer_put_bucket_lifecycle_configuration_denied",
    "writer_put_object_lock_configuration_denied",
    "writer_put_bucket_versioning_denied"
  )) {
    Assert-Contains -Actual $source -Expected "$field ="
  }
  Assert-Contains -Actual $source -Expected 'Move-Item -Force -LiteralPath $temporaryPath'
  Assert-Contains -Actual $source -Expected 'SHAMRAI_BACKUP_S3_WRITER_POLICY_JSON'
  Assert-Contains -Actual $source -Expected 'SHAMRAI_BACKUP_S3_ACCESS_KEY_ID'
  Assert-Contains -Actual $source -Expected 'SHAMRAI_BACKUP_S3_SECRET_ACCESS_KEY'
  Assert-Contains -Actual $source -Expected 'SHAMRAI_BACKUP_S3_AUDIT_ACCESS_KEY_ID'
  Assert-Contains -Actual $source -Expected 'SHAMRAI_BACKUP_S3_AUDIT_SECRET_ACCESS_KEY'
  Assert-Contains -Actual $source -Expected 'GITHUB_REPOSITORY'
  Assert-Contains -Actual $source -Expected 'GITHUB_RUN_ID'
  Assert-Contains -Actual $source -Expected 'GITHUB_RUN_ATTEMPT'
  Assert-Contains -Actual $source -Expected 'GITHUB_SHA'
  Assert-Contains -Actual $source -Expected 'put-object'
  Assert-Contains -Actual $source -Expected 'get-object'
  Assert-Contains -Actual $source -Expected 'list-objects-v2'
  Assert-Contains -Actual $source -Expected 'delete-object'
  Assert-Contains -Actual $source -Expected 'put-object-retention'
  Assert-Contains -Actual $source -Expected 'put-object-legal-hold'
  Assert-Contains -Actual $source -Expected 'put-object-acl'
  Assert-Contains -Actual $source -Expected 'put-object-tagging'
  Assert-Contains -Actual $source -Expected 'delete-object-tagging'
  Assert-Contains -Actual $source -Expected 'abort-multipart-upload'
  Assert-Contains -Actual $source -Expected '-Operation "get-bucket-policy"'
  Assert-Contains -Actual $source -Expected 'Invoke-AwsStsCallerIdentity'
  Assert-Contains -Actual $source -Expected 'Get-LiveWriterPermissionBoundaryEvidence'
  Assert-Contains -Actual $source -Expected 'Assert-WriterPermissionBoundary'
  Assert-Contains -Actual $source -Expected 'Invoke-AwsIamJson'
  Assert-Contains -Actual $source -Expected 'NotAction'
  Assert-Contains -Actual $source -Expected 'NotResource'
  Assert-Contains -Actual $source -Expected 'StringNotLike'
  Assert-Contains -Actual $source -Expected 'Get-StableWriterPrincipal'
  Assert-NotContains -Actual $source -Unexpected '-Operation "put-bucket-lifecycle-configuration"'
  Assert-NotContains -Actual $source -Unexpected '-Operation "put-object-lock-configuration"'
  Assert-NotContains -Actual $source -Unexpected '-Operation "put-bucket-versioning"'
  Assert-Contains -Actual $source -Expected 'writer_effective_permission_evidence = "live_aws_sts_iam_boundary_bucket_policy_v3+live_unconfounded_object_probes_v3"'
  Assert-Contains -Actual $source -Expected 'writer_delete_object_version_probe_target = "random_missing_key_and_version"'
  Assert-Contains -Actual $source -Expected 'writer_admin_probe_target = "random_missing_object_resources+aws_iam_boundary_v4"'
  Assert-NotContains -Actual $source -Unexpected 'Write-Host $writerPolicyJson'
  Assert-NotContains -Actual $source -Unexpected 'Write-Host $auditSecretKey'
  Assert-NotContains -Actual $source -Unexpected 'Write-Host $writerAccessKey'
  Assert-NotContains -Actual $source -Unexpected 'Write-Host $writerSecretKey'
  Assert-NotContains -Actual $source -Unexpected 'writer_admin_residual_gaps'

  $workflowSource = Get-Content -Raw -LiteralPath $workflowPath
  Assert-Contains -Actual $workflowSource -Expected 'SHAMRAI_BACKUP_S3_AUDIT_ACCESS_KEY_ID: ${{ secrets.SHAMRAI_BACKUP_S3_AUDIT_ACCESS_KEY_ID }}'
  Assert-Contains -Actual $workflowSource -Expected 'SHAMRAI_BACKUP_S3_AUDIT_SECRET_ACCESS_KEY: ${{ secrets.SHAMRAI_BACKUP_S3_AUDIT_SECRET_ACCESS_KEY }}'
  Assert-Contains -Actual $workflowSource -Expected 'SHAMRAI_BACKUP_S3_AUDIT_SESSION_TOKEN: ${{ secrets.SHAMRAI_BACKUP_S3_AUDIT_SESSION_TOKEN }}'
  Assert-Contains -Actual $workflowSource -Expected 'SHAMRAI_BACKUP_S3_ACCESS_KEY_ID: ${{ secrets.SHAMRAI_BACKUP_S3_ACCESS_KEY_ID }}'
  Assert-Contains -Actual $workflowSource -Expected 'SHAMRAI_BACKUP_S3_SECRET_ACCESS_KEY: ${{ secrets.SHAMRAI_BACKUP_S3_SECRET_ACCESS_KEY }}'
  Assert-Contains -Actual $workflowSource -Expected 'SHAMRAI_BACKUP_S3_SESSION_TOKEN: ${{ secrets.SHAMRAI_BACKUP_S3_SESSION_TOKEN }}'
  Assert-Contains -Actual $workflowSource -Expected 'SHAMRAI_BACKUP_S3_STS_ENDPOINT_URL: ${{ vars.SHAMRAI_BACKUP_S3_STS_ENDPOINT_URL }}'
  Assert-Contains -Actual $workflowSource -Expected 'WRITER_POLICY_JSON: ${{ secrets.SHAMRAI_BACKUP_S3_WRITER_POLICY_JSON }}'
  Assert-Contains -Actual $workflowSource -Expected 'printf ''%s'' "$WRITER_POLICY_JSON"'
  Assert-NotContains -Actual $workflowSource -Unexpected 'printf ''%s'' "${{ secrets.SHAMRAI_BACKUP_S3_WRITER_POLICY_JSON }}"'
  Assert-Contains -Actual $workflowSource -Expected 'actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02'
  Assert-Contains -Actual $workflowSource -Expected 'path: .deploy/s3-retention-attestation.json'
  $auditStepIndex = $workflowSource.IndexOf("Verify S3 immutability and writer scope")
  $restoreStepIndex = $workflowSource.IndexOf("Run restore drill from off-host S3")
  if ($auditStepIndex -lt 0 -or $restoreStepIndex -le $auditStepIndex) {
    throw "S3 retention verification must run before the off-host restore drill."
  }

  $runbookSource = Get-Content -Raw -LiteralPath $runbookPath
  foreach ($githubSecretName in @(
    "SHAMRAI_BACKUP_S3_AUDIT_ACCESS_KEY_ID",
    "SHAMRAI_BACKUP_S3_AUDIT_SECRET_ACCESS_KEY",
    "SHAMRAI_BACKUP_S3_AUDIT_SESSION_TOKEN",
    "SHAMRAI_BACKUP_S3_ACCESS_KEY_ID",
    "SHAMRAI_BACKUP_S3_SECRET_ACCESS_KEY",
    "SHAMRAI_BACKUP_S3_SESSION_TOKEN",
    "SHAMRAI_BACKUP_S3_WRITER_POLICY_JSON"
  )) {
    Assert-Contains -Actual $runbookSource -Expected $githubSecretName
  }
  Assert-Contains -Actual $runbookSource -Expected "Settings → Secrets and variables → Actions"
  Assert-Contains -Actual $runbookSource -Expected ".deploy/s3-retention-attestation.json"
  Assert-Contains -Actual $runbookSource -Expected "writer_access_key_id_sha256"
  Assert-Contains -Actual $runbookSource -Expected "writer_effective_permission_evidence"
  Assert-Contains -Actual $runbookSource -Expected "random missing key and version"
  Assert-Contains -Actual $runbookSource -Expected "workflow_run_id"
  Assert-Contains -Actual $runbookSource -Expected "workflow_run_attempt"
  Assert-Contains -Actual $runbookSource -Expected "immutable artifact"
  Assert-Contains -Actual $runbookSource -Expected "DeleteObjectVersion"
  Assert-Contains -Actual $runbookSource -Expected "outside-prefix"
  Assert-Contains -Actual $runbookSource -Expected "PutObjectRetention"
  Assert-Contains -Actual $runbookSource -Expected "GetBucketPolicy"
  Assert-Contains -Actual $runbookSource -Expected "GetCallerIdentity"
  Assert-Contains -Actual $runbookSource -Expected "NotAction"
  Assert-Contains -Actual $runbookSource -Expected "NotResource"
  Assert-Contains -Actual $runbookSource -Expected "live_aws_sts_iam_boundary_bucket_policy_v3"
  Assert-Contains -Actual $runbookSource -Expected "writer_permissions_boundary_status"
  Assert-Contains -Actual $runbookSource -Expected "iam:GetPolicyVersion"
  Assert-Contains -Actual $runbookSource -Expected "SHAMRAI_BACKUP_S3_STS_ENDPOINT_URL"
  Assert-NotContains -Actual $runbookSource -Unexpected "writer_admin_residual_gaps"

  Write-Host "s3_retention_audit_tests_ok"
} finally {
  foreach ($name in $secretEnvironmentNames) {
    [Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], "Process")
  }
}
