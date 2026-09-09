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
    [string]$SourceDir = "",
    [string]$ServiceName = "TallyAgent"
)

$ErrorActionPreference = "Stop"

# $PSScriptRoot is not reliably populated yet when used as a param() default
# value - it depends on exactly how the script was invoked (bare .\install.ps1
# vs. `powershell -File "<path>"`, which is what the elevated self-relaunch
# below uses) and can come back empty, which made Join-Path throw during
# parameter binding - before a single line of the script body ran, so
# nothing ever printed and the elevated window closed instantly with no
# visible error. Resolve it explicitly here instead, once the script body
# has actually started.
if (-not $SourceDir) {
    $scriptRoot = $PSScriptRoot
    if (-not $scriptRoot) { $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
    if (-not $scriptRoot) { $scriptRoot = (Get-Location).Path }
    $SourceDir = Join-Path $scriptRoot "publish"
}

# --- self-elevate ------------------------------------------------------
# A right-click "Run with PowerShell" on a non-elevated shell relaunches
# this script in a SEPARATE, elevated window (Start-Process -Verb RunAs) -
# the original window exits immediately after, so anyone watching THAT
# window sees nothing and the elevated child's own output can vanish just
# as fast if it closes on completion. Two things fix that: a transcript
# file that survives regardless of which window anyone was watching, and a
# pause at the very end of the elevated run so the window itself doesn't
# disappear before it can be read.
$transcriptPath = "C:\ProgramData\TallyAgent\logs\install-transcript.log"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Administrator rights are needed - relaunching elevated..."
    Write-Host "(A new window will open and ask for confirmation. Once it's done,"
    Write-Host " it will pause so you can read the result before it closes.)"
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    foreach ($key in $PSBoundParameters.Keys) {
        $value = $PSBoundParameters[$key]
        $argList += "-$key"
        $argList += "`"$value`""
    }
    Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs
    exit
}

# From here on we ARE the elevated instance - keep a permanent record of
# this run regardless of whether the window stays visible. Best-effort:
# don't let a transcript failure (e.g. the log dir not existing yet) stop
# the actual install.
try {
    New-Item -ItemType Directory -Force -Path (Split-Path $transcriptPath) -ErrorAction Stop | Out-Null
    Start-Transcript -Path $transcriptPath -Append | Out-Null
} catch {
    Write-Host "(Could not start a transcript log - continuing without one.)"
}

$installFailed = $false
try {

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

} catch {
    $installFailed = $true
    Write-Host ""
    Write-Host "INSTALL FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace
}

try { Stop-Transcript | Out-Null } catch { }

Write-Host ""
if ($installFailed) {
    Write-Host "Something went wrong - see the error above (also saved to $transcriptPath)."
} else {
    Write-Host "Done."
}
Write-Host "Press Enter to close this window..."
Read-Host | Out-Null
