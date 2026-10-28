@echo off
rem ==========================================================
rem  Desktop Pet Timer - build portable package (onedir)
rem
rem  This file is intentionally pure ASCII.
rem  All paths are resolved from %~dp0 (this script's folder),
rem  so it works no matter where the project is located and
rem  does not depend on the current working directory.
rem ==========================================================
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================================
echo  Desktop Pet Timer - build portable package
echo ==========================================================
echo.

rem ---------- 1. check virtual environment ----------
if not exist ".venv\Scripts\python.exe" goto NO_VENV
set "PY=.venv\Scripts\python.exe"
echo [OK] Interpreter: %PY%

rem ---------- 2. make sure PyInstaller is installed ----------
"%PY%" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 goto INSTALL_PYI
echo [OK] PyInstaller is ready.
goto CLEAN

:INSTALL_PYI
echo [INFO] PyInstaller not found. Installing into .venv ...
"%PY%" -m pip install pyinstaller
if errorlevel 1 goto PYI_FAIL
echo [OK] PyInstaller installed.

:CLEAN
rem ---------- 3. clean previous output ----------
if exist "build" rmdir /s /q "build"
if exist "dist"  rmdir /s /q "dist"
echo [OK] Cleaned old build and dist folders.

rem ---------- 4. build ----------
echo.
echo [INFO] Running PyInstaller, this may take a few minutes ...
echo.
"%PY%" -m PyInstaller build.spec --noconfirm
if errorlevel 1 goto BUILD_FAIL

rem ---------- 5. report result ----------
echo.
echo ==========================================================
echo  BUILD OK
echo ==========================================================
echo  Output folder:
for /d %%D in ("%~dp0dist\*") do echo      %%~fD
echo.
echo  Executable:
for /r "%~dp0dist" %%F in (*.exe) do echo      %%~fF
echo.
echo  Next steps:
echo    1. Quit-path smoke test:
echo         .venv\Scripts\python.exe tools\check_quit.py
echo    2. Copy the whole output folder above to a computer
echo       WITHOUT Python installed, then double-click the .exe.
echo    3. To build the installer:
echo         - GUI: open installer.iss with Inno Setup, press F9
echo         - CLI: "ISCC.exe" installer.iss
echo       Result: dist\Setup.exe
echo.
pause
exit /b 0

:NO_VENV
echo [ERROR] Virtual environment not found:
echo         %~dp0.venv\Scripts\python.exe
echo.
echo Create it first:
echo     python -m venv .venv
echo     .venv\Scripts\python.exe -m pip install PySide6
echo.
pause
exit /b 1

:PYI_FAIL
echo [ERROR] Failed to install PyInstaller. Check your network connection.
pause
exit /b 1

:BUILD_FAIL
echo.
echo [ERROR] Build failed. Read the log above for details.
pause
exit /b 1
