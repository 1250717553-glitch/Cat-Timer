"""资源路径解析：统一处理「开发期」与「PyInstaller 打包后」两种情形。

打包后**绝对不能**再用 `Path(__file__).parent` 之类去推项目根目录 ——
PyInstaller 会把 .py 编译进归档，`__file__` 指向的位置与资源实际位置无关。
唯一可靠的做法是读 `sys._MEIPASS`（PyInstaller 注入的资源基准目录）。

开发期基准 = 项目根目录；打包后基准 = sys._MEIPASS。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional




def backup_corrupt_file(path: Path) -> Optional[Path]:
    """把损坏的文件改名备份，返回备份路径；失败返回 None。

    为什么用 rename 而不是 copy：
        copy 的话原文件还在，下次启动又读到同样的损坏内容，反复报错；
        rename 之后原路径空出来，程序可以正常写入新文件，
        损坏内容被隔离在 .corrupt-<时间戳> 里，用户随时能找回。

    为什么文件名叫 .corrupt- 而不是 .bak：
        后者看不出「为什么备份」，用户可能以为是正常备份而删掉；
        corrupt 这个词本身就在提示「这个文件有问题，你该看一眼」。

    为什么带时间戳：同一份配置可能在不同时间多次损坏，
    覆盖掉旧的备份等于把更早期的数据也丢了。

    为什么不能抛异常：
        本函数专门在 except 分支里被调用。它再抛的话，
        调用方（load_events / load_preferences / restore_position）会直接崩，
        整个程序起不来 —— 比丢一份备份严重得多。

    :param path: 损坏文件的路径
    :return: 备份后的路径；文件不存在或改名失败时返回 None
    """
    try:
        if not path.exists():
            return None
    except OSError:
        return None

    stamp = int(time.time())
    backup = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
    # 同一秒内连续损坏会撞名，加序号避免覆盖前一份
    counter = 1
    try:
        while backup.exists():
            backup = path.with_name(f"{path.stem}.corrupt-{stamp}-{counter}{path.suffix}")
            counter += 1
        path.rename(backup)
        print(f"[storage] 配置损坏，已备份到 {backup.name}")
        return backup
    except OSError as exc:
        print(f"[storage] 警告：损坏文件备份失败 -> {exc}")
        return None


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
