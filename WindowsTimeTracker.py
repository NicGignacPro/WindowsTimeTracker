"""
WindowsTimeTracker — Windows Notification Script
-------------------------------------------------
Sits in the system tray. Right-click the icon for options.

Activity = mouse movement, keyboard input, or in a call (camera/mic in use).
A break  = fully idle for 2+ minutes straight.

After 30 min of continuous activity, a notification fires.
If ignored, repeats every 5 min until a break is detected or timer is reset.

Also tracks daily active time and logs sessions to:
  %USERPROFILE%/WindowsTimeTracker_log.csv

Requirements:
    pip install pyautogui windows-toasts pynput pystray pillow pywin32 psutil

Run:
    python WindowsTimeTracker.py

To run silently with no console window:
    pythonw WindowsTimeTracker.py  (or rename to .pyw)
"""

import ctypes
import time
import threading
import csv
import os
import winsound
from datetime import datetime, date, timedelta

import winreg
import pyautogui
import win32api
import win32con
import win32gui
from pynput import keyboard
from windows_toasts import Toast, WindowsToaster, InteractableWindowsToaster, ToastDuration, ToastButton
from PIL import Image, ImageDraw
import pystray
import tkinter as tk
from tkinter import ttk, messagebox


def disable_quick_edit() -> None:
    """Disable Quick Edit mode on the Windows console.

    When Quick Edit is enabled (the default), clicking inside the console
    window selects text and *blocks* all writes to stdout.  Because the
    main loop calls print(), this freezes the entire program until the
    user presses Enter or Escape — silently losing all activity tracking.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(ctypes.c_long(-10))  # STD_INPUT_HANDLE
        if handle == -1:
            return
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return  # not attached to a console (e.g. pythonw)
        ENABLE_QUICK_EDIT_MODE = 0x0040
        ENABLE_EXTENDED_FLAGS  = 0x0080
        mode.value &= ~ENABLE_QUICK_EDIT_MODE
        mode.value |= ENABLE_EXTENDED_FLAGS
        kernel32.SetConsoleMode(handle, mode)
    except Exception:
        pass

# ── Settings ──────────────────────────────────────────────────────────────────
BREAK_THRESHOLD_SECONDS       = 3 * 60   # idle time to count as a break (3 min)
SHORT_BREAK_MAX_SECONDS       = 10 * 60  # breaks shorter than this lose only BREAK_THRESHOLD_SECONDS;
                                          # breaks at or above this are counted in full
ALERT_AFTER_SECONDS           = 40 * 60  # continuous active time before first alert (40 min)
REPEAT_ALERT_SECONDS    = 5 * 60   # repeat alert interval (5 min)
POLL_INTERVAL_SECONDS   = 5        # main loop frequency
DEVICE_POLL_SECONDS     = BREAK_THRESHOLD_SECONDS  # check camera/mic registry
LOG_FILE                = os.path.join(os.path.expanduser("~"), "WindowsTimeTracker_log.csv")
CHECKPOINT_FILE         = os.path.join(os.path.expanduser("~"), "WindowsTimeTracker_checkpoint.txt")
LOCK_FILE               = os.path.join(os.path.expanduser("~"), "WindowsTimeTracker.lock")
# Alert sound: path to a .wav file, or None to use the built-in Windows chime
ALERT_SOUND             = None
# ──────────────────────────────────────────────────────────────────────────────

_lock             = threading.Lock()
_last_key_time    = 0.0
_in_call          = False   # True when camera or microphone is in use (registry-based)
_woke_from_sleep  = False   # set to True when Windows fires a resume event

state = {
    "active_since":       time.monotonic(),
    "idle_since":         None,
    "idle_since_wall":    None,   # wall-clock equivalent of idle_since
    "alerted":            False,
    "last_alert_time":    None,
    "reset_requested":    False,
    "paused":             False,
    # Session tracking
    "session_start":      datetime.now(),   # wall-clock start of current active session
    "session_active":     True,             # are we currently in an active session?
    "today_total_secs":   0.0,              # accumulated active seconds today
    "log_date":           date.today(),     # date we last tallied (to reset on new day)
}


# ── CSV log ───────────────────────────────────────────────────────────────────

def ensure_log_file() -> None:
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "session_start", "session_end", "duration_minutes"])


def log_session(start: datetime, end: datetime) -> None:
    ensure_log_file()
    duration_minutes = round((end - start).total_seconds() / 60, 1)
    if duration_minutes < 1:
        return
    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            start.strftime("%Y-%m-%d"),
            start.strftime("%H:%M:%S"),
            end.strftime("%H:%M:%S"),
            duration_minutes,
        ])
    print(f"[{time.strftime('%H:%M:%S')}] Session logged: "
          f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')} "
          f"({duration_minutes} min)")

# Any single logged session longer than this is treated as a bug artifact and ignored.
# Real sessions are bounded by activity patterns (BREAK_THRESHOLD_SECONDS of idle = new session).
MAX_SESSION_MINUTES = 10 * 60   # 10 hours


def get_today_total_from_log() -> float:
    """Read the CSV and sum up today's logged sessions in seconds."""
    if not os.path.exists(LOG_FILE):
        return 0.0
    today_str = date.today().strftime("%Y-%m-%d")
    total = 0.0
    with open(LOG_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["date"] == today_str:
                mins = float(row["duration_minutes"])
                if mins <= MAX_SESSION_MINUTES:
                    total += mins * 60
    return total


def get_week_total_from_log() -> float:
    """Sum logged sessions for Mon–Fri of the current week, excluding today, in seconds.
    Today's in-memory total is added by the callers to avoid double-counting."""
    if not os.path.exists(LOG_FILE):
        return 0.0
    today      = date.today()
    week_start = today - timedelta(days=today.weekday())          # Monday
    week_end   = week_start + timedelta(days=4)                   # Friday
    total      = 0.0
    with open(LOG_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row_date = date.fromisoformat(row["date"])
            except ValueError:
                continue
            if week_start <= row_date <= week_end and row_date != today:
                mins = float(row["duration_minutes"])
                if mins <= MAX_SESSION_MINUTES:
                    total += mins * 60
    return total


def format_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m}m"


# ── Work-hours report ─────────────────────────────────────────────────────────

def generate_report(weeks: int = 1) -> None:
    """
    Build a plain-text work-hours report covering the last *weeks* full weeks
    (Mon–Sun) plus today, write it to a temp file, and open it in Notepad.

    Sessions separated by a break shorter than REPORT_MERGE_MINUTES are merged
    into a single block (the gap time is not counted as active).
    Each day shows first clock-in, last clock-out, total active time, and the
    merged session blocks — handy for filling in a time sheet.
    """
    REPORT_MERGE_MINUTES = 30   # gaps shorter than this merge adjacent sessions

    today      = date.today()
    days_back  = today.weekday() + 7 * weeks
    start_date = today - timedelta(days=days_back)

    # ── Collect sessions from CSV ─────────────────────────────────────────────
    sessions_by_day: dict[date, list[dict]] = {}
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    row_date = date.fromisoformat(row["date"])
                except ValueError:
                    continue
                if start_date <= row_date <= today:
                    sessions_by_day.setdefault(row_date, []).append(row)

    # Fold in any in-memory session for today not yet flushed
    with _lock:
        active  = state["session_active"]
        s_start = state["session_start"]
    if active and s_start.date() == today:
        in_mem = {
            "date":             today.isoformat(),
            "session_start":    s_start.strftime("%H:%M:%S"),
            "session_end":      datetime.now().strftime("%H:%M:%S"),
            "duration_minutes": str(round((datetime.now() - s_start).total_seconds() / 60, 1)),
        }
        sessions_by_day.setdefault(today, []).append(in_mem)

    # ── Helper: merge sessions with short gaps ────────────────────────────────
    def merge_day_sessions(raw: list[dict]) -> list[dict]:
        """
        Sort sessions by start time, then merge consecutive ones whose gap is
        shorter than REPORT_MERGE_MINUTES. Returns a list of merged 'sessions'
        where each entry has the same keys as the originals plus a
        'sub_sessions' list for the detail rows.
        """
        valid = [s for s in raw if float(s["duration_minutes"]) >= 1
                 and float(s["duration_minutes"]) <= MAX_SESSION_MINUTES]
        if not valid:
            return []
        valid.sort(key=lambda s: s["session_start"])

        groups: list[dict] = []
        cur_subs  = [valid[0]]
        cur_start = valid[0]["session_start"]
        cur_end   = valid[0]["session_end"]
        cur_mins  = float(valid[0]["duration_minutes"])

        for s in valid[1:]:
            end_dt   = datetime.strptime(cur_end[:8],          "%H:%M:%S")
            start_dt = datetime.strptime(s["session_start"][:8], "%H:%M:%S")
            gap_min  = (start_dt - end_dt).total_seconds() / 60
            if gap_min < REPORT_MERGE_MINUTES:
                cur_subs.append(s)
                cur_end   = max(cur_end, s["session_end"])
                cur_mins += float(s["duration_minutes"])
            else:
                groups.append({
                    "session_start":    cur_start,
                    "session_end":      cur_end,
                    "duration_minutes": str(round(cur_mins, 1)),
                    "sub_sessions":     cur_subs,
                })
                cur_subs  = [s]
                cur_start = s["session_start"]
                cur_end   = s["session_end"]
                cur_mins  = float(s["duration_minutes"])

        groups.append({
            "session_start":    cur_start,
            "session_end":      cur_end,
            "duration_minutes": str(round(cur_mins, 1)),
            "sub_sessions":     cur_subs,
        })
        return groups

    # ── Build report lines ────────────────────────────────────────────────────
    lines: list[str] = []
    title = f"Work Hours Report — last {weeks} week{'s' if weeks > 1 else ''}"
    lines.append(title)
    lines.append("=" * len(title))
    lines.append(f"Generated: {datetime.now().strftime('%A %B %d, %Y at %H:%M')}")
    lines.append(f"Source:    {LOG_FILE}")
    lines.append("")

    current        = start_date
    week_active_secs = 0.0

    while current <= today:
        # Week header on Mondays
        if current.weekday() == 0:
            week_end = current + timedelta(days=6)
            wh = f"Week of {current.strftime('%b %d')} – {week_end.strftime('%b %d, %Y')}"
            lines.append(wh)
            lines.append("-" * len(wh))
            week_active_secs = 0.0

        raw_sessions   = sessions_by_day.get(current, [])
        merged         = merge_day_sessions(raw_sessions)
        day_label      = current.strftime("%a %b %d")

        if not merged:
            lines.append(f"  {day_label}:  —  (no activity logged)")
        else:
            day_mins = sum(float(g["duration_minutes"]) for g in merged)
            week_active_secs += day_mins * 60
            lines.append(f"  {day_label}:")
            for g in merged:
                lines.append(
                    f"      {g['session_start'][:5]} – {g['session_end'][:5]}"
                    f"  ({g['duration_minutes']} min active)"
                )
            lines.append(f"      Daily total:  {format_duration(day_mins * 60)}")

        # Weekly total + blank line after Sunday (or last day in range)
        is_last_day = (current == today)
        if current.weekday() == 6 or is_last_day:
            if week_active_secs > 0:
                lines.append(f"  {'─' * 44}")
                lines.append(f"  Week total:  {format_duration(week_active_secs)}")
            lines.append("")

        current += timedelta(days=1)

    report_text = "\n".join(lines) + "\n"

    report_path = os.path.join(
        os.path.expanduser("~"),
        f"WindowsTimeTracker_report_{weeks}w_{date.today().isoformat()}.txt",
    )
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"[{time.strftime('%H:%M:%S')}] Report written to {report_path}")
    import subprocess
    try:
        proc = subprocess.Popen(["notepad.exe", report_path])
        proc.wait()
    finally:
        try:
            os.remove(report_path)
        except Exception:
            pass


