# WindowsTimeTracker

A lightweight Windows system-tray script that reminds you to take a break when you've been continuously active at your computer for too long. It also tracks and logs your daily and weekly active time.

## Features

- **Break alerts** — notifies you after 40 minutes of continuous activity, then every 5 minutes until a break is detected
- **Smart activity detection** — tracks mouse movement, keyboard input, and whether your camera or microphone is in use (e.g. during calls)
- **Break detection** — a break is counted after 3 minutes of full inactivity
- **System tray icon** — color-coded (green = active, yellow = alerted); right-click for options
- **Session logging** — each active session is appended to `~/WindowsTimeTracker_log.csv`
- **Daily & weekly stats** — view today's and this week's active time directly from the tray
- **Work-hours report** — generate a 1- or 2-week report showing your clock-in, clock-out, and total active time per day — useful for filling in a time sheet
- **Manual session editing** — add or delete sessions directly from the tray menu to correct the log
- **Pause / Resume** — temporarily suspend the timer from the tray menu
- **Auto-install** — missing Python packages are installed automatically on first run
- **Crash recovery** — a checkpoint file lets the timer survive accidental restarts mid-session

## Requirements

- Windows 10 or 11
- Python 3.10+

Dependencies are installed automatically on first run. To install them manually:

```bash
pip install pyautogui windows-toasts pynput pystray pillow pywin32 psutil
```

## Usage

### Run manually

Run normally (with a console window):

```bash
python WindowsTimeTracker.py
```

Run silently with no console window:

```bash
pythonw WindowsTimeTracker.py
```

You can also rename the file to `WindowsTimeTracker.pyw` and double-click it to launch without a terminal.

The script will appear as a small circle in the system tray (bottom-right). Right-click the icon for options.

### Run automatically at startup (recommended)

Two PowerShell helper scripts are included to register a Task Scheduler task that starts the tracker automatically when you log in, and restarts it if it ever stops.

1. Open **PowerShell as Administrator**.
2. Navigate to the folder containing the scripts.
3. Run:

```powershell
.\RegisterTaskAtStartupPlus15m.ps1
```

This installs missing Python packages and registers a task named **WindowsTimeTracker** that:
- Launches `StartIfNotRunning.ps1` at every logon
- Re-checks every 15 minutes and restarts the tracker if it is not running

To remove the task later:

```powershell
Unregister-ScheduledTask -TaskName "WindowsTimeTracker" -Confirm:$false
```

### Alternative: Startup folder (simpler, no auto-restart)

If you'd rather not use Task Scheduler:

1. Press `Win + R`, type `shell:startup`, and press Enter.
2. Create a shortcut in that folder pointing to `pythonw.exe` with the argument being the full path to `WindowsTimeTracker.py` (or simply copy `WindowsTimeTracker.pyw` into the folder).

This starts the tracker at logon but will not restart it if it crashes or is closed.

## Tray menu

| Item | Description |
|---|---|
| Today: Xh XXm | Shows today's total active time; click for a toast notification |
| This week: Xh XXm | Shows this week's (Mon–Fri) total; click for a toast notification |
| Add session… | Opens a dialog to manually add a past session to the log |
| Delete session… | Opens a list of recent sessions so you can remove incorrect entries |
| Report: last 1 week | Generates a daily breakdown for the last full week and opens it in Notepad |
| Report: last 2 weeks | Same, covering the last two full weeks |
| Reset timer | Resets the continuous-activity counter without taking a break |
| Quit | Logs the current session and exits |

## Configuration

Open `WindowsTimeTracker.py` and edit the settings block near the top:

```python
BREAK_THRESHOLD_SECONDS = 3 * 60    # idle time required to count as a break (default: 3 min)
SHORT_BREAK_MAX_SECONDS = 10 * 60   # breaks shorter than this deduct only BREAK_THRESHOLD_SECONDS
                                     # from active time; longer breaks deduct their full duration
ALERT_AFTER_SECONDS     = 40 * 60   # continuous active time before the first alert (default: 40 min)
REPEAT_ALERT_SECONDS    = 5 * 60    # how often to re-alert if the break is ignored (default: 5 min)
ALERT_SOUND             = None      # path to a .wav file, or None to use the Windows default chime
```

## Session log

Sessions are written to `%USERPROFILE%\WindowsTimeTracker_log.csv` (e.g. `C:\Users\YourName\WindowsTimeTracker_log.csv`) with the following columns:

| Column | Description |
|---|---|
| `date` | Date of the session (YYYY-MM-DD) |
| `session_start` | Start time (HH:MM:SS) |
| `session_end` | End time (HH:MM:SS) |
| `duration_minutes` | Session length in minutes |

Sessions shorter than 1 minute are not logged.

## Work-hours report

Right-click the tray icon and choose **Report: last 1 week** or **Report: last 2 weeks**. A `.txt` file is written to your home folder and opened automatically in Notepad.

Sessions with a gap of less than 30 minutes between them are merged into a single block in the report (the gap itself is not counted as active time).

Each day shows:
- Start and end time of each merged block
- Daily total active time
- Weekly total active time

Example output:

```
Week of Mar 24 – Mar 30, 2026
------------------------------
  Mon Mar 24:
      08:42 – 12:48  (229.0 min active)
      13:30 – 17:31  (218.0 min active)
      Daily total:  7h 27m
  ────────────────────────────────────────────
  Week total:  37h 12m
```

The report is displayed in a temporary file that is deleted automatically once you close Notepad. If you want to keep a copy, use **File → Save As** before closing.

## License

MIT
