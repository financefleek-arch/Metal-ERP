<#
.SYNOPSIS
  Installs (or updates) the Tally Agent on a shop PC.

.DESCRIPTION
  DEFAULT (per-user, no administrator needed): installs under
  %LOCALAPPDATA%\TallyAgent and registers a Scheduled Task that starts the
  agent when the current user logs in (and re-starts it within 5 minutes if it
  ever stops). It runs as the same Windows user who runs TallyPrime, so it sees
  that user's drives, folders and proxy settings.

  -AsService: the older install - a Windows Service under C:\Program Files,
  running as LocalSystem. Needs Administrator (the script elevates itself).
  Use it only where TallyPrime runs on a server / RDP host with no one logged
  in. LocalSystem cannot see mapped network drives or user-profile folders.

  This script ships inside a zip built for ONE firm: the bundled
  publish\appsettings.json already has that firm's key and backend URL baked
  in. There is normally nothing to type.

  Before changing anything it runs a PRE-FLIGHT check and prints PASS / WARN /
  FAIL for each item. A FAIL stops the install with the reason; WARN is
  information.

.PARAMETER AsService
  Install as a Windows Service (LocalSystem) instead of the per-user task.

.PARAMETER PreflightOnly
  Run the checks and stop. Changes nothing.

.PARAMETER SkipPreflight
  Skip the checks (support use only).

.PARAMETER AddDefenderExclusion
  Also add the install folder to Windows Defender's exclusions (needs
  Administrator). Off by default - only use it if Defender quarantined the exe.

.PARAMETER TallyGatewayUrl
  Where TallyPrime's HTTP gateway is. Default http://127.0.0.1:9000 (TallyPrime
  on this same PC). Set it if TallyPrime runs on another PC, e.g.
  -TallyGatewayUrl http://192.168.1.20:9000

.PARAMETER InstallDir
  Override the install folder.

.PARAMETER SourceDir
  Where the published build is. Default: .\publish next to this script.
#>

[CmdletBinding()]
param(
    [switch]$AsService,
    [switch]$PreflightOnly,
    [switch]$SkipPreflight,
    [switch]$AddDefenderExclusion,
    [switch]$NoPause,
    [string]$ShopApiKey = "",
    [string]$BackendBaseUrl = "",
    [string]$WatchFolder = "",
    [string]$TallyGatewayUrl = "",
    # Optional override for the backup-file globs. Empty = keep the bundled
    # appsettings value (["TDBK*","TBK*.900"] - native Data BacKup manifest +
    # parts, never the TSDBK SQL export).
    [string[]]$FilePatterns = @(),
    [string]$InstallDir = "",
    [string]$SourceDir = "",
    [string]$TaskName = "TallyAgent",
    [string]$ServiceName = "TallyAgent"
)

$ErrorActionPreference = "Stop"

