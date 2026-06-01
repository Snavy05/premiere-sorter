@echo off
:: SteadyCut — Windows launcher
:: Double-click this file to run SteadyCut.
:: On first run it creates a local .venv and installs all dependencies.

cd /d "%~dp0"
set VENV_DIR=.venv

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python not found. Install it from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo.
    echo =^> First run detected -- setting up SteadyCut...
    echo    Creating virtual environment...
    python -m venv %VENV_DIR%

    echo    Installing dependencies (this takes ~2-3 min once)...
    %VENV_DIR%\Scripts\pip install --upgrade pip -q
    %VENV_DIR%\Scripts\pip install -r requirements.txt

    echo.
    echo =^> Setup complete. Launching SteadyCut...
    echo.
) else (
    echo =^> Launching SteadyCut...
)

%VENV_DIR%\Scripts\python run.py
if errorlevel 1 pause
