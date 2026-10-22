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
$cred = Join-Path $KeystoreDir "credentials.json"
$keytool = Find-Keytool
if (Test-Path $ks) {
  # A re-run after a failure further down: reuse the keystore, never replace it.
  if (-not (Test-Path $cred)) { throw "$ks exists but $cred does not, so its passwords are unknown. Not overwriting; sort that out by hand." }
  $saved = Get-Content $cred -Raw | ConvertFrom-Json
  $storePass = $saved.storePassword
  $keyPass = $saved.keyPassword
  Write-Host "Reusing the existing keystore in $KeystoreDir"
} else {
  New-Item -ItemType Directory -Force $KeystoreDir | Out-Null
  $storePass = New-Secret
  $keyPass = $storePass   # PKCS12 keystores use one password for store and key
  & $keytool -genkeypair -noprompt -keystore $ks -storetype PKCS12 -alias metalerp -keyalg RSA -keysize 2048 `
    -validity 10000 -storepass $storePass -keypass $keyPass -dname "CN=Metal ERP, O=Fleek Finance, C=IN"
  if ($LASTEXITCODE -ne 0) { throw "keytool failed" }
  @{ keystore = $ks; alias = "metalerp"; storePassword = $storePass; keyPassword = $keyPass } |
    ConvertTo-Json | Set-Content $cred -Encoding utf8
}
$fp = (& $keytool -list -v -keystore $ks -alias metalerp -storepass $storePass |
  Select-String "SHA256:\s*(\S+)").Matches[0].Groups[1].Value

# 2. environment ---------------------------------------------------------------------------------
# Request bodies go through a UTF-8 (no BOM) FILE and `gh api --input <file>`. Piping the JSON in
# (`$json | gh api --input -`) fails in Windows PowerShell 5.1 with "Problems parsing JSON (HTTP 400)".
function Invoke-GhJson([string]$method, [string]$path, $body) {
  $f = Join-Path ([IO.Path]::GetTempPath()) ("gh-body-" + [guid]::NewGuid().ToString("N") + ".json")
  [IO.File]::WriteAllText($f, ($body | ConvertTo-Json -Depth 6), (New-Object System.Text.UTF8Encoding($false)))
  try { gh api -X $method $path --input $f 2>$null | Out-Null; return $LASTEXITCODE }
  finally { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
}
$policy = @{ protected_branches = $false; custom_branch_policies = $true }
$envPath = "repos/$repo/environments/android-release"
$rc = Invoke-GhJson PUT $envPath @{ reviewers = @(@{ type = "User"; id = [int]$me }); deployment_branch_policy = $policy }
if ($rc -ne 0) {
  Write-Warning "Could not set you as a required reviewer (plan limitation?). Creating the environment without one; add it under Settings > Environments."
  $rc = Invoke-GhJson PUT $envPath @{ deployment_branch_policy = $policy }
  if ($rc -ne 0) { throw "could not create the android-release environment" }
}
$rc = Invoke-GhJson POST "$envPath/deployment-branch-policies" @{ name = "android-v*"; type = "tag" }
if ($rc -ne 0) { Write-Warning "Could not add the android-v* tag rule (it may already exist). Check Settings > Environments." }

# 3 + 4. secrets ---------------------------------------------------------------------------------
# The value goes to gh's stdin as exact bytes. `$value | gh secret set` would append a newline (and
# pass-by-argument mangles the JSON's quotes), either of which corrupts a stored secret.
$ghExe = (Get-Command gh).Source
function Set-EnvSecret([string]$name, [string]$value) {
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $ghExe
  $psi.Arguments = "secret set $name --env android-release --repo $repo"
  $psi.UseShellExecute = $false
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardError = $true
  $p = [System.Diagnostics.Process]::Start($psi)
  $bytes = (New-Object System.Text.UTF8Encoding($false)).GetBytes($value)
  $p.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
  $p.StandardInput.Close()
  $err = $p.StandardError.ReadToEnd()
  $p.WaitForExit()
  if ($p.ExitCode -ne 0) { throw "could not set ${name}: $err" }
  Write-Host "  set $name"
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