# ── Checkpoint (survive restarts mid-session) ─────────────────────────────────

def save_checkpoint(session_start: datetime, last_active: datetime | None = None) -> None:
    """Write session_start and last-active heartbeat to disk each poll cycle.

    Two lines: line 1 = session start, line 2 = last heartbeat (≈ time of last poll).
    Keeping both lets recovery credit only the actually-worked time, not any gap
    that accumulated while the script wasn't running.
    """
    try:
        la = last_active if last_active is not None else session_start
        with open(CHECKPOINT_FILE, "w") as f:
            f.write(session_start.isoformat() + "\n" + la.isoformat())
    except Exception:
        pass


def load_checkpoint() -> tuple[datetime, datetime] | None:
    """
    On startup, read session_start and last_active from the checkpoint file.
    Returns (session_start, last_active) if the checkpoint is from today and
    valid, otherwise None.  Handles the old single-line format gracefully.
    """
    try:
        with open(CHECKPOINT_FILE) as f:
            lines = f.read().strip().splitlines()
        session_start = datetime.fromisoformat(lines[0])
        last_active   = datetime.fromisoformat(lines[1]) if len(lines) > 1 else session_start
        if session_start.date() == date.today() and session_start < datetime.now():
            return session_start, last_active
    except Exception:
        pass
    return None


