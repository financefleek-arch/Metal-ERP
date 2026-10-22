<#
.SYNOPSIS
  One-time Android setup: makes the upload keystore and loads everything the release workflow needs
  into GitHub, so there are no secrets to copy by hand.

.DESCRIPTION
  Needs the GitHub CLI, logged in with admin rights on the repo:   winget install GitHub.cli ; gh auth login
  Does, in order:
    1. makes the upload keystore with random passwords, kept OUTSIDE the repo (-KeystoreDir)
    2. creates the GitHub Environment "android-release" (you as required reviewer, tags android-v* only)
    3. stores ANDROID_KEYSTORE_BASE64 / ANDROID_KEYSTORE_PASSWORD / ANDROID_KEY_PASSWORD in it
    4. optionally stores PLAY_SERVICE_ACCOUNT_JSON (-PlayServiceAccountJson path)
    5. writes web/public/.well-known/assetlinks.json with the upload key, so a sideloaded APK works at once
  Safe to re-run: it refuses to overwrite an existing keystore (losing it cannot be undone).
#>
param(
  [string]$KeystoreDir = (Join-Path $env:USERPROFILE ".metalerp-android"),
  [string]$PlayServiceAccountJson = ""
)
# Not "Stop": Windows PowerShell 5.1 turns any stderr line from a native tool (keytool prints its progress
# there) into a terminating error. Every native call below checks $LASTEXITCODE itself instead.
$ErrorActionPreference = "Continue"

function Find-Keytool {
  $c = Get-Command keytool -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  $candidates = @()
  if ($env:JAVA_HOME) { $candidates += Join-Path $env:JAVA_HOME "bin\keytool.exe" }
  $candidates += "C:\Program Files\Android\Android Studio\jbr\bin\keytool.exe"
  $candidates += Get-ChildItem "C:\Program Files\Java\*\bin\keytool.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
  foreach ($p in $candidates) { if (Test-Path $p) { return $p } }
  throw "keytool not found. Install a JDK (winget install EclipseAdoptium.Temurin.17.JDK) or Android Studio."
}

function New-Secret([int]$n = 32) {
  $alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
  $bytes = New-Object byte[] $n
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  -join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "GitHub CLI missing: winget install GitHub.cli ; gh auth login" }
gh auth status | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Run: gh auth login" }
$repo = gh repo view --json nameWithOwner --jq .nameWithOwner
if ($LASTEXITCODE -ne 0 -or -not $repo) { throw "Run this inside the cloned repo (gh could not find it)" }
$me = gh api user --jq ".id"
if ($LASTEXITCODE -ne 0) { throw "gh could not read your user" }
$twa = Get-Content (Join-Path $PSScriptRoot "twa-manifest.json") -Raw | ConvertFrom-Json
Write-Host "Repo: $repo   Package: $($twa.packageId)"

# 1. keystore ------------------------------------------------------------------------------------
$ks = Join-Path $KeystoreDir "android.keystore"
if (Test-Path $ks) { throw "$ks already exists. Not overwriting. Delete it deliberately if you really mean to start over." }
New-Item -ItemType Directory -Force $KeystoreDir | Out-Null
$storePass = New-Secret
$keyPass = $storePass   # PKCS12 keystores use one password for store and key
$keytool = Find-Keytool
& $keytool -genkeypair -noprompt -keystore $ks -storetype PKCS12 -alias metalerp -keyalg RSA -keysize 2048 `
  -validity 10000 -storepass $storePass -keypass $keyPass -dname "CN=Metal ERP, O=Fleek Finance, C=IN"
if ($LASTEXITCODE -ne 0) { throw "keytool failed" }
@{ keystore = $ks; alias = "metalerp"; storePassword = $storePass; keyPassword = $keyPass } |
  ConvertTo-Json | Set-Content (Join-Path $KeystoreDir "credentials.json") -Encoding utf8
$fp = (& $keytool -list -v -keystore $ks -alias metalerp -storepass $storePass |
  Select-String "SHA256:\s*(\S+)").Matches[0].Groups[1].Value

# 2. environment ---------------------------------------------------------------------------------
$policy = @{ protected_branches = $false; custom_branch_policies = $true }
$withReviewer = @{ reviewers = @(@{ type = "User"; id = [int]$me }); deployment_branch_policy = $policy } | ConvertTo-Json -Depth 5
$withReviewer | gh api -X PUT "repos/$repo/environments/android-release" --input - 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Warning "Required reviewers are not available on this plan (private repo on Free). Creating the environment without them."
  @{ deployment_branch_policy = $policy } | ConvertTo-Json -Depth 5 |
    gh api -X PUT "repos/$repo/environments/android-release" --input - | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "could not create the android-release environment" }
}
@{ name = "android-v*"; type = "tag" } | ConvertTo-Json |
  gh api -X POST "repos/$repo/environments/android-release/deployment-branch-policies" --input - 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Warning "Could not add the android-v* tag rule (it may already exist). Check Settings > Environments." }

# 3 + 4. secrets ---------------------------------------------------------------------------------
function Set-EnvSecret($name, $value) {
  $value | gh secret set $name --env android-release --repo $repo
  if ($LASTEXITCODE -ne 0) { throw "could not set $name" }
}
Set-EnvSecret ANDROID_KEYSTORE_BASE64 ([Convert]::ToBase64String([IO.File]::ReadAllBytes($ks)))
Set-EnvSecret ANDROID_KEYSTORE_PASSWORD $storePass
Set-EnvSecret ANDROID_KEY_PASSWORD $keyPass
if ($PlayServiceAccountJson) { Set-EnvSecret PLAY_SERVICE_ACCOUNT_JSON (Get-Content $PlayServiceAccountJson -Raw) }

# 5. assetlinks ----------------------------------------------------------------------------------
node (Join-Path $PSScriptRoot "assetlinks.mjs") $fp

Write-Host ""
Write-Host "Done. Upload-key SHA-256: $fp"
Write-Host "BACK UP $KeystoreDir (keystore + credentials.json) to a password manager NOW. GitHub cannot give it back."
Write-Host "Next: commit assetlinks.json, then tag android-v1.0.0 and approve the run."
