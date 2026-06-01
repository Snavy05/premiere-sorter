#!/usr/bin/env bash
# SteadyCut — macOS launcher
# Double-click this file (or right-click → Open) to run SteadyCut.
# On first run it creates a local .venv and installs all dependencies.

set -e
cd "$(dirname "$0")"

VENV_DIR=".venv"
PYTHON="python3"

# Ensure Python 3 is available
if ! command -v "$PYTHON" &>/dev/null; then
    echo "ERROR: python3 not found. Install it from https://www.python.org/downloads/"
    read -r -p "Press Enter to exit..."
    exit 1
fi

if [ ! -d "$VENV_DIR" ]; then
    echo ""
    echo "==> First run detected — setting up SteadyCut..."
    echo "    Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"

    echo "    Installing dependencies (this takes ~2–3 min once)..."
    "$VENV_DIR/bin/pip" install --upgrade pip -q
    "$VENV_DIR/bin/pip" install -r requirements.txt

    echo ""
    echo "==> Setup complete. Launching SteadyCut..."
    echo ""
else
    echo "==> Launching SteadyCut..."
fi

"$VENV_DIR/bin/python" run.py
