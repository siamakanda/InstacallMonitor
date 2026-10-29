@echo off
REM Build a standalone Windows executable (dist\InstacallMonitor.exe).
setlocal
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
  echo Creating virtual environment...
  python -m venv venv || goto :error
)

echo Installing build dependencies...
venv\Scripts\python.exe -m pip install --upgrade pip >nul || goto :error
venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller || goto :error

echo Building InstacallMonitor.exe ...
venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging\InstacallMonitor.spec || goto :error

echo.
echo Build complete: dist\InstacallMonitor.exe
echo Put a .env file (PORTAL_USERNAME / PORTAL_PASSWORD) next to the exe, then run it.
pause
exit /b 0

:error
echo.
echo Build failed.
pause
exit /b 1
