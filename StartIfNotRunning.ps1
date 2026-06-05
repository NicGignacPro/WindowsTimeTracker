# Checks if WindowsTimeTracker.py is already running; starts it if not.
# Schedule this script with Windows Task Scheduler.

$scriptName = "WindowsTimeTracker.py"
$scriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Definition
$scriptPath = Join-Path $scriptDir $scriptName

# Find any python/pythonw process whose command line contains the script name
$running = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" |
    Where-Object { $_.CommandLine -like "*$scriptName*" }

if (-not $running) {
    # Use pythonw so no console window appears
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    $pythonw = if ($cmd) { $cmd.Source } else { $null }
    if (-not $pythonw) {
        # Fall back to python.exe if pythonw is not on PATH
        $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
        $pythonw = if ($cmd) { $cmd.Source } else { $null }
    }

    if ($pythonw) {
        Start-Process -FilePath $pythonw -ArgumentList "`"$scriptPath`"" -WorkingDirectory $scriptDir -WindowStyle Hidden
    } else {
        Write-Error "Python executable not found. Make sure Python is on the PATH."
        exit 1
    }
}
