"""单实例锁（macOS 实现）。

Qt 的 QSharedMemory 在 macOS 上**可用**，所以主体逻辑与 Windows 版一致。
单独放一份而不是直接复用，是为了给平台留差异空间：

    Windows 上异常退出可能留下残留段（v1 就踩过这个坑），
    需要 attach/detach 清理；macOS 的 POSIX 共享内存随进程结束自动回收，
    不需要那套重试逻辑。

保持与 Windows 版**相同的公开接口**：SingleInstance 类 + try_lock/release 函数。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSharedMemory

# key 与 Windows 版保持一致，避免同一台机器双系统时出现两套锁。
DEFAULT_KEY = "desktop-pet-timer-single-instance-v2"

# 只用来表达「存在性」，1 字节足够。
SEGMENT_SIZE = 1


class SingleInstance:
    """单实例锁。

    :param key: 共享内存键名
    """

    def __init__(self, key: str = DEFAULT_KEY) -> None:
        self._key = key
        self._shared: Optional[QSharedMemory] = None

    def try_lock(self) -> bool:
        """尝试获取锁；已有实例在跑时返回 False。"""
        shared = QSharedMemory(self._key)
        if shared.create(SEGMENT_SIZE):
            self._shared = shared  # 必须持有，否则被 GC 回收后锁就没了
            return True

        # macOS 上 create 失败基本就是「已存在」。这里不再做 Windows 那套
        # attach/detach 清理：POSIX 共享内存不会因进程异常退出而残留。
        return False

    def release(self) -> None:
        """释放锁。"""
        if self._shared is not None:
            try:
                self._shared.detach()
            except Exception:
                pass
            self._shared = None


_holder: Optional[SingleInstance] = None


def try_lock(key: str = DEFAULT_KEY) -> bool:
    """模块级便捷函数，锁对象由模块持有。"""
    global _holder
    if _holder is None:
        _holder = SingleInstance(key)
    return _holder.try_lock()


def release() -> None:
    """释放模块级锁。"""
    global _holder
    if _holder is not None:
        _holder.release()
        _holder = None
