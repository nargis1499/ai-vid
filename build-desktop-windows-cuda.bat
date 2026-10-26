@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-desktop-windows.ps1" -Cuda
if errorlevel 1 (
  echo.
  echo CUDA desktop build failed. Read the error message above.
  pause
  exit /b 1
)
echo.
echo CreatorFlow CUDA desktop build finished.
pause