def clear_checkpoint() -> None:
    try:
        os.remove(CHECKPOINT_FILE)
    except Exception:
        pass


# ── Session helpers ───────────────────────────────────────────────────────────

def close_current_session(now_wall: datetime) -> None:
    """Log the current session and reset session state."""
    with _lock:
        if state["session_active"]:
            start = state["session_start"]
            if start.date() < now_wall.date():
                # Session spans midnight (e.g. woke from overnight sleep).
                # Log it only up to midnight of the day it started; don't
                # add any of that cross-day time to today's counter.
                midnight = datetime.combine(start.date() + timedelta(days=1), datetime.min.time())
                log_session(start, midnight)
                # today_total_secs is left untouched (check_day_rollover already reset it to 0)
            elif start.date() < date.today():
                # now_wall is on a previous day (e.g. sleep-wake closed with idle_since_wall);
                # log the session but don't contaminate today's counter.
                log_session(start, now_wall)
            else:
                duration = (now_wall - start).total_seconds()
                state["today_total_secs"] += duration
                log_session(start, now_wall)
            state["session_active"] = False
    clear_checkpoint()


def start_new_session(now_wall: datetime) -> None:
    with _lock:
        state["session_start"]  = now_wall
        state["session_active"] = True
    save_checkpoint(now_wall, now_wall)


# ── Session editing (add / delete) ────────────────────────────────────────────

def get_all_sessions(limit_days: int = 14) -> list[dict]:
    """Read the CSV and return sessions from the last limit_days days, newest first."""
    if not os.path.exists(LOG_FILE):
        return []
    cutoff = date.today() - timedelta(days=limit_days)
    sessions = []
    with open(LOG_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row_date = date.fromisoformat(row["date"])
            except ValueError:
                continue
            if row_date >= cutoff:
                sessions.append(row)
    sessions.sort(key=lambda s: (s["date"], s["session_start"]), reverse=True)
    return sessions


def check_overlap(target_date: date, start_str: str, end_str: str) -> dict | None:
    """Check if the proposed session overlaps with any existing one on that date.
    Returns the overlapping session dict, or None."""
    new_start = datetime.strptime(start_str, "%H:%M:%S")
    new_end = datetime.strptime(end_str, "%H:%M:%S")

    # Check against logged sessions
    if os.path.exists(LOG_FILE):
        date_str = target_date.strftime("%Y-%m-%d")
        with open(LOG_FILE, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["date"] == date_str:
                    ex_start = datetime.strptime(row["session_start"][:8], "%H:%M:%S")
                    ex_end = datetime.strptime(row["session_end"][:8], "%H:%M:%S")
                    if new_start < ex_end and ex_start < new_end:
                        return row

    # Check against the current active session (not yet logged)
    with _lock:
        if state["session_active"] and state["session_start"].date() == target_date:
            active_start = state["session_start"]
            active_end = datetime.now()
            as_start = datetime.strptime(active_start.strftime("%H:%M:%S"), "%H:%M:%S")
            as_end = datetime.strptime(active_end.strftime("%H:%M:%S"), "%H:%M:%S")
            if new_start < as_end and as_start < new_end:
                return {
                    "date": target_date.strftime("%Y-%m-%d"),
                    "session_start": active_start.strftime("%H:%M:%S"),
                    "session_end": active_end.strftime("%H:%M:%S"),
                    "duration_minutes": str(round(
                        (active_end - active_start).total_seconds() / 60, 1)),
                }

    return None


def add_session_to_log(target_date: date, start_str: str, end_str: str) -> None:
    """Add a manually-created session to the CSV log."""
    ensure_log_file()
    start_dt = datetime.combine(target_date, datetime.strptime(start_str, "%H:%M:%S").time())
    end_dt = datetime.combine(target_date, datetime.strptime(end_str, "%H:%M:%S").time())
    duration_minutes = round((end_dt - start_dt).total_seconds() / 60, 1)

    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            target_date.strftime("%Y-%m-%d"),
            start_str,
            end_str,
            duration_minutes,
        ])

    if target_date == date.today():
        with _lock:
            state["today_total_secs"] += duration_minutes * 60

    print(f"[{time.strftime('%H:%M:%S')}] Manually added session: "
          f"{target_date} {start_str[:5]}–{end_str[:5]} ({duration_minutes} min)")


