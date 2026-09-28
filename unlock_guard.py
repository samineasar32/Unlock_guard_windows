"""
Unlock Guard - a second layer of security for your Windows laptop.

What it does:
    - Watches for your laptop being unlocked (correct Windows password
      entered after sleep/lock).
    - The instant that happens, it pops up a full-screen prompt asking
      for a SECRET CODE that only you know.
    - Enter it correctly -> the prompt disappears, nothing else happens.
    - Enter it wrong -> your webcam silently takes a photo (saved with
      the date/time) to a folder you choose, and they can try again.
    - After too many wrong attempts, it shuts down the laptop immediately.

IMPORTANT - be honest with yourself about what this is:
    This is a deterrent + evidence-gathering tool, not military-grade
    security. It runs as a normal background program, so someone who
    knows to open Task Manager and end the process, or boot into Safe
    Mode, can bypass it. It's great for catching a nosy sibling,
    roommate, or classmate - not a determined attacker.

-------------------------------------------------------------------
SETUP (do this once)
-------------------------------------------------------------------
1. Install the required packages:
       pip install opencv-python pywin32

2. Run the one-time setup wizard to choose your secret code and the
   folder where "caught you" photos should be saved:
       python unlock_guard.py --setup

3. Run it for real:
       python unlock_guard.py

   To have it start automatically every time you log in, see the
   "AUTO-START" notes at the bottom of this file.
-------------------------------------------------------------------
"""

import os
import sys
import json
import time
import hashlib
import getpass
import ctypes
from datetime import datetime

import cv2

try:
    import win32gui
    import win32con
    import win32api
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

try:
    import tkinter as tk
    from tkinter import messagebox
    TK_AVAILABLE = True
except ImportError:
    TK_AVAILABLE = False


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "unlock_guard_config.json")
LOG_PATH = os.path.join(BASE_DIR, "unlock_guard_log.txt")

MAX_ATTEMPTS = 2
WTS_SESSION_UNLOCK = 0x8
WTS_SESSION_LOCK = 0x7
WM_WTSSESSION_CHANGE = 0x02B1


# -----------------------------------------------------------------------
# Config: secret code (stored as a salted hash, never in plain text) and
# the photo folder you choose.
# -----------------------------------------------------------------------
def hash_code(code, salt):
    return hashlib.sha256((salt + code).encode("utf-8")).hexdigest()


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return None
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def run_setup_wizard():
    print("=== Unlock Guard setup ===")
    while True:
        code1 = getpass.getpass("Choose a secret code: ")
        code2 = getpass.getpass("Type it again to confirm: ")
        if code1 == code2 and code1.strip():
            break
        print("Codes didn't match (or were empty) - try again.\n")

    folder = input(
        "Folder to save 'caught you' photos in "
        "(e.g. C:\\Users\\you\\UnlockGuardPhotos): "
    ).strip()
    os.makedirs(folder, exist_ok=True)

    salt = os.urandom(16).hex()
    config = {
        "code_hash": hash_code(code1, salt),
        "salt": salt,
        "photo_folder": folder,
        "max_attempts": MAX_ATTEMPTS,
    }
    save_config(config)
    print(f"\nSaved settings to {CONFIG_PATH}")
    print("Run 'python unlock_guard.py' (no arguments) to start protecting your laptop.")


# -----------------------------------------------------------------------
# Logging + photo capture
# -----------------------------------------------------------------------
def log_event(message):
    with open(LOG_PATH, "a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} - {message}\n")


def capture_photo(folder):
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        log_event("Could not open webcam to capture photo.")
        return None
    # Give the camera a moment to adjust exposure before taking the shot
    for _ in range(5):
        cap.read()
        time.sleep(0.05)
    success, frame = cap.read()
    cap.release()
    if not success:
        log_event("Failed to capture a frame from the webcam.")
        return None
    filename = f"unlock_attempt_{datetime.now():%Y%m%d_%H%M%S}.jpg"
    path = os.path.join(folder, filename)
    cv2.imwrite(path, frame)
    return path


