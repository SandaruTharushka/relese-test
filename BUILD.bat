@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: ============================================================
::  Garage Management System — Master Build Script
::  Produces: dist\GarageManagementSystem.exe + dist\SuperMartPrinterManager.exe
::            + release\GarageManagementSystem_Setup_v3.1.exe  (if Inno Setup found)
::
::  Usage:  BUILD.bat
::
::  What this script does — in order:
::    0. Validate environment (Python, required files, OneDrive warning)
::    1. Create virtual environment if it does not exist
::    2. Upgrade pip and install / refresh requirements.txt
::    3. Call build_desktop.bat  (runs PyInstaller for both EXEs)
::    4. Call build_installer.bat (runs ISCC if Inno Setup is installed)
::    5. Print a clear pass/fail summary
:: ============================================================

title Garage Management System — Full Build
color 0A

echo.
echo  =====================================================
echo    Garage Management System  v3.2  --  Full Build Pipeline
echo  =====================================================
echo.

:: ── STEP 0 — OneDrive / synced-folder detection ──────────────────────────────
echo %CD% | findstr /i "OneDrive" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo  [WARNING] *** ONEDRIVE PATH DETECTED ***
    echo.
    echo  Your project appears to be inside a OneDrive-synced folder:
    echo    %CD%
    echo.
    echo  Building inside OneDrive causes intermittent file-lock failures because
    echo  the OneDrive sync daemon holds read/write locks on files while they are
    echo  being created or modified.  Symptoms include:
    echo    - "Access is denied" when deleting dist\ or build\
    echo    - "The process cannot access the file because it is being used"
    echo    - Random PyInstaller mid-build failures
    echo.
    echo  RECOMMENDATION: Move the project to a local non-synced folder such as:
    echo    C:\Dev\GarageManagementSystem
    echo.
    set /p ONEDRIVE_CONTINUE=" Type YES to continue anyway (not recommended): "
    if /i "!ONEDRIVE_CONTINUE!" neq "YES" (
        echo  Build cancelled. Move the project and try again.
        goto :fail
    )
    echo.
)

:: ── STEP 0 — Python version detection ────────────────────────────────────────
set PYCMD=
set PYVER=

:: Prefer py launcher with explicit version, fall back to 'python'
for %%V in (3.11 3.12 3.10) do (
    if not defined PYCMD (
        py -%%V --version >nul 2>&1
        if not errorlevel 1 (
            for /f "tokens=2" %%W in ('py -%%V --version 2^>^&1') do set PYVER=%%W
            set PYCMD=py -%%V
        )
    )
)
if not defined PYCMD (
    where python >nul 2>&1
    if not errorlevel 1 (
        for /f "tokens=2" %%W in ('python --version 2^>^&1') do set PYVER=%%W
        set PYCMD=python
    )
)
if not defined PYCMD (
    echo.
    echo  [ERROR] Python 3.11 not found.
    echo  Install Python 3.11 from https://www.python.org/downloads/release/python-31110/
    echo  and tick "Add Python to PATH" during installation.
    goto :fail
)

echo  [OK] Python %PYVER% found  (%PYCMD%)

:: Reject Python 2 immediately
echo %PYVER% | findstr /b "2\." >nul 2>&1
if not errorlevel 1 (
    echo  [ERROR] Python 2 is not supported. Install Python 3.11.
    goto :fail
)

:: ── STEP 0 — Required source file check ──────────────────────────────────────
for %%F in (
    main.py
    desktop_runtime.py
    app.py
    runtime_paths.py
    SuperMartPOS.spec
    SuperMartPrinterManager.spec
    requirements.txt
    static\icons\icon.ico
) do (
    if not exist "%%F" (
        echo  [ERROR] Required file missing: %%F
        goto :fail
    )
)
echo  [OK] All required source files present

