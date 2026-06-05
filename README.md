# WindowsTimeTracker

A lightweight Windows system-tray script that reminds you to take a break when you've been continuously active at your computer for too long. It also tracks and logs your daily and weekly active time.

## Features

- **Break alerts** — notifies you after 40 minutes of continuous activity, then every 5 minutes until a break is detected
- **Smart activity detection** — tracks mouse movement, keyboard input, and whether your camera or microphone is in use (e.g. during calls)
- **Break detection** — a break is counted after 3 minutes of full inactivity
- **System tray icon** — color-coded (green = active, yellow = alerted); right-click for options
- **Session logging** — each active session is appended to `~/WindowsTimeTracker_log.csv`
- **Daily & weekly stats** — view today's and this week's active time directly from the tray
- **Work-hours report** — generate a 1- or 2-week report showing your first clock-in, last clock-out, and total active time per day — useful for filling in a time sheet
- **Pause / Resume** — temporarily suspend the timer from the tray menu
- **Crash recovery** — a checkpoint file lets the timer survive accidental restarts mid-session

## Requirements

- Windows 10 or 11
- Python 3.10+

Install dependencies:

```bash
pip install pyautogui windows-toasts pynput pystray pillow pywin32 psutil
```

## Usage

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

## Tray menu

| Item | Description |
|---|---|
| Today: Xh XXm | Shows today's total active time; click for a toast notification |
| This week: Xh XXm | Shows this week's (Mon–Fri) total; click for a toast notification |
| Report: last 1 week | Generates a daily breakdown for the last full week and opens it in Notepad |
| Report: last 2 weeks | Same, covering the last two full weeks |
| Reset timer | Resets the active-time counter without taking a break |
| Pause / Resume timer | Temporarily stops break detection |
| Quit | Logs the current session and exits |

## Configuration

Open `WindowsTimeTracker.py` and edit the settings block near the top:

```python
BREAK_THRESHOLD_SECONDS = 3 * 60   # idle time required to count as a break (default: 3 min)
ALERT_AFTER_SECONDS     = 40 * 60  # continuous active time before the first alert (default: 40 min)
REPEAT_ALERT_SECONDS    = 5 * 60   # how often to re-alert if the break is ignored (default: 5 min)
ALERT_SOUND             = None     # path to a .wav file, or None to use the Windows default chime
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

Each day shows:
- First clock-in and last clock-out time
- Total active time for the day
- Individual session rows with start, end, and duration

Example output:

```
Week of Mar 24 – Mar 30, 2026
------------------------------
  Mon Mar 24:  08:42 – 17:31   |  active 6h 14m  (4 sessions)
      08:42 – 10:15  (93.0 min)
      10:32 – 12:48  (136.0 min)
      13:30 – 15:57  (147.0 min)
      16:20 – 17:31  (71.0 min)
  Tue Mar 25:  09:01 – 16:45   |  active 5h 02m  (3 sessions)
  ...
```

The report file is saved as `WindowsTimeTracker_report_1w_YYYY-MM-DD.txt` (or `2w`) in `%USERPROFILE%\`.

## Run on startup (optional)

To have the script start automatically with Windows:

1. Press `Win + R`, type `shell:startup`, and press Enter
2. Create a shortcut to `WindowsTimeTracker.pyw` (or `pythonw.exe` pointing to the script) in that folder

## License

MIT
