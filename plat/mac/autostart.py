"""开机自启（macOS 实现，写 LaunchAgents plist）。

macOS 的开机自启有三条路：
    LaunchAgents   用户级，~Library/LaunchAgents/*.plist —— 本项目用这个
    LaunchDaemons  系统级，需要 root —— 不用
    Login Items    老 API，用户体验好但无法从代码里可靠地增删 —— 不用

LaunchAgents 的优点：纯文件写，不需要特权，卸载就是删文件。

【与 Windows 版相同的约束】启动命令必须区分开发期与打包后：
    打包后（PyInstaller .app）：命令是 .app 里的可执行文件
    开发期：命令是 python + main.py 绝对路径
写错会导致自启静默失效（plist 里指向一个不存在的路径）。
"""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

# LaunchAgent 的标识，必须唯一。用它拼出 plist 文件名。
LABEL = "com.cat-timer.desktop-pet-timer"

# plist 存放目录（用户级，无需 sudo）
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def _plist_path() -> Path:
    """plist 文件的完整路径。"""
    return AGENTS_DIR / f"{LABEL}.plist"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出来的程序里。"""
    return bool(getattr(sys, "frozen", False))


def main_script() -> Path:
    """开发期 main.py 的绝对路径。接口与 Windows 版一致。"""
    return Path(os.path.abspath(__file__)).resolve().parents[2] / "main.py"


def launch_target() -> Path:
    """自启真正要启动的目标。接口与 Windows 版一致。

    - 打包后：.app 里的可执行文件
    - 开发期：main.py
    """
    if is_frozen():
        return Path(sys.executable)
    return main_script()


def build_command() -> str:
    """构造启动命令行（供展示与排查用）。接口与 Windows 版一致。

    真正的启动参数是 ProgramArguments 数组，由 build_arguments() 产出；
    这个字符串只用于日志/菜单提示，不参与 plist 写入。
    """
    if is_frozen():
        return f'"{sys.executable}"'
    return f'"{sys.executable}" "{main_script()}"'


def build_arguments() -> list[str]:
    """plist 的 ProgramArguments 数组。这才是真正被执行的启动参数。"""
    if is_frozen():
        return [str(Path(sys.executable).resolve())]
    return [str(Path(sys.executable).resolve()), str(main_script())]


def is_enabled() -> bool:
    """当前是否已开启开机自启。"""
    if sys.platform != "darwin":
        return False
    return _plist_path().is_file()


def enable() -> bool:
    """开启开机自启。返回是否成功（失败绝不抛异常）。"""
    if sys.platform != "darwin":
        return False
    try:
        AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": LABEL,
            "ProgramArguments": build_arguments(),
            "RunAtLoad": True,
            # 崩溃退出后不自动重启：这是「开机启动一次」，
            # 不是「守护进程」，无限重启会让用户没法关掉它。
            "KeepAlive": False,
            # 不显示在 Dock —— 桌宠是常驻托盘程序
            "LSUIElement": True,
        }
        with open(_plist_path(), "wb") as f:
            plistlib.dump(payload, f)
        return True
    except (OSError, TypeError):
        return False


def disable() -> bool:
    """关闭开机自启（删 plist）。返回是否成功。"""
    if sys.platform != "darwin":
        return False
    try:
        _plist_path().unlink()
        return True
    except FileNotFoundError:
        return True  # 本来就没有 = 已经是关闭状态
    except OSError:
        return False


def toggle() -> bool:
    """切换开机自启，返回切换后的状态。"""
    if is_enabled():
        disable()
    else:
        enable()
    return is_enabled()