def delete_session_from_log(target_date_str: str, start_str: str, end_str: str) -> bool:
    """Remove a specific session from the CSV. Returns True if found and deleted."""
    if not os.path.exists(LOG_FILE):
        return False

    rows = []
    deleted_mins = 0.0
    found = False
    with open(LOG_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (not found
                    and row["date"] == target_date_str
                    and row["session_start"] == start_str
                    and row["session_end"] == end_str):
                found = True
                deleted_mins = float(row["duration_minutes"])
                continue
            rows.append(row)

    if found:
        with open(LOG_FILE, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "session_start", "session_end", "duration_minutes"])
            for row in rows:
                writer.writerow([row["date"], row["session_start"],
                                 row["session_end"], row["duration_minutes"]])

        if target_date_str == date.today().strftime("%Y-%m-%d"):
            with _lock:
                state["today_total_secs"] = max(0, state["today_total_secs"] - deleted_mins * 60)

        print(f"[{time.strftime('%H:%M:%S')}] Manually deleted session: "
              f"{target_date_str} {start_str[:5]}–{end_str[:5]}")

    return found


def _add_session_dialog() -> None:
    """Open a tkinter dialog to add a manual session."""
    root = tk.Tk()
    root.title("Add Session")
    root.resizable(False, False)
    root.attributes("-topmost", True)

    w, h = 340, 230
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    frame = ttk.Frame(root, padding=15)
    frame.grid(sticky="nsew")

    ttk.Label(frame, text="Date (YYYY-MM-DD):").grid(row=0, column=0, sticky="w", pady=3)
    date_var = tk.StringVar(value=date.today().strftime("%Y-%m-%d"))
    date_entry = ttk.Entry(frame, textvariable=date_var, width=15)
    date_entry.grid(row=0, column=1, sticky="w", pady=3)

    ttk.Label(frame, text="Start time (HH:MM):").grid(row=1, column=0, sticky="w", pady=3)
    start_var = tk.StringVar()
    start_entry = ttk.Entry(frame, textvariable=start_var, width=15)
    start_entry.grid(row=1, column=1, sticky="w", pady=3)

    ttk.Label(frame, text="End time (HH:MM):").grid(row=2, column=0, sticky="w", pady=3)
    end_var = tk.StringVar()
    end_entry = ttk.Entry(frame, textvariable=end_var, width=15)
    end_entry.grid(row=2, column=1, sticky="w", pady=3)

    status_var = tk.StringVar()
    status_label = ttk.Label(frame, textvariable=status_var, foreground="red", wraplength=300)
    status_label.grid(row=3, column=0, columnspan=2, pady=5)

    def on_add():
        try:
            target = date.fromisoformat(date_var.get().strip())
        except ValueError:
            status_var.set("Invalid date format. Use YYYY-MM-DD.")
            return

        raw_start = start_var.get().strip()
        raw_end = end_var.get().strip()

        try:
            if len(raw_start) == 5:
                raw_start += ":00"
            if len(raw_end) == 5:
                raw_end += ":00"
            s = datetime.strptime(raw_start, "%H:%M:%S")
            e = datetime.strptime(raw_end, "%H:%M:%S")
        except ValueError:
            status_var.set("Invalid time format. Use HH:MM.")
            return

        if e <= s:
            status_var.set("End time must be after start time.")
            return

        duration_min = (e - s).total_seconds() / 60
        if duration_min < 1:
            status_var.set("Session must be at least 1 minute.")
            return

        overlap = check_overlap(target, raw_start, raw_end)
        if overlap:
            status_var.set(
                f"Overlaps with existing session: "
                f"{overlap['session_start'][:5]}–{overlap['session_end'][:5]}")
            return

        add_session_to_log(target, raw_start, raw_end)
        root.destroy()

    btn_frame = ttk.Frame(frame)
    btn_frame.grid(row=4, column=0, columnspan=2, pady=10)
    ttk.Button(btn_frame, text="Add", command=on_add, width=10).pack(side="left", padx=5)
    ttk.Button(btn_frame, text="Cancel", command=root.destroy, width=10).pack(side="left", padx=5)

    start_entry.focus_set()
    root.mainloop()


