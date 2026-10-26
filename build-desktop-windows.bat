@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-desktop-windows.ps1"
if errorlevel 1 (
  echo.
  echo Desktop build failed. Read the error message above.
  pause
  exit /b 1
)
echo.
echo CreatorFlow desktop build finished.
pause
