@echo off
cd /d "%~dp0"
echo   InstacallMonitor
echo   Real-time balance + margin monitoring
echo.

if not exist "venv\Scripts\python.exe" (
    echo   [!] Virtual environment not found.
    echo   Run setup_and_run.bat first to install.
    pause
    exit /b 1
)

venv\Scripts\python.exe monitor.py %*
pause