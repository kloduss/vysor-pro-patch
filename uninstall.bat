@echo off
REM vysor-pro-patch / uninstall.bat
REM Bootstraps the PowerShell uninstaller next to this file.

setlocal
set PS1=%~dp0uninstall.ps1
if not exist "%PS1%" (
    echo uninstall.ps1 not found next to uninstall.bat - did you copy the whole repo?
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
echo.
pause
