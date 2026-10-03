<#
.SYNOPSIS
  Removes the Tally Agent from this PC.

.DESCRIPTION
  Stops and removes the per-user scheduled task / Startup entry and, if present,
  the older Windows service; then deletes the installed program files.
  Logs and the agent's small local state database are KEPT unless you pass
  -Purge, so a re-install picks up where it left off.

  Removing the Windows service needs Administrator; this script asks for it
  only when a service is actually installed.

.PARAMETER Purge
  Also delete the agent's logs, local state and downloaded updates.
#>

[CmdletBinding()]
param(
    [switch]$Purge,
    [switch]$NoPause,
    [string]$TaskName = "TallyAgent",
    [string]$ServiceName = "TallyAgent"
)

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue

if ($svc -and -not $isAdmin) {
    Write-Host "A Windows service is installed - removing it needs Administrator. Relaunching elevated..."
    $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Purge) { $argList += "-Purge" }
    $argList += @("-TaskName", "`"$TaskName`"", "-ServiceName", "`"$ServiceName`"")
    Start-Process -FilePath "powershell.exe" -ArgumentList $argList -Verb RunAs
    exit
}

$failed = $false
try {
    # Per-user task + Startup fallback (current user).
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'."
    }
    $run = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    if (Get-ItemProperty -Path $run -Name "TallyAgent" -ErrorAction SilentlyContinue) {
        Remove-ItemProperty -Path $run -Name "TallyAgent"
        Write-Host "Removed the Startup entry."
    }

    # Windows service (older install mode).
    if ($svc) {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        & sc.exe delete $ServiceName | Out-Null
        Write-Host "Removed Windows service '$ServiceName'."
    }

    Get-Process -Name "TallyAgent" -ErrorAction SilentlyContinue | ForEach-Object {
        try { $_.Kill(); $_.WaitForExit(10000) | Out-Null } catch { }
    }
    Start-Sleep -Seconds 1

    $dirs = @(
        (Join-Path $env:LOCALAPPDATA "TallyAgent\app"),
        (Join-Path $env:LOCALAPPDATA "TallyAgent\app.prev"),
        (Join-Path $env:LOCALAPPDATA "TallyAgent\app.bad"),
        (Join-Path $env:ProgramFiles "TallyAgent"),
        (Join-Path $env:ProgramFiles "TallyAgent.prev"),
        (Join-Path $env:ProgramFiles "TallyAgent.bad")
    )
    foreach ($d in $dirs) {
        if (Test-Path $d) {
            try { Remove-Item -Recurse -Force $d; Write-Host "Deleted $d" }
            catch { Write-Host "Could not delete $d ($($_.Exception.Message)) - delete it by hand." -ForegroundColor Yellow }
        }
    }

    if ($Purge) {
        foreach ($d in @((Join-Path $env:LOCALAPPDATA "TallyAgent"), (Join-Path $env:ProgramData "TallyAgent"))) {
            if (Test-Path $d) {
                try { Remove-Item -Recurse -Force $d; Write-Host "Purged $d" }
                catch { Write-Host "Could not purge $d ($($_.Exception.Message))" -ForegroundColor Yellow }
            }
        }
    } else {
        Write-Host "Logs and local state were kept (use -Purge to delete them too)."
    }
    Write-Host "Done. The Tally Agent is removed from this PC."
} catch {
    $failed = $true
    Write-Host "UNINSTALL FAILED: $($_.Exception.Message)" -ForegroundColor Red
}

if (-not $NoPause) {
    Write-Host "Press Enter to close this window..."
    Read-Host | Out-Null
}
if ($failed) { exit 1 }
