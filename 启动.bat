@echo off
cd /d "%~dp0"

rem 开发期启动器：跑最新源码，无控制台窗口。
rem
rem 为什么用 pythonw 而不是 python：
rem   python.exe 是控制台程序，会弹一个黑窗口。用户习惯性把它叉掉，
rem   那等于直接杀掉进程，桌宠就消失了（这不是 bug，是启动方式的问题）。
rem   pythonw 没有控制台，自然不会被误关。
rem
rem 代价：没有控制台就看不到崩溃信息。所以把 stderr 重定向到日志文件，
rem 程序起不来时打开 启动错误.log 看原因。
rem
rem 注意：本文件是 GBK 编码 + CRLF 换行，不要加 chcp 65001 ——
rem   那会让重定向出的 启动错误.log 变成 UTF-8，记事本打开是乱码。

if not exist ".venv\Scripts\pythonw.exe" (
    echo [错误] 找不到 .venv\Scripts\pythonw.exe
    echo         请先创建虚拟环境并安装 PySide6。
    pause
    exit /b 1
)

rem 清空上一次的日志，避免新旧错误混在一起
if exist "启动错误.log" del "启动错误.log"

start "" ".venv\Scripts\pythonw.exe" main.py 2>> "启动错误.log"

rem 立即返回，不占着这个窗口
exit /b 0
