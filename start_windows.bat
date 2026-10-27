@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo OSCam Patch Manager - Windows Start
echo ========================================
echo.

set "PYTHON_EXE="

REM ----------------------------------------
REM Suche eine installierte Python-Version
REM ----------------------------------------

echo Suche nach einer installierten Python-Version ...
echo.

REM 1. Python Launcher verwenden (beliebige Version)
where py >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%P in ('py -c "import sys; print(sys.executable)" 2^>nul') do (
        set "PYTHON_EXE=%%P"
    )
)

REM 2. Fallback: python.exe
if not defined PYTHON_EXE (
    for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)" 2^>nul') do (
        set "PYTHON_EXE=%%P"
    )
)

REM 3. Fallback: python3.exe
if not defined PYTHON_EXE (
    for /f "delims=" %%P in ('python3 -c "import sys; print(sys.executable)" 2^>nul') do (
        set "PYTHON_EXE=%%P"
    )
)

REM ----------------------------------------
REM Python gefunden?
REM ----------------------------------------

if not defined PYTHON_EXE (
    echo FEHLER: Es wurde keine Python-Installation gefunden.
    echo.
    echo Bitte Python installieren und zum PATH hinzufuegen.
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Verwendetes Python:
echo "%PYTHON_EXE%"
"%PYTHON_EXE%" --version
echo.

REM ----------------------------------------
REM Syntaxpruefung
REM ----------------------------------------

echo Pruefe Syntax ...
"%PYTHON_EXE%" -m py_compile main.py startup.py gui.py

if errorlevel 1 (
    echo.
    echo FEHLER: Syntaxpruefung fehlgeschlagen.
    echo Bitte Fehlermeldung oben pruefen.
    pause
    exit /b 1
)

REM ----------------------------------------
REM Programm starten
REM ----------------------------------------

echo.
echo Starte OSCam Patch Manager ...
echo.

"%PYTHON_EXE%" main.py

if errorlevel 1 (
    echo.
    echo FEHLER beim Start. Siehe Fehlermeldung oben.
    pause
)

endlocal
exit /b
