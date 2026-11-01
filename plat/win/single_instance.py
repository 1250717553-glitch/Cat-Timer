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
# v1 改成 v2：v1 那段的 key 已经被一次异常退出留下的残留段占死
# （实测该段 size=4096，而本模块 create 的是 1 字节），
# 导致所有升级到这一版的用户都会「程序打不开」。
# 换 key 是最直接的止损：旧残留在系统里自生自灭，不影响新版本。
DEFAULT_KEY = "desktop-pet-timer-single-instance-v2"

# 段大小。只用来表达「存在性」，1 字节就够；
# 但取 4096 是与页面对齐的常见值，避免个别平台上小于页面的段行为异常。
SEGMENT_SIZE = 1


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

        # 先直接 create：这是最可靠的判据 —— 只有真的没人持有才能成功。
        if shared.create(SEGMENT_SIZE):
            self._shared = shared
            self._locked = True
            return True

        # create 失败可能有两种原因：真的已有实例在跑，或只是残留段。
        # 用 attach/detach 清掉一次引用计数再重试：
        # 残留段被清后会释放，重试就能成功；真实例仍在跑时它握着段，重试仍失败。
        stale = QSharedMemory(self._key)
        if stale.attach():
            stale.detach()
        retry = QSharedMemory(self._key)
        if retry.create(SEGMENT_SIZE):
            self._shared = retry
            self._locked = True
            return True

        # 到这一步说明确实有另一个实例在运行。
        # 但如果连 errorString 都是「已存在」之外的错误，宁可放行也不要把用户锁在门外。
        err = retry.errorString()
        if "already exists" not in err.lower() and "已存在" not in err:
            print(f"[single_instance] 创建共享内存失败（放行）-> {err}")
            return True
        return False

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
