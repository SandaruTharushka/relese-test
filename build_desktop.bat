@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: ============================================================
::  Garage Management System — Desktop EXE Build Script
::  Builds: dist\GarageManagementSystem.exe + dist\SuperMartPrinterManager.exe
::
::  Called by BUILD.bat (which sets up the venv first), but can
::  also be run directly if the venv already exists.
::
::  Hardening against real-world Windows build failures:
::    - Kills stale GarageManagementSystem.exe process before touching dist\
::    - Retries dist\build\ deletion up to 5 times (AV / file-lock)
::    - Detects OneDrive sync folder and warns
::    - Validates Python 3.x venv
::    - Validates all required spec + source files
::    - Copies output to release\ on success
:: ============================================================

title Garage Management System — Desktop EXE Build

echo.
echo  [build_desktop] Building standalone desktop executables...
echo.

:: ── OneDrive detection ────────────────────────────────────────────────────────
echo %CD% | findstr /i "OneDrive" >nul 2>&1
if not errorlevel 1 (
    echo  [WARNING] Build path appears to be inside a OneDrive-synced folder.
    echo  This can cause intermittent file-lock failures during build.
    echo  Consider moving the project to a local non-synced path.
    echo.
)

:: ── Validate virtual environment ─────────────────────────────────────────────
if not exist "venv\Scripts\python.exe" (
    echo  [ERROR] Virtual environment not found.
    echo  Run BUILD.bat to set up the environment, or manually:
    echo    py -3.11 -m venv venv
    echo    venv\Scripts\activate.bat
    echo    pip install -r requirements.txt
    exit /b 1
)

:: Quick sanity check — ensure it is a Python 3 venv
for /f "tokens=2" %%V in ('"venv\Scripts\python.exe" --version 2^>^&1') do set VENV_PYVER=%%V
echo %VENV_PYVER% | findstr /b "3\." >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] venv Python version is unexpected: %VENV_PYVER%
    echo  Delete the venv folder and re-run BUILD.bat.
    exit /b 1
)
echo  [OK] venv Python %VENV_PYVER%

call venv\Scripts\activate.bat
if errorlevel 1 (
    echo  [ERROR] Could not activate virtual environment.
    exit /b 1
)

:: ── Validate required spec and source files ──────────────────────────────────
for %%F in (
    GarageManagementSystem.spec
    SuperMartPrinterManager.spec
    main.py
    desktop_runtime.py
    app.py
    runtime_paths.py
    version.py
    static\icons\icon.ico
) do (
    if not exist "%%F" (
        echo  [ERROR] Required file missing: %%F
        exit /b 1
    )
)
echo  [OK] All required spec and source files present

:: ── Kill stale GarageManagementSystem.exe process ──────────────────────────────────────
::  If a previous build is still running (or the user has dist\GarageManagementSystem.exe
::  open), the dist\ deletion will fail with "Access is denied".
tasklist 2>nul | findstr /i "GarageManagementSystem.exe" >nul 2>&1
if not errorlevel 1 (
    echo  [!] Detected running GarageManagementSystem.exe — terminating before build...
    taskkill /f /im GarageManagementSystem.exe >nul 2>&1
    timeout /t 2 /nobreak >nul
)
tasklist 2>nul | findstr /i "SuperMartPrinterManager.exe" >nul 2>&1
if not errorlevel 1 (
    echo  [!] Detected running SuperMartPrinterManager.exe — terminating...
    taskkill /f /im SuperMartPrinterManager.exe >nul 2>&1
    timeout /t 1 /nobreak >nul
)

:: ── Create release folder ─────────────────────────────────────────────────────
if not exist "release" mkdir release

:: ── Delete old build\ folder (with retry for AV / file-lock) ─────────────────
if exist "build" (
    echo  [..] Removing old build\ folder...
    call :delete_with_retry "build"
    if errorlevel 1 (
        echo  [ERROR] Could not remove build\ folder after retries.
        echo  Close any programs that may be accessing files inside build\
        echo  then re-run this script.
        exit /b 1
    )
)

:: ── Delete old dist\ folder (with retry for AV / file-lock) ──────────────────
if exist "dist" (
    echo  [..] Removing old dist\ folder...
    call :delete_with_retry "dist"
    if errorlevel 1 (
        echo  [ERROR] Could not remove dist\ folder after retries.
        echo  This usually means GarageManagementSystem.exe or SuperMartPrinterManager.exe
        echo  is still running, or your antivirus has a lock on the file.
        echo  Steps to fix:
        echo    1. Close any running Garage Management System instances.
        echo    2. Temporarily disable real-time antivirus scanning for this folder.
        echo    3. Re-run this script.
        exit /b 1
    )
)

:: ── Build GarageManagementSystem.exe ────────────────────────────────────────────────────
echo.
echo  [1/2] Running PyInstaller for GarageManagementSystem...
python -m PyInstaller --clean --noconfirm GarageManagementSystem.spec
if errorlevel 1 (
    echo.
    echo  [ERROR] PyInstaller failed for GarageManagementSystem.spec.
    echo  Common causes:
    echo    - Missing Python package  ^(re-run: pip install -r requirements.txt^)
    echo    - Syntax error in spec file
    echo    - Build path is on OneDrive ^(see warning above^)
    exit /b 1
)
echo  [OK] GarageManagementSystem.exe built successfully

:: ── Build SuperMartPrinterManager.exe ────────────────────────────────────────
echo.
echo  [2/2] Running PyInstaller for SuperMartPrinterManager...
python -m PyInstaller --clean --noconfirm SuperMartPrinterManager.spec
if errorlevel 1 (
    echo.
    echo  [ERROR] PyInstaller failed for SuperMartPrinterManager.spec.
    exit /b 1
)
echo  [OK] SuperMartPrinterManager.exe built successfully

:: ── Verify outputs exist ──────────────────────────────────────────────────────
if not exist "dist\GarageManagementSystem.exe" (
    echo  [ERROR] Build reported success but dist\GarageManagementSystem.exe was not found.
    exit /b 1
)

:: ── Copy to release\ ──────────────────────────────────────────────────────────
copy /y "dist\GarageManagementSystem.exe" "release\GarageManagementSystem.exe" >nul
if exist "dist\SuperMartPrinterManager.exe" (
    copy /y "dist\SuperMartPrinterManager.exe" "release\SuperMartPrinterManager.exe" >nul
)

echo.
echo  [DONE] Desktop executables ready:
echo    dist\GarageManagementSystem.exe
if exist "dist\SuperMartPrinterManager.exe" echo    dist\SuperMartPrinterManager.exe
echo.
exit /b 0


:: ── Subroutine: delete folder with retry ──────────────────────────────────────
:delete_with_retry
:: Usage: call :delete_with_retry "folder"
:: Tries up to 5 times with 2-second waits.
set _DEL_FOLDER=%~1
set _DEL_ATTEMPT=0
:del_retry_loop
set /a _DEL_ATTEMPT+=1
rmdir /s /q "%_DEL_FOLDER%" >nul 2>&1
if not exist "%_DEL_FOLDER%" exit /b 0
if %_DEL_ATTEMPT% geq 5 exit /b 1
echo  [!] Folder %_DEL_FOLDER%\ is locked — retrying in 2 seconds... (attempt %_DEL_ATTEMPT%/5)
timeout /t 2 /nobreak >nul
goto :del_retry_loop
