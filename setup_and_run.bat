@echo off
SETLOCAL EnableDelayedExpansion

echo ============================================================
echo      InstacallMonitor - Automated Setup and Run
echo ============================================================

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Error: Python is not installed or not added to your system PATH.
    echo Please install Python 3.10 or later before running this script.
    pause
    exit /b
)

if not exist .env (
    if exist .env.example (
        echo [+] Creating .env file from .env.example...
        copy .env.example .env >nul
        echo [!] ACTION REQUIRED: Edit .env and add your portal credentials.
        echo     PORTAL_USERNAME="your_username"
        echo     PORTAL_PASSWORD="your_password"
    ) else (
        echo [!] .env.example not found. Create .env with:
        echo     PORTAL_USERNAME="your_username"
        echo     PORTAL_PASSWORD="your_password"
    )
)

if not exist venv (
    echo [+] Creating Python virtual environment...
    python -m venv venv
) else (
    echo [+] Virtual environment already exists.
)

echo [+] Activating virtual environment...
call venv\Scripts\activate

if exist requirements.txt (
    echo [+] Installing dependencies...
    venv\Scripts\python.exe -m pip install -r requirements.txt
)

echo ============================================================
echo [+] Setup complete. Launching InstacallMonitor...
echo ============================================================
echo.
echo   Controls:
echo     Ctrl+C to stop  |  --help for options
echo.
venv\Scripts\python.exe monitor.py

pause