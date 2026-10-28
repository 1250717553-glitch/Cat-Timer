"""资源路径解析：统一处理「开发期」与「PyInstaller 打包后」两种情形。

打包后**绝对不能**再用 `Path(__file__).parent` 之类去推项目根目录 ——
PyInstaller 会把 .py 编译进归档，`__file__` 指向的位置与资源实际位置无关。
唯一可靠的做法是读 `sys._MEIPASS`（PyInstaller 注入的资源基准目录）。

开发期基准 = 项目根目录；打包后基准 = sys._MEIPASS。
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出来的程序里。"""
    return bool(getattr(sys, "frozen", False))


def base_dir() -> Path:
    """资源基准目录（读取 assets/ 等只读资源时用它）。

    - 打包后：`sys._MEIPASS`（PyInstaller 6.x 的 onedir 模式下是 `_internal/`）
    - 开发期：项目根目录（本文件位于 <项目根>/core/paths.py）
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        # 极端兜底：理论上不会走到（frozen 一定有 _MEIPASS）
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_path(relative: str) -> Path:
    """把相对资源路径解析成绝对路径。

    :param relative: 相对基准目录的路径，例如 "assets/tray.ico"
    """
    return base_dir() / relative


def first_existing(relatives: tuple[str, ...]) -> Path | None:
    """按顺序返回第一个存在的资源路径；都不存在返回 None。"""
    for relative in relatives:
        candidate = resource_path(relative)
        if candidate.is_file():
            return candidate
    return None


def app_dir() -> Path:
    """程序所在目录（需要「往自己身边写文件」时用它）。

    - 打包后：exe 所在目录（不是 `_internal`）
    - 开发期：项目根目录
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return base_dir()


def describe() -> str:
    """返回一句人类可读的环境描述，便于排错时打印。"""
    mode = "打包后(frozen)" if is_frozen() else "开发期"
    return f"{mode} | 资源基准={base_dir()} | 程序目录={app_dir()}"
