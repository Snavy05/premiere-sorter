@echo off
:: SteadyCut — Windows setup + launcher
:: Used directly (shows cmd window) or called by SteadyCut.vbs for first-run setup.

cd /d "%~dp0"
set VENV_DIR=.venv

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python not found. Install from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo.
    echo =^> First run -- setting up SteadyCut ^(2-3 min^)...
    echo.
    python -m venv %VENV_DIR%
    %VENV_DIR%\Scripts\pip install --upgrade pip -q
    %VENV_DIR%\Scripts\pip install -r requirements.txt
    echo.
    echo =^> Setup complete!
    echo.
)

:: pythonw.exe launches without keeping a cmd window open
start "" "%VENV_DIR%\Scripts\pythonw.exe" run.py