:: ── STEP 1 — Virtual environment ─────────────────────────────────────────────
if exist "venv\Scripts\python.exe" (
    for /f "tokens=2" %%W in ('"venv\Scripts\python.exe" --version 2^>^&1') do set VENV_PY=%%W
    echo  [OK] Found existing venv  (Python !VENV_PY!)

    :: Rebuild venv if major.minor version changed
    set WANT_MINOR=
    for /f "tokens=1,2 delims=." %%A in ("!PYVER!") do set WANT_MINOR=%%A.%%B
    set HAVE_MINOR=
    for /f "tokens=1,2 delims=." %%A in ("!VENV_PY!") do set HAVE_MINOR=%%A.%%B
    if "!HAVE_MINOR!" neq "!WANT_MINOR!" (
        echo  [!] venv is Python !HAVE_MINOR! but host is !WANT_MINOR! -- rebuilding venv...
        rmdir /s /q venv
    )
)

if not exist "venv\Scripts\python.exe" (
    echo  [1/3] Creating virtual environment...
    %PYCMD% -m venv venv
    if errorlevel 1 (
        echo  [ERROR] Failed to create virtual environment.
        goto :fail
    )
    echo  [OK] Virtual environment created
)

:: ── STEP 2 — Requirements ─────────────────────────────────────────────────────
echo  [2/3] Installing / refreshing requirements...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo  [ERROR] Could not activate virtual environment.
    goto :fail
)

python -m pip install --upgrade pip --quiet --no-warn-script-location
if errorlevel 1 (
    echo  [WARNING] pip upgrade failed — continuing with existing pip.
)

pip install -r requirements.txt --quiet --no-warn-script-location
if errorlevel 1 (
    echo  [ERROR] Requirements install failed.
    echo  Run manually:  venv\Scripts\activate.bat
    echo                 pip install -r requirements.txt
    goto :fail
)

:: Confirm PyInstaller is importable
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] PyInstaller is not installed.  Check requirements.txt contains pyinstaller.
    goto :fail
)
echo  [OK] Requirements installed

:: ── STEP 3 — Desktop EXE build ────────────────────────────────────────────────
echo  [3/3] Building desktop executables...
echo.
call build_desktop.bat
if errorlevel 1 (
    echo.
    echo  [ERROR] Desktop build failed.  See messages above.
    goto :fail
)

:: ── STEP 4 — Installer (optional — only if Inno Setup is present) ─────────────
set ISCC_EXE=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe"      set ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe

if "%ISCC_EXE%"=="" (
    echo.
    echo  [INFO] Inno Setup 6 not found — skipping installer build.
    echo  Install Inno Setup 6 from https://jrsoftware.org/isinfo.php
    echo  then run  build_installer.bat  to build the setup EXE.
) else (
    echo.
    call build_installer.bat
    if errorlevel 1 (
        echo.
        echo  [WARNING] Installer build failed.  Desktop EXE is still available in dist\
        echo  Run  build_installer.bat  separately to diagnose the installer issue.
    )
)

:: ── SUMMARY ───────────────────────────────────────────────────────────────────
echo.
echo  =====================================================
echo    BUILD SUMMARY
echo  =====================================================
if exist "dist\GarageManagementSystem.exe" (
    echo  [PASS] dist\GarageManagementSystem.exe
) else (
    echo  [FAIL] dist\GarageManagementSystem.exe  NOT FOUND
)
if exist "dist\SuperMartPrinterManager.exe" (
    echo  [PASS] dist\SuperMartPrinterManager.exe
) else (
    echo  [WARN] dist\SuperMartPrinterManager.exe  NOT FOUND
)
if exist "release\GarageManagementSystem.exe" (
    echo  [PASS] release\GarageManagementSystem.exe
)
for %%I in (release\GarageManagementSystem_Setup_*.exe) do (
    echo  [PASS] %%I
)
echo.

if not exist "dist\GarageManagementSystem.exe" goto :fail

echo  BUILD COMPLETE
echo.
goto :end

:fail
echo.
echo  *** BUILD FAILED — see messages above ***
echo.
endlocal
exit /b 1

:end
endlocal
exit /b 0
