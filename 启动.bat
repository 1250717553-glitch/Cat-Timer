@echo off
cd /d "%~dp0"

rem 测试期专用启动器：直接跑开发版，不安装、不打包。
rem 每次改完代码双击这个就能看到最新效果。

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 找不到 .venv，请先创建虚拟环境并安装 PySide6。
    pause
    exit /b 1
)

echo 正在启动 桌面宠物计时器（开发版）...
".venv\Scripts\python.exe" main.py
if errorlevel 1 (
    echo.
    echo [程序异常退出] 上面是错误信息，按任意键关闭。
    pause
)
