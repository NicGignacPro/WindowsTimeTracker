# Creates (or updates) a Windows Task Scheduler task that launches
# WindowsTimeTracker.py at logon and rechecks every 15 minutes.
# Run this script once as Administrator.

$taskName   = "WindowsTimeTracker"
$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$watcherPs1 = Join-Path $scriptDir "StartIfNotRunning.ps1"

# --- Action: run the watcher script hidden --------------------------------
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -WindowStyle Hidden -NonInteractive -File `"$watcherPs1`""

# --- Triggers: at logon + every 15 minutes --------------------------------
$triggerLogon  = New-ScheduledTaskTrigger -AtLogOn
$triggerRepeat = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 15) `
                     -Once -At (Get-Date)

# --- Settings -------------------------------------------------------------
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries

# --- Principal: current user, run only when logged on --------------------
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

# --- Register (create or overwrite) ---------------------------------------
$params = @{
    TaskName  = $taskName
    Action    = $action
    Trigger   = @($triggerLogon, $triggerRepeat)
    Settings  = $settings
    Principal = $principal
    Force     = $true
}

Register-ScheduledTask @params | Out-Null

Write-Host "Task '$taskName' registered successfully." -ForegroundColor Green
Write-Host "  Watcher : $watcherPs1"
Write-Host "  Triggers: at logon + every 15 minutes"
