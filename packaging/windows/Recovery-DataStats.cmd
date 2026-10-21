@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0runtime\python\python.exe" (
  echo [ERROR] Runtime not found. Please extract the complete package again.
  pause
  exit /b 1
)
"%~dp0runtime\python\python.exe" "%~dp0recovery.py"
echo.
pause
