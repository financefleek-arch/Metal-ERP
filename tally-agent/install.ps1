<#
.SYNOPSIS
  Installs (or updates) the Tally Agent Windows Service on a shop PC.

.DESCRIPTION
  Copies a published self-contained build into place and registers it as a
  Windows Service so it runs at boot with no login session required. Run as
  Administrator.

  Publish first (from the tally-agent folder, on a machine with the .NET SDK):

      dotnet publish TallyAgent -c Release -r win-x64 --self-contained -o publish

  Then, on the shop PC, with that `publish` folder alongside this script:

      .\install.ps1 -ShopApiKey "<key from tools.make_backup_shop>" `
                     -BackendBaseUrl "https://metalerp.fleek.example.com" `
                     -WatchFolder "C:\Tally\Backup"

.PARAMETER InstallDir
  Where the service binaries + config live. Default: C:\Program Files\TallyAgent.

.PARAMETER SourceDir
  Where the published build (dotnet publish output) is. Default: .\publish
  alongside this script.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$ShopApiKey,
    [Parameter(Mandatory = $true)] [string]$BackendBaseUrl,
    [Parameter(Mandatory = $true)] [string]$WatchFolder,
    [string]$FilePattern = "*",
    [string]$InstallDir = "C:\Program Files\TallyAgent",
    [string]$SourceDir = (Join-Path $PSScriptRoot "publish"),
    [string]$ServiceName = "TallyAgent"
)

$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script as Administrator."
}

if (-not (Test-Path $SourceDir)) {
    throw "Published build not found at '$SourceDir'. Run 'dotnet publish TallyAgent -c Release -r win-x64 --self-contained -o publish' first."
}

Write-Host "Installing Tally Agent to $InstallDir ..."

$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Existing service found — stopping for update."
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Path (Join-Path $SourceDir "*") -Destination $InstallDir -Recurse -Force

# appsettings.json ships with placeholder values (see appsettings.json in
# source) — overwrite the per-shop fields with what was passed in, so a
# re-run of this script (update path) doesn't require re-typing the key.
$settingsPath = Join-Path $InstallDir "appsettings.json"
$settings = Get-Content $settingsPath -Raw | ConvertFrom-Json
$settings.Agent.ShopApiKey = $ShopApiKey
$settings.Agent.BackendBaseUrl = $BackendBaseUrl
$settings.Agent.BackupSync.WatchFolder = $WatchFolder
$settings.Agent.BackupSync.FilePattern = $FilePattern
$settings | ConvertTo-Json -Depth 10 | Set-Content -Path $settingsPath -Encoding utf8

New-Item -ItemType Directory -Force -Path "C:\ProgramData\TallyAgent\logs" | Out-Null

$exePath = Join-Path $InstallDir "TallyAgent.exe"

if ($existing) {
    Start-Service -Name $ServiceName
    Write-Host "Updated and restarted service '$ServiceName'."
}
else {
    New-Service -Name $ServiceName `
        -BinaryPathName "`"$exePath`"" `
        -DisplayName "Tally Agent (Fleek)" `
        -Description "Watches for Tally's scheduled backup and syncs it to cloud storage." `
        -StartupType Automatic | Out-Null
    Start-Service -Name $ServiceName
    Write-Host "Installed and started service '$ServiceName'."
}

Write-Host "Logs: C:\ProgramData\TallyAgent\logs"