# -----------------------------------------------------------------------
# The full-screen challenge window
# -----------------------------------------------------------------------
def show_challenge(config):
    """Blocks until the user enters the correct code or runs out of
    attempts. Returns True if they got it right."""
    result = {"success": False}
    attempts_left = config.get("max_attempts", MAX_ATTEMPTS)

    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.configure(bg="#111111")
    root.overrideredirect(True)  # remove title bar / close button

    label = tk.Label(
        root, text="Enter secret code to continue",
        font=("Segoe UI", 28), fg="white", bg="#111111",
    )
    label.pack(pady=(200, 20))

    status_label = tk.Label(
        root, text="", font=("Segoe UI", 14), fg="#ff5555", bg="#111111",
    )
    status_label.pack(pady=(0, 20))

    entry = tk.Entry(root, show="*", font=("Segoe UI", 20), justify="center")
    entry.pack(ipadx=10, ipady=6)
    entry.focus_set()

    def on_submit(event=None):
        nonlocal attempts_left
        typed = entry.get()
        entry.delete(0, tk.END)
        typed_hash = hash_code(typed, config["salt"])

        if typed_hash == config["code_hash"]:
            result["success"] = True
            log_event("Correct code entered. Access granted.")
            root.destroy()
            return

        attempts_left -= 1
        photo_path = capture_photo(config["photo_folder"])
        log_event(
            f"WRONG code entered. Attempts left: {attempts_left}. "
            f"Photo: {photo_path or 'capture failed'}"
        )

        if attempts_left <= 0:
            status_label.config(text="Too many failed attempts. Shutting down now...")
            root.update()
            log_event("Max attempts exceeded. Shutting down the laptop.")
            os.system('shutdown /s /t 0 /c "Unlock Guard: too many failed code attempts"')
            root.destroy()
            return

        status_label.config(text=f"Wrong code. Photo taken. {attempts_left} attempt(s) left.")

    entry.bind("<Return>", on_submit)
    # Prevent Alt+F4 / Escape from being an easy way out
    root.bind("<Alt-F4>", lambda e: "break")
    root.bind("<Escape>", lambda e: "break")
    root.protocol("WM_DELETE_WINDOW", lambda: None)

    root.mainloop()
    return result["success"]


# -----------------------------------------------------------------------
# Windows session-lock/unlock listener
# -----------------------------------------------------------------------
def wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_WTSSESSION_CHANGE:
        if wparam == WTS_SESSION_UNLOCK:
            log_event("Session unlocked - showing challenge.")
            config = load_config()
            if config:
                show_challenge(config)
        elif wparam == WTS_SESSION_LOCK:
            log_event("Session locked.")
        return 0
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


def main():
    if not WIN32_AVAILABLE:
        print("This script needs 'pywin32'. Install it with: pip install pywin32")
        return
    if not TK_AVAILABLE:
        print("tkinter is required (it usually ships with Python by default).")
        return

    config = load_config()
    if not config:
        print("No configuration found. Run this first:")
        print("    python unlock_guard.py --setup")
        return

    os.makedirs(config["photo_folder"], exist_ok=True)

    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc = wnd_proc
    wc.lpszClassName = "UnlockGuardHiddenWindow"
    class_atom = win32gui.RegisterClass(wc)
    hwnd = win32gui.CreateWindow(
        class_atom, "UnlockGuard", 0, 0, 0, 0, 0, 0, 0, 0, None
    )

    import win32ts
    win32ts.WTSRegisterSessionNotification(hwnd, win32ts.NOTIFY_FOR_THIS_SESSION)

    log_event("Unlock Guard started and watching for unlock events.")
    print("Unlock Guard is running in the background. Close this window to stop it.")
    win32gui.PumpMessages()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--setup":
        run_setup_wizard()
    else:
        main()

# -----------------------------------------------------------------------
# AUTO-START (optional): make this run every time you log in
# -----------------------------------------------------------------------
# Easiest method - Windows Startup folder:
#   1. Press Win+R, type: shell:startup, press Enter.
#   2. Create a shortcut in that folder pointing to:
#         pythonw.exe "C:\full\path\to\unlock_guard.py"
#      (use "pythonw.exe" instead of "python.exe" so no console window
#      pops up on login)
#
# More reliable method - Task Scheduler:
#   1. Open Task Scheduler -> Create Task.
#   2. Trigger: "At log on".
#   3. Action: Start a program -> pythonw.exe, with argument the full
#      path to unlock_guard.py.
#   4. Check "Run whether user is logged on or not" if you want it
#      hidden even more thoroughly.
# -----------------------------------------------------------------------