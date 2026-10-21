@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
if errorlevel 1 (
  echo.
  echo Installation did not complete. Run Diagnostics.cmd to diagnose.
  pause
  exit /b 1
)
exit /b 0
