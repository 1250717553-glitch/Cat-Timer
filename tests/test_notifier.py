"""core/notifier.py 单元测试（纯函数，不需要 PySide6）。

运行方式（在项目根目录执行）：
    python -m unittest tests.test_notifier -v
"""

from __future__ import annotations

import unittest

from core.models import Event, EventMode
from core.notifier import collect_due_events, due_message, mark_expired_as_fired

T0 = 1_700_000_000.0
ONE_HOUR = 3600.0


def make(
    name: str = "事件",
    mode: EventMode = EventMode.COUNTDOWN,
    anchor_ts: float = T0,
    enabled: bool = True,
    fired_at: float | None = None,
) -> Event:
    """构造测试用事件。"""
    return Event(name=name, mode=mode, anchor_ts=anchor_ts, enabled=enabled, fired_at=fired_at)


class TestCollectDueEvents(unittest.TestCase):
    """collect_due_events：挑出本次该提醒的事件。"""

    def test_expired_enabled_unfired_is_collected(self):
        """已到点 + 启用 + 未提醒过 -> 应当提醒。"""
        event = make(anchor_ts=T0 - 1)
        self.assertEqual(collect_due_events([event], now=T0), [event])

    def test_exactly_now_is_collected(self):
        """刚好归零也算到点。"""
        event = make(anchor_ts=T0)
        self.assertEqual(collect_due_events([event], now=T0), [event])

    def test_already_fired_is_excluded(self):
        """已提醒过 -> 不再提醒（防重复）。"""
        event = make(anchor_ts=T0 - 1, fired_at=T0 - 1)
        self.assertEqual(collect_due_events([event], now=T0), [])

    def test_disabled_is_excluded(self):
        """停用的事件不打扰用户。"""
        event = make(anchor_ts=T0 - 1, enabled=False)
        self.assertEqual(collect_due_events([event], now=T0), [])

    def test_not_yet_due_is_excluded(self):
        """还没到点 -> 不提醒。"""
        event = make(anchor_ts=T0 + ONE_HOUR)
        self.assertEqual(collect_due_events([event], now=T0), [])

    def test_count_up_never_notifies(self):
        """正计时永不到点，无论锚点在过去多久都不提醒。"""
        event = make(mode=EventMode.COUNT_UP, anchor_ts=T0 - 100 * ONE_HOUR)
        self.assertEqual(collect_due_events([event], now=T0), [])

    def test_mixed_list_returns_only_due_ones(self):
        """混合列表只挑出该提醒的那些。"""
        due = make("该提醒", anchor_ts=T0 - 1)
        events = [
            due,
            make("已提醒过", anchor_ts=T0 - 1, fired_at=T0 - 1),
            make("已停用", anchor_ts=T0 - 1, enabled=False),
            make("还没到点", anchor_ts=T0 + ONE_HOUR),
            make("正计时", mode=EventMode.COUNT_UP, anchor_ts=T0 - 100 * ONE_HOUR),
        ]
        self.assertEqual(collect_due_events(events, now=T0), [due])

    def test_empty_list(self):
        self.assertEqual(collect_due_events([], now=T0), [])


class TestMarkExpiredAsFired(unittest.TestCase):
    """mark_expired_as_fired：启动时把过期事件直接标记掉。"""

    def test_marks_expired_and_returns_true(self):
        """已到点且未提醒 -> 标记，返回 True。"""
        event = make(anchor_ts=T0 - ONE_HOUR)
        self.assertTrue(mark_expired_as_fired([event], now=T0))
        self.assertEqual(event.fired_at, T0)

    def test_returns_false_when_nothing_changes(self):
        """没有可标记的 -> 返回 False（调用方据此跳过落盘）。"""
        events = [
            make("还没到点", anchor_ts=T0 + ONE_HOUR),
            make("已提醒过", anchor_ts=T0 - ONE_HOUR, fired_at=T0 - ONE_HOUR),
            make("正计时", mode=EventMode.COUNT_UP, anchor_ts=T0 - 100 * ONE_HOUR),
        ]
        self.assertFalse(mark_expired_as_fired(events, now=T0))

    def test_does_not_touch_count_up(self):
        """正计时的 fired_at 恒为 None。"""
        event = make(mode=EventMode.COUNT_UP, anchor_ts=T0 - 100 * ONE_HOUR)
        mark_expired_as_fired([event], now=T0)
        self.assertIsNone(event.fired_at)

    def test_does_not_touch_future_events(self):
        """未到点的事件不能被误标记，否则它到点时就不会提醒了。"""
        event = make(anchor_ts=T0 + ONE_HOUR)
        mark_expired_as_fired([event], now=T0)
        self.assertIsNone(event.fired_at)

    def test_marks_disabled_expired_events_too(self):
        """停用的过期事件同样标记 —— 否则用户之后启用它会突然弹陈旧提醒。"""
        event = make(anchor_ts=T0 - ONE_HOUR, enabled=False)
        self.assertTrue(mark_expired_as_fired([event], now=T0))
        self.assertEqual(event.fired_at, T0)

    def test_keeps_existing_fired_at(self):
        """已提醒过的事件保留原时间戳，不被覆盖。"""
        event = make(anchor_ts=T0 - 2 * ONE_HOUR, fired_at=T0 - ONE_HOUR)
        mark_expired_as_fired([event], now=T0)
        self.assertEqual(event.fired_at, T0 - ONE_HOUR)

    def test_empty_list_returns_false(self):
        self.assertFalse(mark_expired_as_fired([], now=T0))


class TestRestartScenario(unittest.TestCase):
    """核心场景：关机期间到点，重启后不补弹。"""

    def test_no_repeat_after_restart(self):
        # 关机前：目标时间在未来 1 小时，未提醒
        event = make("考试", anchor_ts=T0 + ONE_HOUR)
        self.assertIsNone(event.fired_at)

        # 关机 5 小时后重新打开程序
        restart_now = T0 + 5 * ONE_HOUR
        changed = mark_expired_as_fired([event], now=restart_now)
        self.assertTrue(changed)                       # 有改动 -> main.py 会落盘
        self.assertEqual(event.fired_at, restart_now)  # 已标记为已提醒

        # 启动扫描之后，提醒检查不应再挑出它
        self.assertEqual(collect_due_events([event], now=restart_now), [])
        # 再过很久也依然不提醒
        self.assertEqual(collect_due_events([event], now=restart_now + 100 * ONE_HOUR), [])

    def test_future_event_still_notifies_after_restart(self):
        """对照：重启时还没到点的事件，之后到点仍要正常提醒。"""
        event = make("开会", anchor_ts=T0 + 2 * ONE_HOUR)
        mark_expired_as_fired([event], now=T0 + ONE_HOUR)   # 重启时还没到点
        self.assertIsNone(event.fired_at)

        # 1 小时后到点 -> 应当提醒
        self.assertEqual(collect_due_events([event], now=T0 + 2 * ONE_HOUR), [event])


class TestDueMessage(unittest.TestCase):
    """通知正文文案。"""

    def test_message_format(self):
        self.assertEqual(due_message(make(name="考试")), "考试 已到点")

    def test_empty_name(self):
        self.assertEqual(due_message(make(name="")), " 已到点")


if __name__ == "__main__":
    unittest.main(verbosity=2)
