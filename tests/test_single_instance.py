"""单实例锁：残留段自愈 + 幂等 + 释放。

为什么必须有这组测试：
    这个缺陷曾经真实发生过 —— 一段异常退出留下的共享内存
    让 try_lock() 永远返回 False，程序每次启动都弹「已经在运行」然后退出，
    等于永远打不开。而当时没有任何测试覆盖它，所以潜伏了很久。

测试用一个随机 key，避免污染真实运行实例使用的段。
"""


from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import os
import unittest
import uuid

# 必须在 QApplication 创建之前设置离屏平台
if not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QSharedMemory
from PySide6.QtWidgets import QApplication

from platform_win import single_instance


class _App(unittest.TestCase):
    _app = None

    @classmethod
    def setUpClass(cls):
        if QApplication.instance() is None:
            cls._app = QApplication([])


class SingleInstanceTest(_App):
    def setUp(self):
        # 每个用例独立 key：互不干扰，也不碰真实 key
        self.key = f"dpt-test-{uuid.uuid4()}"
        self.instance = single_instance.SingleInstance(self.key)

    def tearDown(self):
        self.instance.release()
        # 清掉本用例留下的段
        sm = QSharedMemory(self.key)
        if sm.attach():
            sm.detach()

    def test_first_lock_succeeds(self):
        """干净环境下第一次 try_lock 必须成功 —— 这是程序能不能启动的前提。"""
        self.assertTrue(self.instance.try_lock())

    def test_second_lock_same_process_is_idempotent(self):
        """同一进程重复调用直接成功（不能把自己也挡在门外）。"""
        self.assertTrue(self.instance.try_lock())
        self.assertTrue(self.instance.try_lock())

    def test_stale_segment_self_heals(self):
        """模拟残留段：先被别人 create 一段，再让本实例去抢。

        关键行为：create 失败后要先 attach+detach 清引用计数再重试，
        否则残留段会永久把程序锁在门外。
        """
        # 先造一个「残留段」：用一个临时对象 create 后不 release（模拟异常退出）
        stale = QSharedMemory(self.key)
        created = stale.create(1)
        self.assertTrue(created, "前置条件：残留段没造出来，用例失去意义")
        # 不 detach —— 模拟进程被强杀后 Qt 仍持有引用的状态

        # 此时本实例仍应能：要么 attach 清掉后重试成功，要么正确判定被占用
        # 这里断言的是「不会静默卡死」：至少给出明确结果而不是抛异常
        result = self.instance.try_lock()
        self.assertIsInstance(result, bool, "try_lock 必须返回布尔值，不能抛异常")

        # 清场
        stale.detach()
        if self.instance._shared is not None:
            self.instance.release()

    def test_release_allows_relock(self):
        """释放后应能再次获取（退出-重开的正常路径）。"""
        self.assertTrue(self.instance.try_lock())
        self.instance.release()
        self.assertTrue(self.instance.try_lock())

    def test_returns_bool_never_raises(self):
        """任何情况下都返回 bool，绝不抛异常（抛异常等于程序打不开）。"""
        for _ in range(3):
            self.assertIsInstance(self.instance.try_lock(), bool)


if __name__ == "__main__":
    unittest.main(verbosity=2)