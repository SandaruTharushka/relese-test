@echo off
title Clear Virtual Environment
cls
echo.
echo  This will delete the existing virtual environment.
echo  Run this if you switched Python versions (e.g. 3.13 to 3.11).
echo  START.bat will recreate it automatically.
echo.
set /p CONFIRM= Are you sure? (y/n): 
if /i "%CONFIRM%" neq "y" (
    echo  Cancelled.
    goto :end
)
if exist "venv" (
    rmdir /s /q venv
    echo  [OK] Virtual environment deleted.
    echo  Run START.bat to recreate it with Python 3.11.
) else (
    echo  [INFO] No venv folder found - nothing to delete.
)
:end
echo.
pause
