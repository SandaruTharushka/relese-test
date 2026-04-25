@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: ============================================================
::  SuperMart POS — Windows Installer Build Script
::  Builds: release\SuperMartPOS_Setup_v3.1.exe
::
::  Requires:
::    - dist\SuperMartPOS.exe  (built by build_desktop.bat)
::    - Inno Setup 6           (installed on build machine)
::    - SuperMartPOS_Setup.iss (root-level; SourceDir = project root)
::
::  Note: This script calls SuperMartPOS_Setup.iss from the PROJECT ROOT,
::  not from the installer\ subfolder.  The root .iss has correct relative
::  paths for dist\, static\, and release\.  The installer\ subfolder copy
::  is kept for reference only — it uses SourceDir=..\ to compensate.
:: ============================================================

title SuperMart POS — Installer Build

echo.
echo  [build_installer] Building Windows installer...
echo.

:: ── Validate dist\SuperMartPOS.exe ───────────────────────────────────────────
if not exist "dist\SuperMartPOS.exe" (
    echo  [INFO] dist\SuperMartPOS.exe not found — building desktop EXE first...
    echo.
    call build_desktop.bat
    if errorlevel 1 (
        echo  [ERROR] Desktop build failed. Fix the desktop build before building the installer.
        exit /b 1
    )
)

if not exist "dist\SuperMartPOS.exe" (
    echo  [ERROR] dist\SuperMartPOS.exe still missing after desktop build attempt.
    exit /b 1
)
echo  [OK] dist\SuperMartPOS.exe found

:: ── Validate installer source files ──────────────────────────────────────────
if not exist "SuperMartPOS_Setup.iss" (
    echo  [ERROR] SuperMartPOS_Setup.iss not found in project root.
    echo  The installer script must be at: %CD%\SuperMartPOS_Setup.iss
    exit /b 1
)
if not exist "static\icons\icon.ico" (
    echo  [ERROR] static\icons\icon.ico not found.
    echo  The setup icon must exist before building the installer.
    exit /b 1
)
echo  [OK] Installer source files validated

:: ── Ensure release\ folder exists ────────────────────────────────────────────
if not exist "release" mkdir release

:: ── Locate Inno Setup 6 compiler ─────────────────────────────────────────────
set ISCC_EXE=

:: Standard 64-bit Program Files location
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    set "ISCC_EXE=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
)
:: 64-bit-only install (less common)
if "%ISCC_EXE%"=="" if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" (
    set "ISCC_EXE=%ProgramFiles%\Inno Setup 6\ISCC.exe"
)
:: Chocolatey / scoop installs sometimes land here
if "%ISCC_EXE%"=="" if exist "%SystemDrive%\tools\innosetup6\ISCC.exe" (
    set "ISCC_EXE=%SystemDrive%\tools\innosetup6\ISCC.exe"
)

if "%ISCC_EXE%"=="" (
    echo.
    echo  [ERROR] Inno Setup 6 compiler ^(ISCC.exe^) was not found.
    echo.
    echo  To install Inno Setup 6:
    echo    1. Download from  https://jrsoftware.org/isinfo.php
    echo    2. Run the installer ^(ISCC.exe will be placed in Program Files ^(x86^)\Inno Setup 6\^)
    echo    3. Re-run this script.
    echo.
    exit /b 1
)
echo  [OK] Inno Setup 6 found: %ISCC_EXE%

:: ── Run ISCC ──────────────────────────────────────────────────────────────────
::  We call the ROOT-level SuperMartPOS_Setup.iss so that all relative paths
::  (dist\, static\, release\) resolve correctly from the project root.
echo  [..] Compiling installer...
echo.
"%ISCC_EXE%" "SuperMartPOS_Setup.iss"
if errorlevel 1 (
    echo.
    echo  [ERROR] Inno Setup compilation failed.
    echo  Common causes:
    echo    - A file listed in [Files] section was not found ^(check dist\ contents^)
    echo    - VersionInfoVersion has invalid format ^(must be N.N.N.N^)
    echo    - SetupIconFile path is wrong
    echo  Check the ISCC output above for the exact error.
    exit /b 1
)

:: ── Verify output ─────────────────────────────────────────────────────────────
set FOUND_INSTALLER=
for %%I in ("release\SuperMartPOS_Setup_*.exe") do set FOUND_INSTALLER=%%I
if not defined FOUND_INSTALLER (
    echo  [ERROR] Installer built without error but no setup EXE found in release\
    exit /b 1
)

echo.
echo  [DONE] Installer created:
for %%I in ("release\SuperMartPOS_Setup_*.exe") do echo    %%I
echo.
exit /b 0
