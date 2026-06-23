param(
  [int]$Port = 53682,
  [int]$UnitStakeRub = 10000,
  [switch]$ClientSecretFromStdin
)

$ErrorActionPreference = "Stop"

function ConvertFrom-SecureStringPlainText {
  param([Security.SecureString]$SecureValue)
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureValue)
  try {
    return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  }
}

function ConvertTo-UrlEncoded {
  param([string]$Value)
  return [System.Uri]::EscapeDataString($Value)
}

function Read-QueryParameter {
  param(
    [string]$Url,
    [string]$Name
  )
  $query = ([Uri]$Url).Query.TrimStart("?")
  foreach ($part in ($query -split "&")) {
    if ([string]::IsNullOrWhiteSpace($part)) { continue }
    $pieces = $part -split "=", 2
    $key = [Uri]::UnescapeDataString($pieces[0])
    if ($key -eq $Name) {
      $value = if ($pieces.Count -gt 1) { $pieces[1] } else { "" }
      return [Uri]::UnescapeDataString($value.Replace("+", " "))
    }
  }
  return $null
}

$clientId = $env:SHAMRAI_GOOGLE_OAUTH_CLIENT_ID
if ([string]::IsNullOrWhiteSpace($clientId)) {
  $clientId = Read-Host "Google OAuth client ID"
}

$clientSecret = $env:SHAMRAI_GOOGLE_OAUTH_CLIENT_SECRET
if ([string]::IsNullOrWhiteSpace($clientSecret)) {
  if ($ClientSecretFromStdin) {
    $clientSecret = [Console]::In.ReadLine()
  } else {
    $clientSecret = ConvertFrom-SecureStringPlainText (Read-Host "Google OAuth client secret" -AsSecureString)
  }
}

$driveFolderId = $env:SHAMRAI_GOOGLE_DRIVE_STATS_FOLDER_ID
if ([string]::IsNullOrWhiteSpace($driveFolderId)) {
  $driveFolderId = Read-Host "Google Drive target folder ID"
}

if ([string]::IsNullOrWhiteSpace($clientId)) {
  throw "Google OAuth client ID is required."
}
if ([string]::IsNullOrWhiteSpace($clientSecret)) {
  throw "Google OAuth client secret is required."
}
if ([string]::IsNullOrWhiteSpace($driveFolderId)) {
  throw "Google Drive target folder ID is required."
}

$redirectUri = "http://127.0.0.1:$Port/"
$listener = [System.Net.HttpListener]::new()
$listenerStarted = $false
foreach ($candidatePort in @($Port, 53683, 53684, 53685, 53686, 53687, 53688)) {
  $candidateRedirectUri = "http://127.0.0.1:$candidatePort/"
  try {
    $listener.Prefixes.Clear()
    $listener.Prefixes.Add($candidateRedirectUri)
    $listener.Start()
    $redirectUri = $candidateRedirectUri
    $listenerStarted = $true
    break
  } catch {
    if ($listener.IsListening) {
      $listener.Stop()
    }
  }
}

$scope = "https://www.googleapis.com/auth/drive"
$state = [Guid]::NewGuid().ToString("N")
$authParams = [ordered]@{
  client_id = $clientId
  redirect_uri = $redirectUri
  response_type = "code"
  scope = $scope
  access_type = "offline"
  prompt = "consent"
  state = $state
}
$authQuery = ($authParams.GetEnumerator() | ForEach-Object {
  "$(ConvertTo-UrlEncoded $_.Key)=$(ConvertTo-UrlEncoded $_.Value)"
}) -join "&"
$authUrl = "https://accounts.google.com/o/oauth2/v2/auth?$authQuery"

