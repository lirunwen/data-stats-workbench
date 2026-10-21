@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo DataStats Workbench - local diagnostics
echo.
if not exist "%~dp0runtime\python\python.exe" (
  echo [ERROR] runtime\python\python.exe not found
  pause
  exit /b 1
)
"%~dp0runtime\python\python.exe" -c "import sys, sqlite3, flask, waitress, openpyxl, xlrd, oxmsg; print('Python', sys.version); print('SQLite', sqlite3.sqlite_version); print('dependency check passed')"
echo.
echo Data folder: %~dp0data
if not exist "%~dp0program\active.txt" echo [INFO] program\active.txt not found
type "%~dp0program\active.txt" 2>nul
echo.
pause
