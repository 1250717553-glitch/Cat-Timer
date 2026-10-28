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

    def test_floating_has_no_qttimer_field(self):
        """不变量 A：FloatingWindow 没有 QTimer 实例字段，也非其子类实例。"""
        self.assertFalse(hasattr(self.floating, "_timer"))
        for name, value in vars(self.floating).items():
            self.assertNotIsInstance(
                value, QTimer, f"悬浮窗不应持有 QTimer 字段：{name}"
            )

    def test_time_changed_emitted_and_updates_floating_when_hidden(self):
        """不变量 B/C：主窗口隐藏后，常驻定时器路径仍广播并刷新悬浮窗。"""
        # 主窗口隐藏（发到托盘）—— 界面刷新定时器停掉，但不影响提醒检查
        self.main.hide()
        self.assertFalse(self.main.isVisible())

        # 记录初始文本
        initial = [label.text() for _, label, _ in self.floating._rows]
        self.assertTrue(initial)  # 有可见事件

        # 手动驱动常驻路径（等价于 _notify_timer 的 check_notifications 回调）
        spy = []
        self.main.time_changed.connect(lambda now: spy.append(now))
        time.sleep(1.1)  # 让「剩余秒数」必然变化，避免同一秒内比较
        self.main.check_notifications()

        # B：常驻广播在隐藏时确实发出
        self.assertTrue(spy, "隐藏主窗口后 time_changed 未发出")
        # C：悬浮窗文本随广播更新
        now_text = [label.text() for _, label, _ in self.floating._rows]
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
