Set-StrictMode -Version Latest

function Get-ManifestJsonPropertyValue {
  param(
    [AllowNull()][object]$Object,
    [Parameter(Mandatory = $true)][string]$Name,
    [AllowNull()][object]$DefaultValue = $null
  )

  if ($null -eq $Object) {
    return $DefaultValue
  }
  $property = $Object.PSObject.Properties[$Name]
  if ($null -eq $property) {
    return $DefaultValue
  }
  return $property.Value
}

function ConvertFrom-ShamraiEd25519PublicKeyBase64 {
  param([Parameter(Mandatory = $true)][string]$Value)

  if (
    [string]::IsNullOrWhiteSpace($Value) -or
    $Value -cne $Value.Trim() -or
    $Value -notmatch '^[A-Za-z0-9+/]+={0,2}$'
  ) {
    throw "Manifest signing public key must be canonical base64-encoded Ed25519 SPKI DER."
  }

  try {
    [byte[]]$bytes = [Convert]::FromBase64String($Value)
  } catch {
    throw "Manifest signing public key is not valid base64."
  }

  if ([Convert]::ToBase64String($bytes) -cne $Value) {
    throw "Manifest signing public key must use canonical base64 encoding."
  }

  # RFC 8410 SubjectPublicKeyInfo for Ed25519 is exactly this 12-byte prefix
  # followed by the 32-byte raw public key. Parameters must be absent.
  [byte[]]$expectedPrefix = 0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x03, 0x21, 0x00
  if ($bytes.Length -ne 44) {
    throw "Manifest signing public key must be a 44-byte Ed25519 SPKI DER value."
  }
  for ($index = 0; $index -lt $expectedPrefix.Length; $index++) {
    if ($bytes[$index] -ne $expectedPrefix[$index]) {
      throw "Manifest signing public key is not an Ed25519 SPKI DER value."
    }
  }

  return ,$bytes
}

function Assert-ShamraiRegularFile {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Description
  )

  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "$Description was not found: $Path"
  }
  $item = Get-Item -Force -LiteralPath $Path
  if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "$Description must not be a symbolic link or reparse point: $Path"
  }
  if ($item.Length -lt 1) {
    throw "$Description is empty: $Path"
  }
  return $item
}

