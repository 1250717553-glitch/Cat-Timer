@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 桌面宠物计时器 · Windows 启动器（源码版）
rem
rem 与 启动.bat 的区别：
rem   启动.bat     给已经有 .venv 的开发者用，秒开
rem   启动程序.bat  给什么都没装的新用户用，会自动创建环境
rem
rem 这个文件是发给别人用的那份。

set "VENV=.venv"
set "PY=%VENV%\Scripts\python.exe"

if not exist "%PY%" (
    echo 首次运行：正在创建 Python 虚拟环境...
    where python >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [错误] 没有找到 Python。
        echo 请先从 https://www.python.org/downloads/ 安装 Python 3.10 以上版本，
        echo 安装时务必勾选 "Add Python to PATH"，然后重新双击本文件。
        echo.
        pause
        exit /b 1
    )
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败。
        pause
        exit /b 1
    )
)

"%PY%" -c "import PySide6" >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖（PySide6 约 100MB，请耐心等待）...
    "%PY%" -m pip install --upgrade pip --quiet
    "%PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络。
        pause
        exit /b 1
    )
)

if exist "启动错误.log" del "启动错误.log"
start "" "%PY%" main.py 2>> "启动错误.log"
exit /b 0
