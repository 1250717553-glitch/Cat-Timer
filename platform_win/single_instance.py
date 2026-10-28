"""单实例锁（基于 QSharedMemory，零额外依赖）。

原理：共享内存段是**进程级、系统级**的资源。
第一个进程 create() 成功，第二个进程 create() 会失败 —— 由此实现互斥。

【重要】QSharedMemory 对象必须保持存活，一旦被 GC 回收锁就释放了，
所以这里用一个模块级变量持有它。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QSharedMemory

# 共享内存段的键名（全局唯一，避免与其他程序冲突）
DEFAULT_KEY = "desktop-pet-timer-single-instance-v1"


class SingleInstance:
    """单实例锁。

    :param key: 共享内存键名
    """

    def __init__(self, key: str = DEFAULT_KEY) -> None:
        self._key = key
        self._shared: Optional[QSharedMemory] = None
        self._locked = False

    def try_lock(self) -> bool:
        """尝试获取锁。

        :return: True 表示获取成功（本进程是唯一实例）；
                 False 表示已有实例在运行。
        """
        if self._locked:
            return True  # 本进程已持锁，重复调用直接成功

        shared = QSharedMemory(self._key)

        # 先尝试 attach：能挂上说明别的进程已经创建了这段共享内存
        # （Windows 上进程退出会自动回收，所以 attach 成功即代表有活着的实例）
        if shared.attach():
            shared.detach()
            return False

        if not shared.create(1):  # 1 字节足够，我们只用它的存在性
            print(f"[single_instance] 创建共享内存失败 -> {shared.errorString()}")
            return False

        self._shared = shared  # 必须持有引用，否则锁会被释放
        self._locked = True
        return True

    def release(self) -> None:
        """释放锁（退出程序前调用）。"""
        if self._shared is not None:
            if self._shared.isAttached():
                self._shared.detach()
            self._shared = None
        self._locked = False


# ---------------------------------------------------------------------------
# 模块级单例：给 main.py 提供最简调用方式
# ---------------------------------------------------------------------------

_instance: Optional[SingleInstance] = None


def try_lock(key: str = DEFAULT_KEY) -> bool:
    """获取全局单实例锁。返回 False 表示已有实例在运行。"""
    global _instance
    if _instance is None:
        _instance = SingleInstance(key)
    return _instance.try_lock()


def release() -> None:
    """释放全局单实例锁（幂等，可安全重复调用）。"""
    global _instance
    if _instance is not None:
        _instance.release()
