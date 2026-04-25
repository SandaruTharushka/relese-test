@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

title SuperMart POS — Automated GitHub Release
color 0B

echo.
echo  =====================================================
echo    SuperMart POS  v3.1  --  GitHub Release Tool
echo  =====================================================
echo.

:: Check for Git
where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git is not installed or not in PATH.
    goto :fail
)

:: Check for GITHUB_TOKEN
if "%GITHUB_TOKEN%"=="" (
    echo [WARNING] GITHUB_TOKEN environment variable is not set.
    set /p GITHUB_TOKEN=" Enter your GitHub Personal Access Token (PAT): "
    if "!GITHUB_TOKEN!"=="" (
        echo [ERROR] GITHUB_TOKEN is required for release automation.
        goto :fail
    )
)

:: Get version tag
set /p VERSION_TAG=" Enter version tag (e.g. v3.1.0): "
if "%VERSION_TAG%"=="" (
    echo [ERROR] Version tag is required.
    goto :fail
)

:: ── STEP 1 — Build Executables ───────────────────────────────────────────────
echo.
echo  [1/4] Building executables...
call BUILD.bat
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. Release cancelled.
    goto :fail
)

:: ── STEP 2 — Push Source Code ────────────────────────────────────────────────
echo.
echo  [2/4] Pushing source code to GitHub...
git add .
git commit -m "Prepare release %VERSION_TAG%"
git push origin main
if errorlevel 1 (
    echo [WARNING] Push failed. Attempting to continue anyway...
)

:: ── STEP 3 — Create and Push Tag ─────────────────────────────────────────────
echo.
echo  [3/4] Tagging version %VERSION_TAG%...
git tag -a %VERSION_TAG% -m "Version %VERSION_TAG%"
git push origin %VERSION_TAG%
if errorlevel 1 (
    echo [ERROR] Failed to push tag %VERSION_TAG%.
    goto :fail
)

:: ── STEP 4 — Upload to GitHub Release ───────────────────────────────────────
echo.
echo  [4/4] Creating GitHub Release and uploading assets...
:: Find the setup EXE dynamically
set SETUP_EXE=
for %%F in (release\SuperMartPOS_Setup_*.exe) do set SETUP_EXE=%%F

if exist "venv\Scripts\python.exe" (
    venv\Scripts\python.exe github_release.py %VERSION_TAG% release\SuperMartPOS.exe release\SuperMartPrinterManager.exe !SETUP_EXE!
) else (
    python github_release.py %VERSION_TAG% release\SuperMartPOS.exe release\SuperMartPrinterManager.exe !SETUP_EXE!
)

if errorlevel 1 (
    echo.
    echo [ERROR] GitHub Release upload failed.
    goto :fail
)

echo.
echo  =====================================================
echo    RELEASE COMPLETE: %VERSION_TAG%
echo  =====================================================
echo.
pause
goto :end

:fail
echo.
echo  *** RELEASE FAILED ***
echo.
pause
exit /b 1

:end
endlocal
exit /b 0
