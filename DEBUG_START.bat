@echo off
setlocal enabledelayedexpansion
title SuperMart POS - DEBUG MODE
color 0E
cls

echo.
echo  ============================================
echo    SuperMart POS -- DEBUG MODE
echo  ============================================
echo.

cd /d "%~dp0"
echo  [DEBUG] Folder: %CD%
echo.

:: ===== STEP 1: Find Python 3.11 ONLY =====
echo  ----- STEP 1: Finding Python 3.11 -----
set PYCMD=

:: Try py launcher first
py -3.11 --version >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=2" %%i in ('py -3.11 --version 2^>^&1') do set PY311VER=%%i
    echo  [OK] Found via py launcher: Python !PY311VER!
    set PYCMD=py -3.11
    goto :found_python
)

:: Try C:\Python311
if exist "C:\Python311\python.exe" (
    for /f "tokens=2" %%i in ('"C:\Python311\python.exe" --version 2^>^&1') do set PY311VER=%%i
    echo  [OK] Found at C:\Python311: Python !PY311VER!
    set PYCMD=C:\Python311\python.exe
    goto :found_python
)

:: Try LOCALAPPDATA install
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
    for /f "tokens=2" %%i in ('"%LOCALAPPDATA%\Programs\Python\Python311\python.exe" --version 2^>^&1') do set PY311VER=%%i
    echo  [OK] Found at LOCALAPPDATA: Python !PY311VER!
    set PYCMD=%LOCALAPPDATA%\Programs\Python\Python311\python.exe
    goto :found_python
)

:: Check if default python is 3.11
where python >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=2" %%i in ('python --version 2^>^&1') do set DEFVER=%%i
    echo !DEFVER! | findstr /b "3.11" >nul 2>&1
    if not errorlevel 1 (
        set PYCMD=python
        set PY311VER=!DEFVER!
        echo  [OK] Found on PATH: Python !PY311VER!
        goto :found_python
    )
    echo  [FAIL] Found Python !DEFVER! - need Python 3.11!
    echo.
    echo  This debug launcher requires Python 3.11.
    echo  Install Python 3.11 from:
    echo  https://www.python.org/downloads/release/python-31110/
    goto :end
)

echo  [FAIL] No Python found at all!
echo  Install Python 3.11: https://www.python.org/downloads/release/python-31110/
goto :end

:found_python
echo  Using: %PYCMD%
echo.

:: ===== STEP 2: venv =====
echo  ----- STEP 2: Virtual Environment -----
if exist "venv" (
    "venv\Scripts\python.exe" --version >nul 2>&1
    if errorlevel 1 (
        echo  [!] venv broken - deleting...
        rmdir /s /q venv
    ) else (
        for /f "tokens=2" %%i in ('"venv\Scripts\python.exe" --version 2^>^&1') do set VENV_PY=%%i
        echo !VENV_PY! | findstr /b "3.11" >nul 2>&1
        if errorlevel 1 (
            echo  [!] venv is Python !VENV_PY! - need 3.11 - deleting...
            rmdir /s /q venv
        ) else (
            echo  [OK] venv is Python !VENV_PY!
        )
    )
)

if not exist "venv" (
    echo  Creating venv with Python 3.11...
    %PYCMD% -m venv venv
    if errorlevel 1 (
        echo  [FAIL] Could not create venv! Try: Right-click > Run as Administrator
        goto :end
    )
    echo  [OK] venv created
) else (
    echo  [OK] venv exists
)
echo.

:: ===== STEP 3: Activate =====
echo  ----- STEP 3: Activate venv -----
call venv\Scripts\activate.bat
echo  [OK] Activated
echo.

:: ===== STEP 4: Packages =====
echo  ----- STEP 4: Package Install -----
python -c "import flask" >nul 2>&1
if errorlevel 1 (
    echo  Installing packages...
    python -m pip install --upgrade pip --no-warn-script-location
    pip install -r requirements.txt --no-warn-script-location
    if errorlevel 1 (
        echo  [WARNING] Some failed - retrying core packages...
        pip install flask flask-sqlalchemy flask-login python-dotenv werkzeug sqlalchemy requests pillow waitress PySide6 --no-warn-script-location
    )
) else (
    echo  [OK] Flask already installed
)
echo.

:: ===== STEP 5: Verify =====
echo  ----- STEP 5: Verify Imports -----
for %%M in (flask flask_sqlalchemy flask_login dotenv waitress PySide6) do (
    python -c "import %%M; print('  [OK] %%M')" 2>nul || echo  [MISSING] %%M
)
echo.

:: ===== STEP 6: Launch (debug - console stays open) =====
echo  ----- STEP 6: Starting App (DEBUG) -----
echo.

python main.py
set APP_EXIT=!errorlevel!

echo.
echo  App exited with code: !APP_EXIT!

if !APP_EXIT! neq 0 (
    echo.
    echo  Trying browser mode (app.py)...
    timeout /t 2 /nobreak >nul
    start "" http://localhost:5000
    python app.py
)

:end
echo.
echo  ============================================
echo  Press any key to close...
pause >nul
