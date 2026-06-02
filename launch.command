#!/usr/bin/env bash
# SteadyCut — macOS launcher
# Double-click in Finder to run. Terminal closes automatically after launch.

set -e
cd "$(dirname "$0")"

VENV_DIR=".venv"
PYTHON="python3"

if ! command -v "$PYTHON" &>/dev/null; then
    osascript -e 'display alert "Python 3 not found" message "Install Python 3 from python.org, then try again." as warning' 2>/dev/null || true
    echo "ERROR: python3 not found. Install from https://www.python.org/downloads/"
    read -r -p "Press Enter to exit..."
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo ""
    echo "==> First run — setting up SteadyCut (2-3 min)..."
    echo "    The app will open automatically when done."
    echo ""
    "$PYTHON" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -r requirements.txt
    echo ""
    echo "==> Setup complete — launching SteadyCut..."
    echo ""
fi

# Launch detached from this Terminal so the window can close cleanly.
nohup "$VENV_DIR/bin/python" run.py >>/tmp/steadycut.log 2>&1 &
disown $!

# Brief pause so pywebview has time to start, then close this Terminal window.
sleep 1
osascript -e 'tell application "Terminal" to close (every window whose frontmost is true)' 2>/dev/null || true
exit 0
