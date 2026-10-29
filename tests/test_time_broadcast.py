"""单一计时源：主窗口 time_changed 常驻广播驱动悬浮窗刷新。

覆盖三条不变量（需要 PySide6 + offscreen，见底部运行说明）：
  A) FloatingWindow 不再自建 QTimer 实例字段；
  B) time_changed 由常驻提醒定时器（check_notifications）发出，主窗口隐藏后仍广播；
  C) 悬浮窗每秒仍更新（隐藏主窗口后手动驱动 check_notifications，标签文本变化）。

运行方式（在项目根目录执行）：
    export QT_QPA_PLATFORM=offscreen
    export APPDATA=$(mktemp -d)
    .venv/Scripts/python.exe -m unittest tests.test_time_broadcast -v
"""


from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import os
import tempfile
import time
import unittest
from pathlib import Path

# 必须在 QApplication 创建之前设置离屏平台
if not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core.models import DisplayFormat, Event, EventMode
from ui.floating_window import FloatingWindow
from ui.main_window import MainWindow


def _make_running_countdown() -> Event:
    """一个 10 秒后到点的倒计时：每秒剩余秒数变化，标签文本可观察地改变。"""
    return Event(
        name="待刷新",
        mode=EventMode.COUNTDOWN,
        anchor_ts=time.time() + 10,
        display_format=DisplayFormat.FULL,
        enabled=True,
        show_on_desktop=True,
    )