# $PSScriptRoot is not reliably populated when used as a param() default, so
# resolve it here, once the script body has started.
if (-not $SourceDir) {
    $scriptRoot = $PSScriptRoot
    if (-not $scriptRoot) { $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
    if (-not $scriptRoot) { $scriptRoot = (Get-Location).Path }
    $SourceDir = Join-Path $scriptRoot "publish"
}

if ($AsService) {
    $dataDir = Join-Path $env:ProgramData "TallyAgent"
    if (-not $InstallDir) { $InstallDir = Join-Path $env:ProgramFiles "TallyAgent" }
} else {
    $dataDir = Join-Path $env:LOCALAPPDATA "TallyAgent"
    if (-not $InstallDir) { $InstallDir = Join-Path $dataDir "app" }
}
$logDir = Join-Path $dataDir "logs"
$transcriptPath = Join-Path $logDir "install-transcript.log"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# --- service mode needs Administrator: relaunch elevated ----------------
if ($AsService -and -not $PreflightOnly -and -not $isAdmin) {
    Write-Host "Service mode needs Administrator rights - relaunching elevated..."
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    foreach ($key in $PSBoundParameters.Keys) {
        $value = $PSBoundParameters[$key]
        if ($value -is [System.Management.Automation.SwitchParameter]) {
            if ($value.IsPresent) { $argList += "-$key" }
        } elseif ($value -is [array]) {
            $argList += "-$key"
            $argList += (($value | ForEach-Object { "`"$_`"" }) -join ",")
        } else {
            $argList += "-$key"
            $argList += "`"$value`""
        }
    }
    Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs
    exit
}

try {
    New-Item -ItemType Directory -Force -Path $logDir -ErrorAction Stop | Out-Null
    Start-Transcript -Path $transcriptPath -Append | Out-Null
} catch {
    Write-Host "(Could not start a transcript log - continuing without one.)"
}

# --- pre-flight ---------------------------------------------------------
$script:checks = New-Object System.Collections.ArrayList
function Add-Check([string]$Name, [string]$Status, [string]$Detail) {
    [void]$script:checks.Add([pscustomobject]@{ Name = $Name; Status = $Status; Detail = $Detail })
}

function Read-BundledSettings {
    $p = Join-Path $SourceDir "appsettings.json"
    if (-not (Test-Path $p)) { return $null }
    try { return (Get-Content $p -Raw | ConvertFrom-Json) } catch { return $null }
}

function Invoke-Preflight {
    $bundled = Read-BundledSettings

    # OS: .NET 10 (self-contained) needs Windows 10 1607+ / Server 2016+.
    $build = [Environment]::OSVersion.Version.Build
    $caption = ""
    try { $caption = (Get-CimInstance Win32_OperatingSystem -ErrorAction Stop).Caption } catch { }
    if ([Environment]::OSVersion.Version.Major -lt 10 -or $build -lt 14393) {
        Add-Check "Windows version" "FAIL" "$caption (build $build) is too old. Windows 10 (1607 or newer), Windows 11 or Server 2016+ is required."
    } else {
        Add-Check "Windows version" "PASS" "$caption (build $build)"
    }

    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $arch = $env:PROCESSOR_ARCHITEW6432 }
    if (-not [Environment]::Is64BitOperatingSystem -or $arch -eq "x86") {
        Add-Check "64-bit Windows" "FAIL" "This build is 64-bit only (this PC is 32-bit)."
    } elseif ($arch -eq "ARM64") {
        Add-Check "64-bit Windows" "WARN" "ARM64 PC: the x64 build runs under emulation. Should work; tell Fleek if it does not."
    } else {
        Add-Check "64-bit Windows" "PASS" $arch
    }

    if ($PSVersionTable.PSVersion.Major -lt 5) {
        Add-Check "PowerShell" "FAIL" "PowerShell $($PSVersionTable.PSVersion) is too old (5.1 needed)."
    } else {
        Add-Check "PowerShell" "PASS" "$($PSVersionTable.PSVersion)"
    }

    try {
        $root = [System.IO.Path]::GetPathRoot($InstallDir)
        $free = (New-Object System.IO.DriveInfo($root)).AvailableFreeSpace
        $freeMb = [math]::Round($free / 1MB)
        if ($freeMb -lt 500) { Add-Check "Disk space" "FAIL" "$freeMb MB free on $root - at least 500 MB needed." }
        else { Add-Check "Disk space" "PASS" "$freeMb MB free on $root" }
    } catch {
        Add-Check "Disk space" "WARN" "Could not read free space: $($_.Exception.Message)"
    }

    # An older service + a per-user task would both start - the agent allows only
    # one per PC, so one would quietly do nothing.
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($svc -and -not $AsService) {
        Add-Check "Older Windows service" "FAIL" "A '$ServiceName' Windows service is already installed. Remove it first: right-click uninstall.ps1 -> Run with PowerShell (it asks for Administrator). Then run this installer again."
    } elseif ($svc -and $AsService) {
        Add-Check "Older Windows service" "PASS" "Existing service will be updated."
    } else {
        Add-Check "Older Windows service" "PASS" "none"
    }
    if ($AsService -and (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) {
        Add-Check "Per-user task" "WARN" "A '$TaskName' scheduled task exists for some user; remove it with uninstall.ps1 (as that user) or both will try to start."
    }

    # Backend reachable + clock.
    $base = $BackendBaseUrl
    if (-not $base -and $bundled) { $base = $bundled.Agent.BackendBaseUrl }
    if ($base) {
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $resp = Invoke-WebRequest -Uri ($base.TrimEnd('/') + "/health") -UseBasicParsing -TimeoutSec 20
            Add-Check "Reach Fleek ($base)" "PASS" "HTTP $($resp.StatusCode)"
            $dateHeader = $resp.Headers["Date"]
            if ($dateHeader) {
                $skew = [math]::Abs(((Get-Date).ToUniversalTime() - ([datetime]::Parse($dateHeader)).ToUniversalTime()).TotalMinutes)
                if ($skew -gt 5) { Add-Check "PC clock" "WARN" ("Clock is off by about {0:N0} minutes. Uploads can fail on a wrong clock - fix the date/time." -f $skew) }
                else { Add-Check "PC clock" "PASS" "within 5 minutes" }
            }
        } catch {
            Add-Check "Reach Fleek ($base)" "WARN" "Could not connect: $($_.Exception.Message). Check the internet / firewall / proxy. The agent keeps retrying once connected."
        }
    } else {
        Add-Check "Reach Fleek" "WARN" "No backend address found in the bundle."
    }

    # TallyPrime gateway.
    $gw = $TallyGatewayUrl
    if (-not $gw -and $bundled -and $bundled.Agent.TallyMasters) { $gw = $bundled.Agent.TallyMasters.GatewayUrl }
    if (-not $gw) { $gw = "http://127.0.0.1:9000" }
    $reachable = $false
    try {
        [void](Invoke-WebRequest -Uri $gw -UseBasicParsing -TimeoutSec 5)
        $reachable = $true
    } catch {
        # An HTTP error status still proves something is listening.
        if ($_.Exception.Response) { $reachable = $true }
    }
    if ($reachable) { Add-Check "TallyPrime gateway ($gw)" "PASS" "answering" }
    else { Add-Check "TallyPrime gateway ($gw)" "WARN" "Not answering. Open TallyPrime + a company and turn on F1 > Settings > Connectivity > 'TallyPrime acts as Server'. If TallyPrime is on another PC, re-run with -TallyGatewayUrl http://<that-pc>:9000. You can continue - this is checked again every minute." }

    # Backup watch folder.
    $wf = $WatchFolder
    if (-not $wf -and $bundled -and $bundled.Agent.BackupSync) { $wf = $bundled.Agent.BackupSync.WatchFolder }
    if ($wf) {
        $wf = [Environment]::ExpandEnvironmentVariables($wf)
        if (Test-Path $wf) { Add-Check "Backup folder ($wf)" "PASS" "exists" }
        else { Add-Check "Backup folder ($wf)" "WARN" "Does not exist yet. Create it (or tell Fleek the folder TallyPrime backs up to) - see README.txt." }
        if ($AsService -and $wf -match '^[A-Za-z]:') {
            try {
                $dt = (Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='" + $wf.Substring(0, 2) + "'") -ErrorAction Stop).DriveType
                if ($dt -eq 4) { Add-Check "Backup folder on a network drive" "WARN" "Service mode runs as LocalSystem, which cannot see mapped drives. Use the default per-user install instead." }
            } catch { }
        }
    }
}

function Show-Preflight {
    Write-Host ""
    Write-Host "Pre-flight check"
    Write-Host "----------------"
    foreach ($c in $script:checks) {
        $color = "Green"
        if ($c.Status -eq "WARN") { $color = "Yellow" } elseif ($c.Status -eq "FAIL") { $color = "Red" }
        Write-Host ("[{0}] {1}" -f $c.Status, $c.Name) -ForegroundColor $color
        if ($c.Status -ne "PASS" -or $c.Detail) { Write-Host ("       {0}" -f $c.Detail) }
    }
    Write-Host ""
}

$installFailed = $false
try {

    if (-not (Test-Path $SourceDir)) {
        throw "Published build not found at '$SourceDir'. This zip looks incomplete - re-download it from Metal ERP."
    }

    if (-not $SkipPreflight) {
        Invoke-Preflight
        Show-Preflight
        $fails = @($script:checks | Where-Object { $_.Status -eq "FAIL" })
        if ($fails.Count -gt 0) { throw "Pre-flight found $($fails.Count) blocking problem(s) (marked FAIL above). Fix them and run the installer again." }
    }
    if ($PreflightOnly) {
        Write-Host "Pre-flight only - nothing was installed."
        throw [System.OperationCanceledException]::new("preflight-only")
    }

    # Files from a downloaded zip carry a "downloaded from the internet" mark that
    # triggers extra warnings; clear it on the files we are about to run.
    Get-ChildItem -Path $SourceDir -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

    $mode = "per-user task"
    if ($AsService) { $mode = "Windows service" }
    Write-Host "Installing Tally Agent ($mode) to $InstallDir ..."

    # --- stop whatever is running --------------------------------------
    if ($AsService) {
        $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($existing) { Write-Host "Existing service found - stopping for update."; Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue }
    } else {
        $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($existing) { Write-Host "Existing task found - stopping for update."; Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    }
    Get-Process -Name "TallyAgent" -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($InstallDir, [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { try { $_.Kill(); $_.WaitForExit(10000) | Out-Null } catch { } }

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    # Leftovers from an in-agent update.
    foreach ($suffix in @(".prev", ".bad")) {
        $left = $InstallDir.TrimEnd('\') + $suffix
        if (Test-Path $left) { Remove-Item -Recurse -Force $left -ErrorAction SilentlyContinue }
    }
    Copy-Item -Path (Join-Path $SourceDir "*") -Destination $InstallDir -Recurse -Force

    # --- config: use the bundled key unless it is a placeholder ----------
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
    if ($FilePatterns.Count -gt 0) {
        $settings.Agent.BackupSync.FilePatterns = $FilePatterns
        $settings.Agent.BackupSync.PSObject.Properties.Remove("FilePattern")
    }
    if ($TallyGatewayUrl) {
        $settings.Agent.TallyMasters.GatewayUrl = $TallyGatewayUrl
        $settings.Agent.WhatsAppDelivery.TallyGatewayBaseUrl = $TallyGatewayUrl
    }
    if ($AsService) {
        # Per-user paths in the bundle (%LOCALAPPDATA%) mean nothing to LocalSystem.
        $settings.Agent.StateDbPath = (Join-Path $dataDir "state.db")
        $settings.Agent.LogDirectory = $logDir
    }

    $settings | ConvertTo-Json -Depth 10 | Set-Content -Path $settingsPath -Encoding utf8

    if (-not $settings.Agent.ShopApiKey -or ($placeholders -contains $settings.Agent.ShopApiKey)) {
        throw "No API key configured. This download may be broken - get a fresh one from Metal ERP, or pass -ShopApiKey."
    }

    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $exePath = Join-Path $InstallDir "TallyAgent.exe"
    $logBaseline = 0
    $existingLogs = Get-ChildItem -Path $logDir -Filter "tally-agent-*.log" -ErrorAction SilentlyContinue
    foreach ($lf in $existingLogs) { $logBaseline += @(Select-String -Path $lf.FullName -Pattern "checkin ok" -SimpleMatch -ErrorAction SilentlyContinue).Count }

    # --- register + start -------------------------------------------------
    if ($AsService) {
        # The key lives in appsettings.json: keep it readable by SYSTEM and Administrators only.
        & icacls.exe $settingsPath /inheritance:r /grant:r "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null

        $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($svc) {
            Start-Service -Name $ServiceName
            Write-Host "Updated and restarted service '$ServiceName'."
        } else {
            New-Service -Name $ServiceName -BinaryPathName "`"$exePath`"" -DisplayName "Tally Agent (Fleek)" `
                -Description "Syncs Tally masters + backups with Metal ERP." -StartupType Automatic | Out-Null
            Start-Service -Name $ServiceName
            Write-Host "Installed and started service '$ServiceName'."
        }
        # Restart on crash (5s, 30s, 60s; counter resets daily) and start after the network is up.
        & sc.exe failure $ServiceName reset= 86400 actions= restart/5000/restart/30000/restart/60000 | Out-Null
        & sc.exe failureflag $ServiceName 1 | Out-Null
        & sc.exe config $ServiceName start= delayed-auto | Out-Null
    } else {
        $user = "$env:USERDOMAIN\$env:USERNAME"
        $registered = $false
        try {
            $action = New-ScheduledTaskAction -Execute $exePath -WorkingDirectory $InstallDir
            $atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
            # Safety net: if the agent ever exits, the next 5-minute tick starts it again.
            $repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
                -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
            $tsettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) `
                -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
            Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($atLogon, $repeat) `
                -Settings $tsettings -Principal $principal -Description "Fleek Tally Agent (per-user)" -Force | Out-Null
            Start-ScheduledTask -TaskName $TaskName
            $registered = $true
            Write-Host "Registered scheduled task '$TaskName' (starts at your login) and started it."
        } catch {
            Write-Host "Could not register a scheduled task ($($_.Exception.Message)) - falling back to the Startup registry entry." -ForegroundColor Yellow
        }
        if (-not $registered) {
            Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "TallyAgent" -Value "`"$exePath`""
            Start-Process -FilePath $exePath -WorkingDirectory $InstallDir
            Write-Host "Added a Startup entry and started the agent."
        }
    }

    if ($AddDefenderExclusion) {
        try {
            Add-MpPreference -ExclusionPath $InstallDir -ErrorAction Stop
            Write-Host "Added $InstallDir to Windows Defender exclusions."
        } catch {
            Write-Host "Could not add a Defender exclusion (needs Administrator): $($_.Exception.Message)" -ForegroundColor Yellow
        }
    }

    # --- confirm it is really talking to Fleek ----------------------------
    Write-Host ""
    Write-Host "Waiting up to 90 seconds for the agent to check in with Fleek..."
    $connected = $false
    $rejected = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 3
        $count = 0
        foreach ($lf in @(Get-ChildItem -Path $logDir -Filter "tally-agent-*.log" -ErrorAction SilentlyContinue)) {
            $count += @(Select-String -Path $lf.FullName -Pattern "checkin ok" -SimpleMatch -ErrorAction SilentlyContinue).Count
            if (@(Select-String -Path $lf.FullName -Pattern "checkin rejected: 401" -SimpleMatch -ErrorAction SilentlyContinue).Count -gt 0) { $rejected = $true }
        }
        if ($count -gt $logBaseline) { $connected = $true; break }
    }
    if ($connected) {
        Write-Host "[PASS] The agent checked in with Fleek." -ForegroundColor Green
    } elseif ($rejected) {
        Write-Host "[WARN] Fleek rejected this agent's key (401). The download may be out of date - ask Fleek for a fresh installer." -ForegroundColor Yellow
    } else {
        Write-Host "[WARN] No check-in seen yet. It may just need a moment, or the internet/proxy is blocking it." -ForegroundColor Yellow
        Write-Host "       Look at the newest file in $logDir"
    }

    Write-Host ""
    Write-Host "Next step: in TallyPrime, open your company and turn on"
    Write-Host "  F1 -> Settings -> Connectivity -> ""TallyPrime acts as Server"""
    Write-Host ""
    Write-Host "Logs: $logDir"

} catch [System.OperationCanceledException] {
    # -PreflightOnly: a clean early exit, not an error.
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
if (-not $NoPause) {
    Write-Host "Press Enter to close this window..."
    Read-Host | Out-Null
}
if ($installFailed) { exit 1 }
