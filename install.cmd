@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1" %*
if errorlevel 1 (
    echo.
    echo Setup did not complete. See the message above and README.md.
    pause
    exit /b 1
)
pause