try {
  Write-Host "Opening Google OAuth consent in your browser..."
  Write-Host "OAuth redirect URI: $redirectUri"
  Start-Process $authUrl

  if ($listenerStarted) {
    $context = $listener.GetContext()
    $request = $context.Request
    $response = $context.Response
    $code = $request.QueryString["code"]
    $returnedState = $request.QueryString["state"]
    $errorValue = $request.QueryString["error"]

    $responseText = if ($code -and $returnedState -eq $state) {
      "Google OAuth code received. You can close this tab and return to PowerShell."
    } else {
      "Google OAuth did not return a valid code. Return to PowerShell for details."
    }
    $buffer = [Text.Encoding]::UTF8.GetBytes($responseText)
    $response.ContentType = "text/plain; charset=utf-8"
    $response.ContentLength64 = $buffer.Length
    $response.OutputStream.Write($buffer, 0, $buffer.Length)
    $response.OutputStream.Close()
  } else {
    Write-Host "Local callback listener was not available, switching to manual code capture."
    Write-Host "After Google redirects to 127.0.0.1 and the page fails to load, copy the FULL browser address and paste it here."
    $redirectedUrl = Read-Host "Full redirected URL"
    $code = Read-QueryParameter -Url $redirectedUrl -Name "code"
    $returnedState = Read-QueryParameter -Url $redirectedUrl -Name "state"
    $errorValue = Read-QueryParameter -Url $redirectedUrl -Name "error"
  }

  if ($errorValue) {
    throw "Google OAuth returned error: $errorValue"
  }
  if (-not $code -or $returnedState -ne $state) {
    throw "Google OAuth did not return a valid authorization code."
  }
} finally {
  if ($listener.IsListening) {
    $listener.Stop()
  }
  $listener.Close()
}

$tokenResponse = Invoke-RestMethod `
  -Method Post `
  -Uri "https://oauth2.googleapis.com/token" `
  -ContentType "application/x-www-form-urlencoded" `
  -Body @{
    code = $code
    client_id = $clientId
    client_secret = $clientSecret
    redirect_uri = $redirectUri
    grant_type = "authorization_code"
  }

$refreshToken = [string]$tokenResponse.refresh_token
if ([string]::IsNullOrWhiteSpace($refreshToken)) {
  throw "Google did not return a refresh token. Re-run and make sure prompt=consent is shown, or revoke prior app access and try again."
}

$previous = @{
  SHAMRAI_GOOGLE_DRIVE_STATS_FOLDER_ID = $env:SHAMRAI_GOOGLE_DRIVE_STATS_FOLDER_ID
  SHAMRAI_GOOGLE_OAUTH_CLIENT_ID = $env:SHAMRAI_GOOGLE_OAUTH_CLIENT_ID
  SHAMRAI_GOOGLE_OAUTH_CLIENT_SECRET = $env:SHAMRAI_GOOGLE_OAUTH_CLIENT_SECRET
  SHAMRAI_GOOGLE_OAUTH_REFRESH_TOKEN = $env:SHAMRAI_GOOGLE_OAUTH_REFRESH_TOKEN
  SHAMRAI_GOOGLE_OAUTH_TOKEN_URI = $env:SHAMRAI_GOOGLE_OAUTH_TOKEN_URI
}

try {
  $env:SHAMRAI_GOOGLE_DRIVE_STATS_FOLDER_ID = $driveFolderId
  $env:SHAMRAI_GOOGLE_OAUTH_CLIENT_ID = $clientId
  $env:SHAMRAI_GOOGLE_OAUTH_CLIENT_SECRET = $clientSecret
  $env:SHAMRAI_GOOGLE_OAUTH_REFRESH_TOKEN = $refreshToken
  $env:SHAMRAI_GOOGLE_OAUTH_TOKEN_URI = "https://oauth2.googleapis.com/token"

  powershell -ExecutionPolicy Bypass -File ".\scripts\configure-google-drive-oauth-stats.ps1" -UnitStakeRub $UnitStakeRub
} finally {
  foreach ($key in $previous.Keys) {
    if ($null -eq $previous[$key]) {
      Remove-Item -Path "Env:$key" -ErrorAction SilentlyContinue
    } else {
      Set-Item -Path "Env:$key" -Value $previous[$key]
    }
  }
}

Write-Host "Google Drive OAuth setup completed."