class TimeBroadcastTest(unittest.TestCase):
    """GUI 层不变量：单一计时源 + 常驻广播。"""

    _app = None

    @classmethod
    def setUpClass(cls):
        # 隔离 APPDATA，避免写出真实用户配置
        cls._tmp = tempfile.TemporaryDirectory()
        cls._env_patch = _EnvPatch()
        # QApplication 全局只能建一个，用类级创建
        app = QApplication.instance()
        if app is None:
            cls._app = QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls._env_patch.restore()
        cls._tmp.cleanup()

    def setUp(self):
        self._env_patch.start(self._tmp.name)
        # 每个用例独立 MainWindow / FloatingWindow，避免信号跨用例残留
        self.main = MainWindow(events=[_make_running_countdown()])
        self.floating = FloatingWindow(self.main)
        self.main.floating_window = self.floating
        self.floating.show()

    def tearDown(self):
        # 停掉两个定时器，防跨用例泄漏（不能用会触发副作用的方式直接删事件）
        self.main.hide()
        self.floating.close()
        self.floating = None
        self.main = None

    def test_floating_has_no_second_refresh_timer(self):
        """不变量 A：悬浮窗对象树里不允许出现「每秒刷新」定时器。

        只允许一个 ≤100ms 的动画节拍（呼吸/点击/歪头）。理由：每秒的时间刷新
        必须只有主窗口心跳这一个来源，两个 1000ms 定时器相位不同会互相打架
        （表现为悬浮窗比表格慢一拍）。

        用 `findChildren` **递归穿透对象树**，而不是只看 `vars(self.floating)`：
        动画定时器封装在 _PetAnimator 里，只查窗口自身的字段会漏掉它 ——
        那样这条测试就形同虚设（曾经的版本正是这么写的）。
        """
        intervals = [timer.interval() for timer in self.floating.findChildren(QTimer)]

        slow = [i for i in intervals if 900 <= i <= 1100]
        self.assertEqual(
            slow, [], f"悬浮窗不应持有 1 秒刷新定时器，实际全部间隔：{intervals}"
        )

        fast = [i for i in intervals if i <= 100]
        self.assertEqual(
            len(fast), 1, f"应恰好有一个动画定时器（≤100ms），实际全部间隔：{intervals}"
        )

    def test_pet_area_is_top_aligned_and_fixed_size(self):
        """宠物区贴顶、尺寸固定，不随卡片高度变化；x 跟着朝向走。

        顶部对齐是刻意的：事件多到卡片比猫高时，垂直居中会让猫悬在窗口中间，
        上下各留一大片空白，看起来像「飘着」。
        """
        box = self.floating._pet_box()
        self.assertEqual(box.top(), 0)
        self.assertEqual(box.width(), FloatingWindow.PET_AREA_WIDTH)
        self.assertEqual(box.height(), FloatingWindow.PET_AREA_HEIGHT)

        # x 由朝向决定：默认贴左（0），翻转后贴右（width - PET_AREA_WIDTH）
        expected_x = (
            self.floating.width() - FloatingWindow.PET_AREA_WIDTH
            if self.floating.is_flipped()
            else 0
        )
        self.assertEqual(box.left(), expected_x)

        # 加事件让卡片长高，宠物区必须**顶部对齐且尺寸不变**。
        # 注意不能断言 QRect 整体相等：翻转态下 x = width - PET_AREA_WIDTH，
        # 而卡片变高时宽度也可能变（时间文本变长），x 随之变化是正确行为。
        for _ in range(5):
            self.main.events.append(_make_running_countdown())
        self.floating.rebuild()
        grown = self.floating._pet_box()
        self.assertGreater(self.floating.height(), box.height(), "卡片没有长高，用例前提不成立")
        self.assertEqual(grown.top(), 0, "宠物区必须始终贴顶")
        self.assertEqual(grown.width(), FloatingWindow.PET_AREA_WIDTH)
        self.assertEqual(grown.height(), FloatingWindow.PET_AREA_HEIGHT)
        expected_x_after = (
            self.floating.width() - FloatingWindow.PET_AREA_WIDTH
            if self.floating.is_flipped()
            else 0
        )
        self.assertEqual(grown.left(), expected_x_after, "宠物区的 x 必须仍按当前朝向贴边")

    def test_time_changed_emitted_and_updates_floating_when_hidden(self):
        """不变量 B/C：主窗口隐藏后，常驻定时器路径仍广播并刷新悬浮窗。"""
        # 主窗口隐藏（发到托盘）—— 界面刷新定时器停掉，但不影响提醒检查
        self.main.hide()
        self.assertFalse(self.main.isVisible())

        # 记录初始文本
        initial = [row[3].text() for row in self.floating._rows]
        self.assertTrue(initial)  # 有可见事件

        # 手动驱动常驻路径（等价于 _notify_timer 的 check_notifications 回调）
        spy = []
        self.main.time_changed.connect(lambda now: spy.append(now))
        time.sleep(1.1)  # 让「剩余秒数」必然变化，避免同一秒内比较
        self.main.check_notifications()

        # B：常驻广播在隐藏时确实发出
        self.assertTrue(spy, "隐藏主窗口后 time_changed 未发出")
        # C：悬浮窗文本随广播更新
        now_text = [row[3].text() for row in self.floating._rows]
        self.assertNotEqual(initial, now_text, "隐藏主窗口后悬浮窗应每秒更新")

    def test_snooze_button_greyed_when_none_or_not_due(self):
        """不变量 D：未选中 / 选中但未到点 -> 稍后提醒按钮置灰。"""
        # 未选中
        self.main._update_button_state()
        self.assertFalse(self.main.snooze_button.isEnabled())

        # 选中但未到点（锚点在未来）
        self.main.events[0] = _make_running_countdown()  # +10s，未到点
        self.main._populate_table()
        self.main.table.selectRow(0)
        self.main._update_button_state()
        self.assertFalse(self.main.snooze_button.isEnabled())

        # 已到点的倒计时 -> 可用
        self.main.events[0].anchor_ts = time.time() - 1
        self.main._update_button_state()
        self.assertTrue(self.main.snooze_button.isEnabled())

    def test_snooze_moves_event_forward_on_due(self):
        """点稍后提醒后：事件 anchor 前移、fired_at 清空（纯逻辑验证）。"""
        due = Event(
            name="已到点",
            mode=EventMode.COUNTDOWN,
            anchor_ts=time.time() - 1,
            display_format=DisplayFormat.FULL,
            enabled=True,
            show_on_desktop=True,
        )
        self.main.events = [due]
        self.main._populate_table()
        before = time.time()
        self.assertTrue(self.main.snooze_event_at(0, 5))
        after = time.time()
        ev = self.main.events[0]
        self.assertIsNone(ev.fired_at)
        self.assertGreaterEqual(ev.anchor_ts, before + 300)
        self.assertLessEqual(ev.anchor_ts, after + 300)


class _EnvPatch:
    """临时把 APPDATA 指到测试目录（不写真实用户目录）。"""

    def __init__(self):
        self._old = None

    def start(self, root: str):
        self._old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = root

    def restore(self):
        if self._old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old


if __name__ == "__main__":
    unittest.main(verbosity=2)
