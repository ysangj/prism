@echo off
rem ===========================================================================
rem Prism launcher (Windows)
rem ---------------------------------------------------------------------------
rem One command to bootstrap and run Prism:
rem   1. Find a Python 3.11+ interpreter
rem   2. Create (or reuse) a local virtualenv at .\.venv
rem   3. Install/sync dependencies from requirements.txt
rem   4. Launch the Streamlit app at http://localhost:8501
rem
rem Usage:
rem   run.bat                          launch on the default port (8501)
rem   run.bat -- --server.port 8600    pass extra args through to streamlit
rem
rem Optional API keys (FRED live Treasury curve, Anthropic) are NOT required for
rem demo mode. If you have them, put them in a .env file at the repo root, e.g.:
rem   FRED_API_KEY=...
rem   ANTHROPIC_API_KEY=...
rem This script never echoes or requires any key.
rem ===========================================================================

setlocal enableextensions enabledelayedexpansion

rem --- Run from the repo root (where this script lives), regardless of CWD.
cd /d "%~dp0"

set "VENV_DIR=.venv"
set "REQ_FILE=requirements.txt"
set "APP_FILE=app.py"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "APP_URL=http://localhost:8501"

rem --- 1. Locate a suitable Python interpreter (3.11+). ----------------------
rem Verify the version by actually executing the interpreter. Try the Windows
rem launcher (py -3) first, then bare python.
set "PY="

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,11) else 1)" >nul 2>&1
    if !ERRORLEVEL! EQU 0 set "PY=py -3"
)

if not defined PY (
    where python >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        python -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,11) else 1)" >nul 2>&1
        if !ERRORLEVEL! EQU 0 set "PY=python"
    )
)

if not defined PY (
    echo ERROR: No suitable Python interpreter found.>&2
    echo Prism requires Python 3.11+ ^(e.g. installed via the python.org installer^).>&2
    echo Install it from: https://www.python.org/downloads/>&2
    echo After installing, re-run: run.bat>&2
    exit /b 1
)

for /f "delims=" %%V in ('%PY% -c "import sys; print('%%d.%%d.%%d' %% sys.version_info[:3])"') do set "PY_VER=%%V"
echo Using Python %PY_VER% ^(%PY%^)

rem --- 2. Create the venv if missing, reuse if present. ----------------------
if not exist "%VENV_PY%" (
    echo Creating virtualenv at %VENV_DIR% ...
    %PY% -m venv "%VENV_DIR%"
    if !ERRORLEVEL! NEQ 0 (
        echo ERROR: Failed to create virtualenv at %VENV_DIR%.>&2
        exit /b 1
    )
) else (
    echo Reusing existing virtualenv at %VENV_DIR%
)

if not exist "%VENV_PY%" (
    echo ERROR: Virtualenv python not found at %VENV_PY% after creation.>&2
    exit /b 1
)

rem --- 3. Install/sync dependencies. -----------------------------------------
rem Lightweight optimization: skip pip install when requirements.txt is
rem unchanged since the last successful install (hash sentinel). A fresh
rem checkout has no sentinel, so it always installs everything first.
set "SENTINEL=%VENV_DIR%\.deps-installed"
set "REQ_HASH="
for /f "skip=1 tokens=* delims=" %%H in ('certutil -hashfile "%REQ_FILE%" SHA256 2^>nul') do (
    if not defined REQ_HASH set "REQ_HASH=%%H"
)
rem certutil prints with spaces on some locales; strip them.
set "REQ_HASH=%REQ_HASH: =%"

set "SKIP_INSTALL="
if defined REQ_HASH if exist "%SENTINEL%" (
    set /p SAVED_HASH=<"%SENTINEL%"
    if "!SAVED_HASH!"=="%REQ_HASH%" set "SKIP_INSTALL=1"
)

if defined SKIP_INSTALL (
    echo Dependencies already up to date ^(requirements.txt unchanged^).
) else (
    echo Installing dependencies ...
    "%VENV_PY%" -m pip install --upgrade pip >nul
    if !ERRORLEVEL! NEQ 0 (
        echo ERROR: Failed to upgrade pip.>&2
        exit /b 1
    )
    "%VENV_PY%" -m pip install -r "%REQ_FILE%"
    if !ERRORLEVEL! NEQ 0 (
        echo ERROR: Failed to install dependencies from %REQ_FILE%.>&2
        exit /b 1
    )
    if defined REQ_HASH (
        >"%SENTINEL%" echo %REQ_HASH%
    )
)

rem --- 4. Launch the app. ----------------------------------------------------
rem Strip a leading "--" separator so users can do:
rem   run.bat -- --server.port 8600
set "ARGS=%*"
if "%~1"=="--" (
    set "ARGS="
    shift
    :shiftloop
    if "%~1"=="" goto shiftdone
    set "ARGS=!ARGS! %1"
    shift
    goto shiftloop
    :shiftdone
)

echo.
echo Starting Prism at %APP_URL% ...
echo (Press Ctrl-C to stop.)

rem Open the browser shortly after launch (best effort). Streamlit runs
rem headless so it never blocks on the first-run email prompt; we open the
rem URL ourselves to preserve the desktop UX. Anonymous usage stats disabled.
start "" cmd /c "timeout /t 3 >nul & start """" %APP_URL%"

"%VENV_PY%" -m streamlit run "%APP_FILE%" --server.headless true --browser.gatherUsageStats false %ARGS%
exit /b %ERRORLEVEL%