def _delete_session_dialog() -> None:
    """Open a tkinter dialog to delete a session from the log."""
    sessions = get_all_sessions(limit_days=14)

    root = tk.Tk()
    root.title("Delete Session")
    root.attributes("-topmost", True)

    if not sessions:
        root.withdraw()
        messagebox.showinfo("Delete Session",
                            "No sessions found in the last 14 days.",
                            parent=root)
        root.destroy()
        return

    root.resizable(True, True)
    w, h = 480, 400
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 2
    root.geometry(f"{w}x{h}+{x}+{y}")

    frame = ttk.Frame(root, padding=10)
    frame.grid(sticky="nsew")
    root.grid_rowconfigure(0, weight=1)
    root.grid_columnconfigure(0, weight=1)
    frame.grid_rowconfigure(1, weight=1)
    frame.grid_columnconfigure(0, weight=1)

    ttk.Label(frame, text="Select a session to delete (last 14 days):").grid(
        row=0, column=0, sticky="w", pady=(0, 5))

    list_frame = ttk.Frame(frame)
    list_frame.grid(row=1, column=0, sticky="nsew")
    list_frame.grid_rowconfigure(0, weight=1)
    list_frame.grid_columnconfigure(0, weight=1)

    listbox = tk.Listbox(list_frame, font=("Consolas", 10), selectmode="single")
    scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
    listbox.configure(yscrollcommand=scrollbar.set)
    listbox.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    for s in sessions:
        dur = float(s["duration_minutes"])
        listbox.insert(tk.END,
            f"{s['date']}   {s['session_start'][:5]} – {s['session_end'][:5]}   "
            f"({dur:.0f} min)")

    def on_delete():
        sel = listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        session = sessions[idx]
        confirm = messagebox.askyesno(
            "Confirm Delete",
            f"Delete session?\n\n"
            f"Date: {session['date']}\n"
            f"Time: {session['session_start'][:5]} – {session['session_end'][:5]}\n"
            f"Duration: {session['duration_minutes']} min",
            parent=root,
        )
        if confirm:
            delete_session_from_log(
                session["date"],
                session["session_start"],
                session["session_end"],
            )
            listbox.delete(idx)
            sessions.pop(idx)

    btn_frame = ttk.Frame(frame)
    btn_frame.grid(row=2, column=0, pady=10)
    ttk.Button(btn_frame, text="Delete Selected", command=on_delete, width=15).pack(
        side="left", padx=5)
    ttk.Button(btn_frame, text="Close", command=root.destroy, width=10).pack(
        side="left", padx=5)

    root.mainloop()


def check_day_rollover(now: float) -> None:
    """If it's a new day, reset today's total and discard any stale checkpoint.
    Also resets active_since so cross-midnight monotonic drift can't inflate alerts."""
    with _lock:
        if date.today() != state["log_date"]:
            state["today_total_secs"] = 0.0
            state["log_date"]         = date.today()
            state["active_since"]     = now   # prevent cross-midnight alert accumulation
            state["alerted"]          = False
            state["last_alert_time"]  = None
            # NOTE: idle_since / idle_since_wall are intentionally NOT reset here.
            # Resetting them would erase the real idle start time, causing sessions
            # that span midnight to be logged up to 00:00 instead of the actual
            # idle start — inflating the daily/weekly totals with ghost hours.
    clear_checkpoint()


# ── Tray icon ─────────────────────────────────────────────────────────────────

def make_icon_image(color: str) -> Image.Image:
    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, 60, 60), fill=color)
    return img


def on_reset(icon, item) -> None:
    with _lock:
        state["reset_requested"] = True


def on_pause_resume(icon, item) -> None:
    with _lock:
        state["paused"] = not state["paused"]
    status = "paused" if state["paused"] else "resumed"
    print(f"[{time.strftime('%H:%M:%S')}] Timer {status}.")


def on_show_today(icon, item) -> None:
    with _lock:
        total     = state["today_total_secs"]
        active    = state["session_active"]
        start     = state["session_start"]
    if active and start.date() == date.today():
        total += (datetime.now() - start).total_seconds()
    toaster = WindowsToaster("WindowsTimeTracker")
    toast   = Toast()
    toast.text_fields = [
        f"Today's active time: {format_duration(total)} 📊",
        f"Logged to: {LOG_FILE}"
    ]
    toaster.show_toast(toast)


def on_show_week(icon, item) -> None:
    with _lock:
        today_total = state["today_total_secs"]
        active      = state["session_active"]
        start       = state["session_start"]
    if active and start.date() == date.today():
        today_total += (datetime.now() - start).total_seconds()
    # Week total from CSV + today's in-memory total (today may not be fully flushed yet)
    week_total = get_week_total_from_log() + today_total
    toaster = WindowsToaster("WindowsTimeTracker")
    toast   = Toast()
    mon = date.today() - timedelta(days=date.today().weekday())
    toast.text_fields = [
        f"This week (Mon {mon.strftime('%b %d')}): {format_duration(week_total)} 📅",
        f"Logged to: {LOG_FILE}"
    ]
    toaster.show_toast(toast)


def on_add_session(icon, item) -> None:
    threading.Thread(target=_add_session_dialog, daemon=True).start()


def on_delete_session(icon, item) -> None:
    threading.Thread(target=_delete_session_dialog, daemon=True).start()


def on_report_1w(icon, item) -> None:
    threading.Thread(target=generate_report, args=(1,), daemon=True).start()


def on_report_2w(icon, item) -> None:
    threading.Thread(target=generate_report, args=(2,), daemon=True).start()


def on_quit(icon, item) -> None:
    close_current_session(datetime.now())
    try:
        os.remove(LOCK_FILE)
    except Exception:
        pass
    # Force-exit after a short delay in a daemon thread so we don't hang
    # waiting for icon.stop() to return (pystray can deadlock on Windows).
    def _force_exit():
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=_force_exit, daemon=True).start()
    icon.stop()