function Assert-ShamraiSignedBackupManifest {
  param(
    [Parameter(Mandatory = $true)][string]$ManifestPath,
    [Parameter(Mandatory = $true)][string]$SignaturePath,
    [Parameter(Mandatory = $true)][string]$PublicKeyDerPath,
    [Parameter(Mandatory = $true)][string]$OpenSslPath,
    [Parameter(Mandatory = $true)][string]$ExpectedManifestFileName,
    [Parameter(Mandatory = $true)][string]$ExpectedSignatureFileName,
    [Parameter(Mandatory = $true)][string]$ExpectedEncryptedFileName,
    [string]$ExpectedS3Bucket = "",
    [string]$ExpectedS3Prefix = "",
    [string]$ExpectedArtifactKey = "",
    [string]$ExpectedManifestKey = "",
    [string]$ExpectedSignatureKey = ""
  )

  Assert-ShamraiRegularFile -Path $ManifestPath -Description "Backup manifest" | Out-Null
  $signatureItem = Assert-ShamraiRegularFile -Path $SignaturePath -Description "Backup manifest signature"
  Assert-ShamraiRegularFile -Path $PublicKeyDerPath -Description "Manifest signing public key" | Out-Null
  if ($signatureItem.Length -ne 64) {
    throw "Backup manifest signature must be exactly 64 bytes for Ed25519."
  }

  # Verify the exact manifest bytes before parsing or trusting any field.
  & $OpenSslPath pkeyutl -verify -rawin -pubin -keyform DER `
    -inkey $PublicKeyDerPath -in $ManifestPath -sigfile $SignaturePath 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "Backup manifest Ed25519 signature verification failed."
  }

  try {
    $manifest = Get-Content -Raw -LiteralPath $ManifestPath | ConvertFrom-Json -ErrorAction Stop
  } catch {
    throw "Signed backup manifest is not valid JSON."
  }

  $schemaVersion = 0
  [int]::TryParse(
    [string](Get-ManifestJsonPropertyValue -Object $manifest -Name "schema_version" -DefaultValue "0"),
    [ref]$schemaVersion
  ) | Out-Null
  if ($schemaVersion -lt 3) {
    throw "Signed restore requires backup manifest schema v3 or newer."
  }

  $createdAtRaw = [string](Get-ManifestJsonPropertyValue -Object $manifest -Name "created_at" -DefaultValue "")
  $createdAt = [DateTimeOffset]::MinValue
  if (-not [DateTimeOffset]::TryParseExact(
    $createdAtRaw,
    "yyyyMMdd'T'HHmmss'Z'",
    [Globalization.CultureInfo]::InvariantCulture,
    [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal,
    [ref]$createdAt
  )) {
    throw "Signed backup manifest created_at is invalid."
  }

  $baseName = "shamrai-db.$createdAtRaw"
  if (
    [string](Get-ManifestJsonPropertyValue -Object $manifest -Name "manifest_type" -DefaultValue "") -cne "shamrai_postgres_backup" -or
    [string](Get-ManifestJsonPropertyValue -Object $manifest -Name "backup_id" -DefaultValue "") -cne $baseName
  ) {
    throw "Signed backup manifest identity is not bound to its timestamp."
  }
  $expectedNames = [ordered]@{
    manifest = "$baseName.manifest.json"
    signature = "$baseName.manifest.sig"
    encrypted = "$baseName.dump.age"
  }
  foreach ($candidateName in @($ExpectedManifestFileName, $ExpectedSignatureFileName, $ExpectedEncryptedFileName)) {
    if ([string]::IsNullOrWhiteSpace($candidateName) -or $candidateName -match '[\\/]') {
      throw "Selected backup filenames must be leaf names without path separators."
    }
  }
  if (
    $ExpectedManifestFileName -cne $expectedNames.manifest -or
    $ExpectedSignatureFileName -cne $expectedNames.signature -or
    $ExpectedEncryptedFileName -cne $expectedNames.encrypted
  ) {
    throw "Signed backup timestamp is not bound to the selected artifact filenames."
  }

  $artifact = Get-ManifestJsonPropertyValue -Object $manifest -Name "artifact"
  if ($null -eq $artifact) {
    throw "Signed backup manifest is missing artifact metadata."
  }
  if (
    [string](Get-ManifestJsonPropertyValue -Object $artifact -Name "base_name" -DefaultValue "") -cne $baseName -or
    [string](Get-ManifestJsonPropertyValue -Object $artifact -Name "encrypted_file" -DefaultValue "") -cne $expectedNames.encrypted
  ) {
    throw "Signed backup manifest artifact metadata is not bound to its timestamp and filename."
  }

  $signatureMetadata = Get-ManifestJsonPropertyValue -Object $manifest -Name "signature"
  $publicKeyHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PublicKeyDerPath).Hash.ToLowerInvariant()
  if (
    $null -eq $signatureMetadata -or
    [string](Get-ManifestJsonPropertyValue -Object $signatureMetadata -Name "algorithm" -DefaultValue "") -cne "Ed25519" -or
    [string](Get-ManifestJsonPropertyValue -Object $signatureMetadata -Name "public_key_sha256" -DefaultValue "") -cne $publicKeyHash
  ) {
    throw "Signed backup manifest does not identify the configured Ed25519 public key."
  }

  if (-not [string]::IsNullOrWhiteSpace($ExpectedS3Bucket)) {
    if (
      [string]::IsNullOrWhiteSpace($ExpectedArtifactKey) -or
      [string]::IsNullOrWhiteSpace($ExpectedManifestKey) -or
      [string]::IsNullOrWhiteSpace($ExpectedSignatureKey)
    ) {
      throw "All selected S3 keys are required for signed storage binding."
    }
    $storage = Get-ManifestJsonPropertyValue -Object $manifest -Name "storage"
    if (
      $null -eq $storage -or
      [string](Get-ManifestJsonPropertyValue -Object $storage -Name "bucket" -DefaultValue "") -cne $ExpectedS3Bucket -or
      [string](Get-ManifestJsonPropertyValue -Object $storage -Name "prefix" -DefaultValue "") -cne $ExpectedS3Prefix -or
      [string](Get-ManifestJsonPropertyValue -Object $storage -Name "artifact_key" -DefaultValue "") -cne $ExpectedArtifactKey -or
      [string](Get-ManifestJsonPropertyValue -Object $storage -Name "manifest_key" -DefaultValue "") -cne $ExpectedManifestKey -or
      [string](Get-ManifestJsonPropertyValue -Object $storage -Name "signature_key" -DefaultValue "") -cne $ExpectedSignatureKey
    ) {
      throw "Signed backup storage coordinates do not match the selected S3 objects."
    }
  }

  return [pscustomobject]@{
    Manifest = $manifest
    CreatedAt = $createdAt
    BaseName = $baseName
    ManifestSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $ManifestPath).Hash.ToLowerInvariant()
    PublicKeySha256 = $publicKeyHash
  }
}

Export-ModuleMember -Function @(
  "ConvertFrom-ShamraiEd25519PublicKeyBase64",
  "Assert-ShamraiSignedBackupManifest"
)
