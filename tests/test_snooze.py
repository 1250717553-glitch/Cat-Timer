"""core/notifier.snooze 单元测试（纯函数，不需要 PySide6）。

运行方式（在项目根目录执行）：
    python -m unittest tests.test_snooze -v
"""

from __future__ import annotations

import unittest

from core.models import DisplayFormat, Event, EventMode
from core.notifier import collect_due_events, snooze

T0 = 1_700_000_000.0
MIN5 = 300.0
MIN15 = 900.0


def make(
    name: str = "事件",
    mode: EventMode = EventMode.COUNTDOWN,
    anchor_ts: float = T0 - 1,  # 默认已到点
    enabled: bool = True,
    fired_at: float | None = None,
) -> Event:
    """构造测试用事件。"""
    return Event(name=name, mode=mode, anchor_ts=anchor_ts, enabled=enabled, fired_at=fired_at)


class TestSnooze(unittest.TestCase):
    """snooze：把已到点的倒计时改成「从现在起 minutes 分钟后」再提醒。"""

    def test_moves_anchor_forward_and_clears_fired_at(self):
        """核心：anchor_ts 前移到 now+minutes，fired_at 清空为 None。"""
        event = make(anchor_ts=T0 - 60, fired_at=T0 - 60)
        result = snooze(event, 5, now=T0)
        self.assertIsNotNone(result)
        self.assertEqual(result.anchor_ts, T0 + MIN5)
        self.assertIsNone(result.fired_at)

    def test_does_not_mutate_original(self):
        """snooze 返回新对象，绝不原地改动传入的事件。"""
        event = make(anchor_ts=T0 - 60, fired_at=T0 - 60)
        original_anchor = event.anchor_ts
        original_fired = event.fired_at
        snooze(event, 5, now=T0)
        self.assertEqual(event.anchor_ts, original_anchor)
        self.assertEqual(event.fired_at, original_fired)

    def test_keeps_other_fields(self):
        """id / name / mode / display_format / enabled / show_on_desktop 原样保留。"""
        event = Event(
            id="fixed-id",
            name="考试",
            mode=EventMode.COUNTDOWN,
            anchor_ts=T0 - 60,
            display_format=DisplayFormat.FULL,
            enabled=False,
            show_on_desktop=False,
            fired_at=T0 - 60,
        )
        result = snooze(event, 15, now=T0)
        self.assertIsNotNone(result)
        self.assertEqual(result.id, "fixed-id")
        self.assertEqual(result.name, "考试")
        self.assertEqual(result.mode, EventMode.COUNTDOWN)
        self.assertEqual(result.display_format, DisplayFormat.FULL)
        self.assertFalse(result.enabled)
        self.assertFalse(result.show_on_desktop)

    def test_returns_none_for_count_up(self):
        """正计时拒绝：返回 None，调用方应忽略。"""
        event = make(mode=EventMode.COUNT_UP, anchor_ts=T0 - 100)
        self.assertIsNone(snooze(event, 5, now=T0))

    def test_count_up_not_mutated(self):
        """正计时对象不被改动。"""
        event = make(mode=EventMode.COUNT_UP, anchor_ts=T0 - 100)
        original = event.anchor_ts
        snooze(event, 5, now=T0)
        self.assertEqual(event.anchor_ts, original)
        self.assertIsNone(event.fired_at)


class TestSnoozeWithCollect(unittest.TestCase):
    """核心场景：snooze 后不再被 collect_due_events 返回，分钟数过后重新返回。"""

    def test_not_due_until_minutes_pass(self):
        """已提醒过的到点事件，snooze 后立即不返回，5 分钟后重新返回。"""
        event = make(anchor_ts=T0 - 60, fired_at=T0 - 60)
        result = snooze(event, 5, now=T0)
        self.assertIsNotNone(result)
        # 立即：5 分钟内不返回
        self.assertEqual(collect_due_events([result], now=T0), [])
        self.assertEqual(collect_due_events([result], now=T0 + MIN5 - 1), [])
        # 刚好 5 分钟后：重新到点，应当返回
        self.assertEqual(collect_due_events([result], now=T0 + MIN5), [result])
        # 更久之后：依然返回（调用方负责标记 fired_at 防重复）
        self.assertEqual(collect_due_events([result], now=T0 + MIN5 + 60), [result])

    def test_unfired_expired_after_snooze_same_behavior(self):
        """未提醒过但已到点的事件，snooze 后的行为一致。"""
        event = make(anchor_ts=T0 - 60, fired_at=None)
        result = snooze(event, 15, now=T0)
        self.assertIsNotNone(result)
        self.assertEqual(collect_due_events([result], now=T0), [])
        self.assertEqual(collect_due_events([result], now=T0 + MIN15), [result])


if __name__ == "__main__":
    unittest.main(verbosity=2)
