# Emulates Task Scheduler's restart behaviour for the e2e: start the agent, and
# whenever it exits, start it again a few seconds later. Runs until the deadline.
param([int]$Minutes = 12)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$work = Join-Path $here "work"
$run = Join-Path $work "run"
$app = Join-Path $run "app"
$data = Join-Path $run "data"
Remove-Item -Recurse -Force $run -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $app, $data | Out-Null
Copy-Item -Recurse -Force (Join-Path $work "out\v1.0.0\*") $app
Get-ChildItem $app -Filter "*.pdb" | Remove-Item -Force

$settings = @{
  Agent = @{
    ShopApiKey = "e2e-key"
    BackendBaseUrl = "http://127.0.0.1:8099"
    StateDbPath = (Join-Path $data "state.db")
    LogDirectory = (Join-Path $data "logs")
    BackupSync = @{ Enabled = $false; WatchFolder = "C:\nonexistent-e2e" }
    BackupHealthMonitor = @{ Enabled = $false }
    WhatsAppDelivery = @{ Enabled = $false }
    TallyMasters = @{ Enabled = $false }
    Update = @{ Enabled = $true }
  }
}
$settings | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $app "appsettings.json") -Encoding utf8

$env:TALLYAGENT_INSTANCE = "e2e"
$exe = Join-Path $app "TallyAgent.exe"
$deadline = (Get-Date).AddMinutes($Minutes)
$log = Join-Path $work "loop.log"
"" | Set-Content $log
while ((Get-Date) -lt $deadline) {
  $p = Start-Process -FilePath $exe -WorkingDirectory $app -PassThru -WindowStyle Hidden
  "{0} started pid {1}" -f (Get-Date -Format HH:mm:ss), $p.Id | Add-Content $log
  $p.WaitForExit()
  "{0} exited code {1}" -f (Get-Date -Format HH:mm:ss), $p.ExitCode | Add-Content $log
  Start-Sleep -Seconds 3
}
"loop done" | Add-Content $log
