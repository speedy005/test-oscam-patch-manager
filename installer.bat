@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
    py -3 installer.py
    exit /b %errorlevel%
)

where python >nul 2>nul
if not errorlevel 1 (
    python installer.py
    exit /b %errorlevel%
)

echo Python 3.9 or newer was not found.
echo Please install Python and enable it in PATH.
echo Python 3.9 oder neuer wurde nicht gefunden.
pause
exit /b 1