def pause_resume_label(item) -> str:
    return "Resume timer" if state["paused"] else "Pause timer"


def today_label(item) -> str:
    with _lock:
        total  = state["today_total_secs"]
        active = state["session_active"]
        start  = state["session_start"]
    if active and start.date() == date.today():
        total += (datetime.now() - start).total_seconds()
    return f"Today: {format_duration(total)}"


def week_label(item) -> str:
    with _lock:
        today_total = state["today_total_secs"]
        active      = state["session_active"]
        start       = state["session_start"]
    if active and start.date() == date.today():
        today_total += (datetime.now() - start).total_seconds()
    week_total = get_week_total_from_log() + today_total
    return f"This week: {format_duration(week_total)}"


def week_label_prefixed(item) -> str:
    return f"↑ {week_label(item)}"


def build_menu():
    return (
        pystray.MenuItem(today_label,          on_show_today),
        pystray.MenuItem(week_label_prefixed,  on_show_week),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Add session\u2026",       on_add_session),
        pystray.MenuItem("Delete session\u2026",    on_delete_session),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Report: last 1 week",  on_report_1w),
        pystray.MenuItem("Report: last 2 weeks", on_report_2w),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Reset timer",        on_reset),
        pystray.MenuItem(pause_resume_label,   on_pause_resume),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit",               on_quit),
    )


def start_tray() -> pystray.Icon:
    menu = pystray.Menu(lambda: build_menu())
    icon = pystray.Icon(
        "WindowsTimeTracker",
        make_icon_image("#4CAF50"),
        "WindowsTimeTracker",
        menu,
    )
    thread = threading.Thread(target=icon.run, daemon=True)
    thread.start()
    return icon


def update_tray_icon(icon, alerted: bool, paused: bool) -> None:
    if paused:
        color = "#9E9E9E"
    elif alerted:
        color = "#F44336"
    else:
        color = "#4CAF50"
    icon.icon = make_icon_image(color)

    with _lock:
        total  = state["today_total_secs"]
        active = state["session_active"]
        start  = state["session_start"]
    if active and start.date() == date.today():
        total += (datetime.now() - start).total_seconds()
    icon.title = f"WindowsTimeTracker — Today: {format_duration(total)}"
    icon.update_menu()  # refresh menu labels (Today / This week) with current values


# ── Keyboard listener ─────────────────────────────────────────────────────────

def _on_key_press(_key) -> None:
    global _last_key_time
    with _lock:
        _last_key_time = time.monotonic()


# ── Sleep / wake detection ────────────────────────────────────────────────────

def _sleep_wake_thread() -> None:
    """
    Creates a hidden Win32 window to receive WM_POWERBROADCAST messages.
    Sets _woke_from_sleep = True whenever the system resumes from sleep.
    """
    global _woke_from_sleep

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == win32con.WM_POWERBROADCAST:
            if wparam == win32con.PBT_APMRESUMEAUTOMATIC:
                # System just woke from sleep
                with _lock:
                    _woke_from_sleep = True
                print(f"[{time.strftime('%H:%M:%S')}] System woke from sleep.")
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    wc            = win32gui.WNDCLASS()
    wc.lpfnWndProc = wnd_proc
    wc.lpszClassName = "WindowsTimeTrackerWatcher"
    wc.hInstance  = win32api.GetModuleHandle(None)
    win32gui.RegisterClass(wc)
    hwnd = win32gui.CreateWindow(
        wc.lpszClassName, "Watcher", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None
    )

    # Pump messages forever
    win32gui.PumpMessages()




def _is_device_in_use(device: str) -> bool:
    """Check Windows CapabilityAccessManager registry for active device usage.
    device should be 'webcam' or 'microphone'.
    When LastUsedTimeStop == 0, the device is currently in use by that app."""
    base = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\{device}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, base) as root:
                for sub in _iter_subkeys(root):
                    if _subkey_device_active(root, sub):
                        return True
        except OSError:
            pass
    return False


def _iter_subkeys(key):
    """Yield subkey names under a registry key."""
    i = 0
    while True:
        try:
            yield winreg.EnumKey(key, i)
            i += 1
        except OSError:
            break


def _subkey_device_active(parent, name: str) -> bool:
    """Return True if *name* (or any child under NonPackaged) is actively using the device."""
    try:
        with winreg.OpenKey(parent, name) as sub:
            # NonPackaged is a container with per-app children
            if name == "NonPackaged":
                for child in _iter_subkeys(sub):
                    if _subkey_device_active(sub, child):
                        return True
                return False
            stop, _ = winreg.QueryValueEx(sub, "LastUsedTimeStop")
            return stop == 0
    except OSError:
        return False


def _device_poll_thread() -> None:
    global _in_call
    while True:
        try:
            result = _is_device_in_use("webcam") or _is_device_in_use("microphone")
        except Exception:
            result = False
        with _lock:
            _in_call = result
        time.sleep(DEVICE_POLL_SECONDS)


# ── Notification ──────────────────────────────────────────────────────────────

def play_alert_sound() -> None:
    """Play a distinct alert sound in a background thread so it doesn't block."""
    def _play():
        if ALERT_SOUND and os.path.exists(ALERT_SOUND):
            winsound.PlaySound(ALERT_SOUND, winsound.SND_FILENAME)
        else:
            # Three rising beeps using the PC speaker — distinct and hard to miss
            for freq, dur in [(600, 150), (800, 150), (1000, 300)]:
                winsound.Beep(freq, dur)
                time.sleep(0.05)
    threading.Thread(target=_play, daemon=True).start()


