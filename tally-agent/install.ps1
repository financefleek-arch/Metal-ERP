<#
.SYNOPSIS
  Installs (or updates) the Tally Agent Windows Service on a shop PC.

.DESCRIPTION
  This script ships inside a zip that was built specifically for one firm:
  the bundled publish\appsettings.json already has that firm's API key and
  backend URL baked in by Metal ERP when the zip was generated. There is
  normally nothing to type - just run this script.

  Self-elevates to Administrator if not already running as one, so a plain
  right-click "Run with PowerShell" is enough.

  The -ShopApiKey / -BackendBaseUrl / -WatchFolder parameters are optional
  overrides, kept for support use (re-pointing an install at a different
  backend, or the old manual flow if a bundle is ever missing its baked-in
  key). They are ignored unless the bundled appsettings.json still has a
  placeholder value.

.PARAMETER InstallDir
  Where the service binaries + config live. Default: C:\Program Files\TallyAgent.

.PARAMETER SourceDir
  Where the published build (dotnet publish output) is. Default: .\publish
  alongside this script.
#>

[CmdletBinding()]
param(
    [string]$ShopApiKey = "",
    [string]$BackendBaseUrl = "",
    [string]$WatchFolder = "",
    [string]$FilePattern = "*",
    [string]$InstallDir = "C:\Program Files\TallyAgent",
    [string]$SourceDir = (Join-Path $PSScriptRoot "publish"),
    [string]$ServiceName = "TallyAgent"
)

$ErrorActionPreference = "Stop"

# --- self-elevate ------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Administrator rights are needed - relaunching elevated..."
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    foreach ($key in $PSBoundParameters.Keys) {
        $value = $PSBoundParameters[$key]
        $argList += "-$key"
        $argList += "`"$value`""
    }
    Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs
    exit
}

if (-not (Test-Path $SourceDir)) {
    throw "Published build not found at '$SourceDir'. This zip looks incomplete - re-download it from Metal ERP."
}

Write-Host "Installing Tally Agent to $InstallDir ..."

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Existing service found - stopping for update."
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Path (Join-Path $SourceDir "*") -Destination $InstallDir -Recurse -Force

# --- config: use the bundled key unless it's a placeholder --------------
# The zip is built per-firm by Metal ERP with the real key already written
# into appsettings.json. Only fall back to overwriting from parameters if
# the bundled file still has a placeholder - keeps a zero-argument run
# working while leaving room for support to re-point an install by hand.
$settingsPath = Join-Path $InstallDir "appsettings.json"
$settings = Get-Content $settingsPath -Raw | ConvertFrom-Json

$placeholders = @("", "REPLACE_ME", "__SHOP_API_KEY__")
$bundledKeyIsPlaceholder = $placeholders -contains $settings.Agent.ShopApiKey

if ($bundledKeyIsPlaceholder -and $ShopApiKey) {
    Write-Host "Bundled config has no key baked in - applying -ShopApiKey."
    $settings.Agent.ShopApiKey = $ShopApiKey
} elseif (-not $bundledKeyIsPlaceholder) {
    Write-Host "Using the key already baked into this download."
}

if ($BackendBaseUrl) { $settings.Agent.BackendBaseUrl = $BackendBaseUrl }
if ($WatchFolder) { $settings.Agent.BackupSync.WatchFolder = $WatchFolder }
if ($FilePattern -ne "*") { $settings.Agent.BackupSync.FilePattern = $FilePattern }

$settings | ConvertTo-Json -Depth 10 | Set-Content -Path $settingsPath -Encoding utf8

if (-not $settings.Agent.ShopApiKey -or ($placeholders -contains $settings.Agent.ShopApiKey)) {
    throw "No API key configured. This download may be broken - get a fresh one from Metal ERP, or pass -ShopApiKey."
}

New-Item -ItemType Directory -Force -Path "C:\ProgramData\TallyAgent\logs" | Out-Null

$exePath = Join-Path $InstallDir "TallyAgent.exe"

if ($existing) {
    Start-Service -Name $ServiceName
    Write-Host "Updated and restarted service '$ServiceName'."
} else {
    New-Service -Name $ServiceName `
        -BinaryPathName "`"$exePath`"" `
        -DisplayName "Tally Agent (Fleek)" `
        -Description "Syncs Tally masters + backups with Metal ERP." `
        -StartupType Automatic | Out-Null
    Start-Service -Name $ServiceName
    Write-Host "Installed and started service '$ServiceName'."
}

Write-Host ""
Write-Host "Waiting a few seconds to confirm the service is running..."
Start-Sleep -Seconds 5
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host "Service is running. It will check in with Metal ERP within about a minute -"
    Write-Host "you can confirm the connection on the Tally Agent page in the console."
} else {
    Write-Host "Service did not report Running - check the logs below before assuming a problem."
}

Write-Host ""
Write-Host "Next step: in TallyPrime, open your company and turn on"
Write-Host "  F1 -> Settings -> Connectivity -> ""TallyPrime acts as Server"""
Write-Host "(see the setup guide included in this download for a screenshot)."
Write-Host ""
Write-Host "Logs: C:\ProgramData\TallyAgent\logs"
