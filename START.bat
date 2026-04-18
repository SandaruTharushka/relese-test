REM Developer use only — not for customer use
@echo off
setlocal enabledelayedexpansion

title SuperMart POS - Desktop Mode
color 0A
cls

echo.
echo  ============================================
echo    SuperMart POS  v3.1  -- Desktop Mode
echo  ============================================
echo.

cd /d "%~dp0"
set PYCMD=
set PYVER=

REM Prefer Python 3.11 — the required runtime for this application.
REM Python 3.12 is accepted as a fallback.
for %%P in (3.11 3.12) do (
    py -%%P --version >nul 2>&1
    if not errorlevel 1 if not defined PYCMD (
        for /f "tokens=2" %%V in ('py -%%P --version 2^>^&1') do set PYVER=%%V
        set PYCMD=py -%%P
    )
)

if not defined PYCMD (
    where python >nul 2>&1
    if not errorlevel 1 (
        for /f "tokens=2" %%V in ('python --version 2^>^&1') do set PYVER=%%V
        set PYCMD=python
    )
)

if not defined PYCMD (
    echo  [ERROR] Python was not found.
    echo  Install Python 3.11 from https://www.python.org/downloads/release/python-31110/
    goto :end
)

echo  [OK] Using Python !PYVER!

if exist "venv" (
    for /f "tokens=2" %%V in ('"venv\Scripts\python.exe" --version 2^>^&1') do set VENV_PY=%%V
    if /i not "!VENV_PY!"=="!PYVER!" (
        echo  [!] Rebuilding virtual environment for Python !PYVER!...
        rmdir /s /q venv
    )
)

if not exist "venv" (
    echo  [1/3] Creating virtual environment...
    %PYCMD% -m venv venv
    if errorlevel 1 (
        echo  [ERROR] Failed to create the virtual environment.
        goto :end
    )
)

call venv\Scripts\activate.bat

echo  [2/3] Installing desktop dependencies...
python -m pip install --upgrade pip --quiet --no-warn-script-location
pip install -r requirements.txt --quiet --no-warn-script-location
if errorlevel 1 (
    echo  [ERROR] Dependency install failed.
    goto :end
)

echo  [3/3] Starting the desktop application...
echo.
python main.py
set EXIT_CODE=!errorlevel!

if !EXIT_CODE! neq 0 (
    echo.
    echo  [ERROR] SuperMart POS exited with code !EXIT_CODE!.
)

:end
echo.
echo  Press any key to close...
pause >nul