_interactable_toaster = InteractableWindowsToaster("WindowsTimeTracker")


def send_notification(active_minutes: int, is_repeat: bool) -> None:
    play_alert_sound()
    toast   = Toast()
    toast.duration = ToastDuration.Long
    if is_repeat:
        toast.text_fields = [
            "Still going? Take that break! 🚶",
            f"You've now been sitting for {active_minutes} minutes. "
            "Your body will thank you for a short walk!"
        ]
    else:
        toast.text_fields = [
            "Time for a break! 🧍",
            f"You've been active for {active_minutes} minutes straight. "
            "Step away, stretch, or take a short walk!"
        ]

    def on_reset_clicked(args):
        with _lock:
            state["reset_requested"] = True

    toast.AddAction(ToastButton("Reset timer", "reset"))
    toast.on_activated = on_reset_clicked
    _interactable_toaster.show_toast(toast)


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    global _woke_from_sleep

    disable_quick_edit()

    # ── Single instance check (PID file — won't affect other Python processes) ──
    import psutil
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE) as f:
                old_pid = int(f.read().strip())
            if psutil.pid_exists(old_pid):
                proc = psutil.Process(old_pid)
                # Only block if it's actually our script, not some other Python process
                if any("WindowsTimeTracker" in c for c in proc.cmdline()):
                    print(f"Already running (PID {old_pid}). Exiting.")
                    return
        except Exception:
            pass
    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

    print("WindowsTimeTracker is running.")
    print("  Right-click the system tray icon to see today's total, reset, or pause.")
    print(f"  Daily log saved to: {LOG_FILE}")
    print("  Press Ctrl+C to stop.\n")

    ensure_log_file()

    # Seed today's total from any previous sessions logged today
    with _lock:
        state["today_total_secs"] = get_today_total_from_log()

    # Recover interrupted session from checkpoint if script was stopped mid-session.
    # The checkpoint stores (session_start, last_active_heartbeat).  We credit only
    # the time the script was actually running (last_active - session_start), NOT the
    # gap between last_active and now — that gap was idle/off time and must not be
    # counted as active work.
    MAX_STALE_SECONDS = 2 * 60 * 60  # discard checkpoints whose heartbeat is > 2 h old
    checkpoint = load_checkpoint()
    if checkpoint:
        sess_start, last_active = checkpoint
        gap_secs      = (datetime.now() - last_active).total_seconds()
        unlogged_secs = max(0.0, (last_active - sess_start).total_seconds())
        if sess_start.date() < date.today():
            # Checkpoint is from a previous day — discard it entirely
            clear_checkpoint()
            print(f"[{time.strftime('%H:%M:%S')}] Checkpoint is from {sess_start.strftime('%Y-%m-%d')} "
                  f"— discarding, starting fresh today.")
        elif gap_secs > MAX_STALE_SECONDS:
            # Heartbeat is too old — script was down for a very long time, start fresh
            clear_checkpoint()
            print(f"[{time.strftime('%H:%M:%S')}] Checkpoint heartbeat is "
                  f"{gap_secs/3600:.1f} h old — too stale, starting fresh.")
        elif unlogged_secs >= 60:
            # Log the recovered slice (session_start → last_active) directly to the CSV
            # so it appears in reports, then credit today's total.
            log_session(sess_start, last_active)
            with _lock:
                state["today_total_secs"] += unlogged_secs
            print(f"[{time.strftime('%H:%M:%S')}] Recovered {unlogged_secs/60:.1f} min from checkpoint "
                  f"({sess_start.strftime('%H:%M')} – {last_active.strftime('%H:%M')}); "
                  f"gap since last run: {gap_secs/60:.0f} min (not counted).")
        else:
            # Unlogged slice is < 1 min — not worth logging, just discard
            clear_checkpoint()
            print(f"[{time.strftime('%H:%M:%S')}] Checkpoint unlogged time < 1 min — discarding.")

    kb_listener = keyboard.Listener(on_press=_on_key_press)
    kb_listener.daemon = True
    kb_listener.start()

    dev_thread = threading.Thread(target=_device_poll_thread, daemon=True)
    dev_thread.start()

    sleep_thread = threading.Thread(target=_sleep_wake_thread, daemon=True)
    sleep_thread.start()

    icon           = start_tray()
    last_mouse_pos = pyautogui.position()
    last_wall_time = datetime.now()   # for wall-clock gap / sleep detection

    import signal
    def _shutdown(sig, frame):
        print("\nShutting down...")
        close_current_session(datetime.now())
        try:
            os.remove(LOCK_FILE)
        except Exception:
            pass
        icon.stop()
        os._exit(0)
    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    while True:
        time.sleep(POLL_INTERVAL_SECONDS)

        now      = time.monotonic()
        now_wall = datetime.now()

        # Detect system sleep via wall-clock gap — more reliable than the OS event alone
        # because the OS event can arrive mid-iteration (race) or be missed entirely.
        wall_gap = (now_wall - last_wall_time).total_seconds()
        last_wall_time = now_wall
        if wall_gap > BREAK_THRESHOLD_SECONDS:
            with _lock:
                _woke_from_sleep = True   # force the wake-reset path below

        check_day_rollover(now)

        with _lock:
            paused              = state["paused"]
            reset_requested     = state["reset_requested"]
            last_key            = _last_key_time
            woke                = _woke_from_sleep
            idle_since_wall_snap = state["idle_since_wall"]   # captured for sleep close-time
            if _woke_from_sleep:
                _woke_from_sleep = False

        # ── Wake from sleep — close current session, don't count the sleep gap ──
        if woke:
            # Determine when active work actually ended before sleep.
            # idle_since_wall holds the wall-clock moment activity stopped; adding
            # BREAK_THRESHOLD gives us the time a normal break would have triggered.
            # This prevents crediting hours of sleep/overnight as active work time.
            if idle_since_wall_snap is not None:
                close_time = idle_since_wall_snap + timedelta(seconds=BREAK_THRESHOLD_SECONDS)
                if close_time > now_wall:
                    close_time = now_wall
            else:
                # User was actively working right when the PC went to sleep.
                # Use the approximate time the PC slept (last poll before the gap)
                # instead of the wake-up time, to avoid logging hours of sleep.
                close_time = now_wall - timedelta(seconds=wall_gap)
            print(f"[{time.strftime('%H:%M:%S')}] Resuming after sleep — closing session "
                  f"at {close_time.strftime('%H:%M')} ({close_time.strftime('%Y-%m-%d')}).")
            close_current_session(close_time)
            start_new_session(now_wall)
            with _lock:
                state["active_since"]    = now
                state["idle_since"]      = None
                state["idle_since_wall"] = None
                state["alerted"]         = False
                state["last_alert_time"] = None
            update_tray_icon(icon, alerted=False, paused=paused)
            continue

        # ── Manual reset ──
        if reset_requested:
            print(f"[{time.strftime('%H:%M:%S')}] Timer manually reset.")
            close_current_session(now_wall)
            start_new_session(now_wall)
            with _lock:
                state["active_since"]    = now
                state["idle_since"]      = None
                state["idle_since_wall"] = None
                state["alerted"]         = False
                state["last_alert_time"] = None
                state["reset_requested"] = False
            update_tray_icon(icon, alerted=False, paused=False)
            continue

        if paused:
            update_tray_icon(icon, alerted=state["alerted"], paused=True)
            continue

        # ── Detect activity ──
        current_pos    = pyautogui.position()
        mouse_moved    = (current_pos != last_mouse_pos)
        last_mouse_pos = current_pos
        key_recently   = (now - last_key) < POLL_INTERVAL_SECONDS * 2
        with _lock:
            in_call      = _in_call
        any_activity     = mouse_moved or key_recently or in_call

        with _lock:
            idle_since      = state["idle_since"]
            idle_since_wall = state["idle_since_wall"]

        if any_activity:
            # Coming back from idle?
            if idle_since is not None:
                idle_duration = now - idle_since
                if idle_duration >= BREAK_THRESHOLD_SECONDS:
                    print(f"[{time.strftime('%H:%M:%S')}] Break detected "
                          f"({idle_duration / 60:.1f} min). Resetting.")
                    # Short break (< SHORT_BREAK_MAX_SECONDS): close session BREAK_THRESHOLD_SECONDS
                    # after idle started — the grace period still counts as active time.
                    # Long break (>= SHORT_BREAK_MAX_SECONDS): close session exactly at idle start
                    # so none of the break is credited as active time.
                    if idle_duration < SHORT_BREAK_MAX_SECONDS:
                        break_declared_at = idle_since_wall + timedelta(seconds=BREAK_THRESHOLD_SECONDS)
                    else:
                        break_declared_at = idle_since_wall
                    close_current_session(break_declared_at)
                    start_new_session(now_wall)
                    with _lock:
                        state["active_since"]    = now
                        state["idle_since"]      = None
                        state["idle_since_wall"] = None
                        state["alerted"]         = False
                        state["last_alert_time"] = None
                    update_tray_icon(icon, alerted=False, paused=False)
                    continue
            with _lock:
                state["idle_since"]      = None
                state["idle_since_wall"] = None
        else:
            with _lock:
                if state["idle_since"] is None:
                    state["idle_since"]      = now
                    state["idle_since_wall"] = now_wall

        with _lock:
            idle_since      = state["idle_since"]
            active_since    = state["active_since"]
            alerted         = state["alerted"]
            last_alert_time = state["last_alert_time"]

        idle_duration   = (now - idle_since) if idle_since is not None else 0
        on_a_break      = idle_duration >= BREAK_THRESHOLD_SECONDS
        active_duration = now - active_since

        update_tray_icon(icon, alerted=alerted, paused=False)

        if on_a_break:
            continue

        # Save checkpoint only when active so crash recovery doesn't count break time
        with _lock:
            sess_start_snap = state["session_start"]
        save_checkpoint(sess_start_snap, now_wall)

        # Don't interrupt meetings
        if in_call:
            continue

        if not alerted:
            if active_duration >= ALERT_AFTER_SECONDS:
                active_minutes = int(active_duration // 60)
                print(f"[{time.strftime('%H:%M:%S')}] First alert ({active_minutes} min).")
                send_notification(active_minutes, is_repeat=False)
                with _lock:
                    state["alerted"]         = True
                    state["last_alert_time"] = now
                update_tray_icon(icon, alerted=True, paused=False)
        else:
            if now - last_alert_time >= REPEAT_ALERT_SECONDS:
                active_minutes = int(active_duration // 60)
                print(f"[{time.strftime('%H:%M:%S')}] Repeat alert ({active_minutes} min).")
                send_notification(active_minutes, is_repeat=True)
                with _lock:
                    state["last_alert_time"] = now


if __name__ == "__main__":
    main()