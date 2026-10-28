@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo ==========================================
echo  Running unit tests
echo  Interpreter: %PY%
echo ==========================================
echo.

%PY% -m unittest discover -s tests -v

echo.
echo ==========================================
echo  Done. Press any key to close this window.
echo ==========================================
pause >nul
