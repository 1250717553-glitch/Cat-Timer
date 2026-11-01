"""开机自启（Windows 注册表实现）。

写的是 HKEY_CURRENT_USER\\...\\Run，**只对当前用户生效，不需要管理员权限**。

【关键】启动命令行必须区分「开发期」与「打包后」：
  - 打包后（PyInstaller）：直接执行 exe 本身（sys.executable 就是 exe 路径）
  - 开发期：用 python.exe 执行 main.py 的绝对路径
两者搞混会导致开机自启静默失效（注册表里写的路径根本不存在）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import winreg  # 仅 Windows 可用
except ImportError:  # pragma: no cover - 非 Windows 平台兜底
    winreg = None  # type: ignore[assignment]

# 注册表位置与值名
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "桌面宠物计时器"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出来的程序里。"""
    return bool(getattr(sys, "frozen", False))


def main_script() -> Path:
    """开发期 main.py 的绝对路径。

    打包后这个路径没有意义（代码在归档里，没有 .py 文件），
    所以只在非 frozen 分支里使用。做成函数而不是模块级常量，
    是为了避免打包后 import 时就去做无意义的路径推算。
    """
    return Path(os.path.abspath(__file__)).resolve().parent.parent / "main.py"


def launch_target() -> Path:
    """自启真正要启动的目标。

    - 打包后：exe 本身
    - 开发期：main.py
    """
    if is_frozen():
        return Path(sys.executable)
    return main_script()


def build_command() -> str:
    """构造写进注册表的启动命令行（含引号，兼容路径里的空格与中文）。"""
    if is_frozen():
        # 打包后：sys.executable 就是 exe 本身
        return f'"{sys.executable}"'
    # 开发期：python.exe + main.py 绝对路径
    return f'"{sys.executable}" "{main_script()}"'


def is_enabled() -> bool:
    """当前是否已开启开机自启。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        # 键或值不存在 —— 说明没设置过
        return False
    except OSError as exc:
        print(f"[autostart] 警告：读取开机自启状态失败 -> {exc}")
        return False


def enable() -> bool:
    """开启开机自启。成功返回 True。"""
    if winreg is None:
        print("[autostart] 警告：当前平台不支持注册表自启。")
        return False

    # 写入前先确认启动目标真的存在。
    # 否则会写进一条永远跑不起来的自启项 —— 用户以为开了自启，实际开机什么都不发生，
    # 而且从注册表上很难看出问题（路径看着像对的）。
    target = launch_target()
    if not target.exists():
        print(f"[autostart] 警告：启动目标不存在，已放弃写入 -> {target}")
        return False

    try:
        # CreateKeyEx：键不存在时自动创建
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, build_command())
        return True
    except OSError as exc:
        print(f"[autostart] 警告：开启开机自启失败 -> {exc}")
        return False


def disable() -> bool:
    """关闭开机自启。成功返回 True（本来就没设置也算成功）。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return True  # 本来就不存在，视为已达目标
    except OSError as exc:
        print(f"[autostart] 警告：关闭开机自启失败 -> {exc}")
        return False


def toggle() -> bool:
    """切换开机自启，返回**切换后的真实状态**。

    不直接返回「期望值」，而是切换后重新读一次注册表 ——
    这样即使写入失败（例如被安全软件拦截），返回值也不会骗人。
    """
    if is_enabled():
        disable()
    else:
        enable()
    return is_enabled()
