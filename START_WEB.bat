REM Developer use only — not for customer use
@echo off
setlocal enabledelayedexpansion
title Garage Management System - Browser Mode
color 0A
cls

echo.
echo  ============================================
echo    Garage Management System  v3.2  -- Browser Mode
echo    Python 3.11 Edition
echo  ============================================
echo.

cd /d "%~dp0"

:: Python check
where python >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found! Install from:
    echo  https://www.python.org/downloads/release/python-3119/
    echo  Tick "Add Python to PATH" during install!
    goto :end
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set PYVER=%%i
echo  Found: Python %PYVER%

:: Warn if not 3.11
echo %PYVER% | findstr /b "3.11" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [WARNING] Python %PYVER% detected. Optimised for Python 3.11.
    set /p CONT= Continue anyway? (y/n): 
    if /i "!CONT!" neq "y" goto :end
    echo.
)

:: Virtual environment -- version check and rebuild if needed
if exist "venv" (
    for /f "tokens=2" %%i in ('"venv\Scripts\python.exe" --version 2^>^&1') do set VENV_PY=%%i
    echo !VENV_PY! | findstr /b "3.11" >nul 2>&1
    if errorlevel 1 (
        echo  [!] Old venv was Python !VENV_PY! -- rebuilding for Python %PYVER%...
        rmdir /s /q venv
    ) else (
        echo  [OK] venv is Python !VENV_PY! - compatible
    )
)

if not exist "venv" (
    echo  Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo  [ERROR] Failed to create virtual environment!
        goto :end
    )
)

call venv\Scripts\activate.bat

:: Install packages
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo  Installing packages (takes 2-3 minutes)...
    pip install --upgrade pip --quiet --no-warn-script-location
    pip install -r requirements.txt --quiet --no-warn-script-location
    if errorlevel 1 (
        pip install flask flask-sqlalchemy flask-login python-dotenv werkzeug sqlalchemy requests pillow waitress --quiet --no-warn-script-location
    )
    echo  Done!
) else (
    echo  Packages OK
)

:: Verify Flask
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Flask not installed!
    echo  Try: venv\Scripts\activate.bat then pip install -r requirements.txt
    goto :end
)

:: Start browser mode
echo.
echo  Starting server at http://localhost:5000
echo  First run: create a secure admin account in the setup screen
echo.
echo  Keep this window open while using Garage Management System.
echo  ──────────────────────────────────────────────
echo.
timeout /t 2 /nobreak >nul
start "" http://localhost:5000
python app.py

:end
echo.
echo  Garage Management System stopped.
echo  Press any key to close...
pause >nul
