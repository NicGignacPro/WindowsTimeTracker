# Creates (or updates) a Windows Task Scheduler task that launches
# WindowsTimeTracker.py at logon and rechecks every 15 minutes.
# Run this script once as Administrator.

# --- Install Python dependencies if missing --------------------------------
$pythonExe = $null
$candidates = @(
    "C:\Users\MANI4218\AppData\Local\miniconda3\python.exe"
)
foreach ($c in $candidates) {
    if (Test-Path $c) { $pythonExe = $c; break }
}
if (-not $pythonExe) {
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { $pythonExe = $cmd.Source }
}

if ($pythonExe) {
    $packages = @("pyautogui", "windows-toasts", "pynput", "pystray", "pillow", "pywin32", "psutil")
    Write-Host "Checking Python dependencies..." -ForegroundColor Cyan
    foreach ($pkg in $packages) {
        $installed = & $pythonExe -m pip show $pkg 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  Installing $pkg..." -ForegroundColor Yellow
            & $pythonExe -m pip install $pkg --quiet
        } else {
            Write-Host "  $pkg already installed." -ForegroundColor DarkGray
        }
    }
    Write-Host "Dependencies OK." -ForegroundColor Green
} else {
    Write-Warning "Python not found — skipping dependency install."
}

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
