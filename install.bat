@echo off
REM vysor-pro-patch / install.bat
REM Bootstraps the PowerShell installer next to this file.

setlocal
set PS1=%~dp0install.ps1
if not exist "%PS1%" (
    echo install.ps1 not found next to install.bat - did you copy the whole repo?
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%"
echo.
pause
