# -*- coding: utf-8 -*-
"""提醒时间段（core/notifier）。

运行方式（在项目根目录执行）：
    export APPDATA=$(mktemp -d)
    export QT_QPA_PLATFORM=offscreen
    .venv/Scripts/python.exe -m unittest tests.test_active_hours -v

覆盖（重点：mark_expired_as_fired 与 active hours 的交互）：
    1. is_within_active_hours 的规则（全天/非法/跨零点/普通区间）
    2. collect_due_events 在时段外跳过事件
    3. 【致命】启动扫描 mark_expired_as_fired 不标记时段外已到点事件，
       进入时段后才正常提醒 —— 这是「开机在 08:00、时段 09:00-18:00」不丢提醒的关键
"""

from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import unittest

from core.models import Event, EventMode
from core.notifier import (
    collect_due_events,
    is_within_active_hours,
    mark_expired_as_fired,
)

T0 = 2_000_000_000.0
HOUR = 3600.0


def make(
    name="事件",
    anchor_ts=T0 - HOUR,
    active_start="",
    active_end="",
    fired_at=None,
    enabled=True,
    repeat_seconds=None,
):
    return Event(
        name=name,
        mode=EventMode.COUNTDOWN,
        anchor_ts=anchor_ts,
        enabled=enabled,
        fired_at=fired_at,
        repeat_seconds=repeat_seconds,
        active_start=active_start,
        active_end=active_end,
    )


# 构造一个具体时刻：2026-10-29 15:30 的本地时间戳
# 直接用 fromtimestamp 构造太绕，改用时间戳基准 None 的场景按真实时间跑不可控，
# 这里给出手工构造：用 datetime 反向生成该小时的时间戳。
def _ts(hour: int, minute: int = 0) -> float:
    import datetime
    d = datetime.datetime(2026, 10, 29, hour, minute, 0)
    return d.timestamp()


class TestIsWithinActiveHours(unittest.TestCase):
    def test_both_empty_is_all_day(self):
        self.assertTrue(is_within_active_hours(make()))

    def test_only_one_empty_is_all_day(self):
        self.assertTrue(is_within_active_hours(make(active_start="09:00")))
        self.assertTrue(is_within_active_hours(make(active_end="18:00")))

    def test_illegal_format_is_all_day(self):
        ev = make(active_start="abc", active_end="18:00")
        self.assertTrue(is_within_active_hours(ev, now=_ts(10)))
        ev2 = make(active_start="25:99", active_end="18:00")
        self.assertTrue(is_within_active_hours(ev2, now=_ts(10)))

    def test_normal_interval(self):
        ev = make(active_start="09:00", active_end="18:00")
        self.assertTrue(is_within_active_hours(ev, now=_ts(12)))
        self.assertFalse(is_within_active_hours(ev, now=_ts(8)))
        self.assertFalse(is_within_active_hours(ev, now=_ts(19)))
        # 边界：18:00 整不算（开区间）
        self.assertFalse(is_within_active_hours(ev, now=_ts(18)))
        self.assertTrue(is_within_active_hours(ev, now=_ts(9)))

    def test_cross_midnight(self):
        ev = make(active_start="22:00", active_end="02:00")
        self.assertTrue(is_within_active_hours(ev, now=_ts(23)))
        self.assertTrue(is_within_active_hours(ev, now=_ts(1, 30)))
        self.assertFalse(is_within_active_hours(ev, now=_ts(3)))
        self.assertFalse(is_within_active_hours(ev, now=_ts(21)))


class TestCollectDueActiveHours(unittest.TestCase):
    def test_outside_interval_is_skipped(self):
        ev = make(anchor_ts=_ts(12), active_start="09:00", active_end="18:00")
        # 时段内到点 -> 提醒
        self.assertEqual(collect_due_events([ev], now=_ts(12, 5)), [ev])
        # 时段外（如 08:00 检查，虽已到点）-> 跳过
        self.assertEqual(collect_due_events([ev], now=_ts(8, 59)), [])


class TestMarkExpiredActiveHours(unittest.TestCase):
    """关键：启动扫描不标记时段外的已到点事件。"""

    def test_outside_interval_not_marked_and_not_advanced(self):
        # 事件时段 09:00-18:00；在 08:00 开机，它已到点但在时段外
        ev = make(
            anchor_ts=_ts(7, 30),
            active_start="09:00",
            active_end="18:00",
        )
        before_anchor = ev.anchor_ts
        changed = mark_expired_as_fired([ev], now=_ts(8, 0))
        self.assertFalse(changed)
        self.assertIsNone(ev.fired_at)
        self.assertEqual(ev.anchor_ts, before_anchor)

    def test_inside_interval_is_marked(self):
        ev = make(anchor_ts=_ts(7, 30), active_start="09:00", active_end="18:00")
        self.assertTrue(mark_expired_as_fired([ev], now=_ts(12)))
        self.assertEqual(ev.fired_at, _ts(12))

    def test_after_entering_interval_it_notifies_once(self):
        # 08:00 开机不标记 -> 09:00 进入时段后应正常提醒（anchor 只推进一次，不补弹）
        ev = make(anchor_ts=_ts(7, 30), active_start="09:00", active_end="18:00")
        mark_expired_as_fired([ev], now=_ts(8, 0))
        self.assertIsNone(ev.fired_at)
        # 进入时段，已过期的它应被 collect 挑出
        self.assertEqual(collect_due_events([ev], now=_ts(9, 1)), [ev])


if __name__ == "__main__":
    unittest.main(verbosity=2)
