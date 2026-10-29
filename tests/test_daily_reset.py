# -*- coding: utf-8 -*-
"""跨天自动重置计数 + 手动重新开始（core/notifier + core/models）。

运行方式（在项目根目录执行）：
    export APPDATA=$(mktemp -d)
    export QT_QPA_PLATFORM=offscreen
    .venv/Scripts/python.exe -m unittest tests.test_daily_reset -v
"""

from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import json
import unittest

from core.models import Event, EventMode
from core.notifier import (
    at_repeat_limit,
    reset_daily_counters,
    restart_counting,
    collect_due_events,
    mark_expired_as_fired,
)

T0 = 2_000_000_000.0
HOUR = 3600.0
DAY = 24 * 3600.0


def make_repeat(
    repeat_seconds=1800,
    repeat_count=0,
    repeat_count_date="",
    anchor_ts=T0 - 1,
    repeat_limit=None,
    fired_at=None,
    enabled=True,
):
    """一个循环事件。repeat_count_date 默认 ""（旧数据形态）。"""
    return Event(
        name="循环",
        mode=EventMode.COUNTDOWN,
        anchor_ts=anchor_ts,
        enabled=enabled,
        fired_at=fired_at,
        repeat_seconds=repeat_seconds,
        repeat_limit=repeat_limit,
        repeat_count=repeat_count,
        repeat_count_date=repeat_count_date,
    )


class TestResetDailyCounters(unittest.TestCase):
    """reset_daily_counters：跨天把循环事件计数归零。"""

    def test_resets_when_date_differs(self):
        ev = make_repeat(repeat_count=5, repeat_count_date="2020-01-01")
        today = __import__("datetime").date.fromtimestamp(T0).isoformat()
        self.assertNotEqual("2020-01-01", today)
        self.assertTrue(reset_daily_counters([ev], now=T0))
        self.assertEqual(ev.repeat_count, 0)
        self.assertEqual(ev.repeat_count_date, today)

    def test_no_change_same_day(self):
        today = __import__("datetime").date.fromtimestamp(T0).isoformat()
        ev = make_repeat(repeat_count=3, repeat_count_date=today)
        self.assertFalse(reset_daily_counters([ev], now=T0))
        self.assertEqual(ev.repeat_count, 3)

    def test_returns_false_when_nothing_changes(self):
        today = __import__("datetime").date.fromtimestamp(T0).isoformat()
        events = [
            make_repeat(repeat_count=2, repeat_count_date=today),
            make_repeat(repeat_count=0, repeat_count_date=today),
        ]
        self.assertFalse(reset_daily_counters(events, now=T0))

    def test_old_data_missing_date_resets_once(self):
        # 旧数据形态：repeat_count_date=""，首次读入应归零并记为今天
        ev = make_repeat(repeat_count=5, repeat_count_date="")
        today = __import__("datetime").date.fromtimestamp(T0).isoformat()
        self.assertTrue(reset_daily_counters([ev], now=T0))
        self.assertEqual(ev.repeat_count, 0)
        self.assertEqual(ev.repeat_count_date, today)

    def test_does_not_touch_one_off_events(self):
        # 一次性事件（repeat_seconds=None）
        one = make_repeat(repeat_seconds=None, repeat_count=0, repeat_count_date="")
        # 非倒计时事件
        up = Event(
            name="正计时",
            mode=EventMode.COUNT_UP,
            anchor_ts=T0,
            repeat_seconds=1800,
            repeat_count=3,
            repeat_count_date="2020-01-01",
        )
        self.assertFalse(reset_daily_counters([one, up], now=T0))
        self.assertEqual(one.repeat_count_date, "")
        self.assertEqual(up.repeat_count_date, "2020-01-01")

    def test_cross_day_restores_due_detection(self):
        # 关键顺序：次数用满 -> 跨天后 reset 归零,collect 才能重新提示
        ev = make_repeat(
            repeat_limit=5,
            repeat_count=5,
            repeat_count_date="2020-01-01",
            anchor_ts=T0 - 1,
        )
        self.assertTrue(at_repeat_limit(ev))
        # reset 必须先于 collect_due_events
        self.assertTrue(reset_daily_counters([ev], now=T0))
        self.assertFalse(at_repeat_limit(ev))
        self.assertEqual(collect_due_events([ev], now=T0), [ev])

    def test_idempotent_reset_returns_true_then_false(self):
        """幂等自检的返回值序列：同一天连调 reset_daily_counters 三次，
        只有第一次发生跨天重置返回 True，后两次无副作用返回 False。
        这是「幂等」设计能被测试抓住的返回值契约 —— 若实现变成了
        每次调用都归零，第二次就会返回 True 而暴露问题。"""
        today = __import__("datetime").date.fromtimestamp(T0).isoformat()
        ev = make_repeat(repeat_count=5, repeat_count_date="2020-01-01")
        r1 = reset_daily_counters([ev], now=T0)
        r2 = reset_daily_counters([ev], now=T0)
        r3 = reset_daily_counters([ev], now=T0)
        self.assertEqual([r1, r2, r3], [True, False, False])
        self.assertEqual(ev.repeat_count, 0)
        self.assertEqual(ev.repeat_count_date, today)


class TestRestartCounting(unittest.TestCase):
    """restart_counting：手动重新开始循环事件。"""

    def test_resets_count_date_fired_and_anchor(self):
        ev = make_repeat(
            repeat_limit=5,
            repeat_count=4,
            repeat_count_date="2020-01-01",
            anchor_ts=1000.0,
            fired_at=T0 - 1,
        )
        result = restart_counting(ev, now=T0)
        today = __import__("datetime").date.fromtimestamp(T0).isoformat()
        self.assertIsNotNone(result)
        self.assertEqual(result.repeat_count, 0)
        self.assertEqual(result.repeat_count_date, today)
        self.assertIsNone(result.fired_at)
        self.assertEqual(result.anchor_ts, T0 + ev.repeat_seconds)
        # 原对象不动
        self.assertEqual(ev.repeat_count, 4)
        self.assertIsNotNone(ev.fired_at)
        # 立即被云盘为启用中的循环事件，且到点后可提醒
        self.assertFalse(at_repeat_limit(result))
        self.assertEqual(collect_due_events([result], now=T0 + 1800), [result])

    def test_returns_none_for_one_off(self):
        one = make_repeat(repeat_seconds=None)
        self.assertIsNone(restart_counting(one, now=T0))

    def test_returns_none_for_count_up(self):
        up = Event(
            name="正计时",
            mode=EventMode.COUNT_UP,
            anchor_ts=T0,
            repeat_seconds=1800,
        )
        self.assertIsNone(restart_counting(up, now=T0))


class TestStartupChainCrossDay(unittest.TestCase):
    """致命顺序回归：跨天 + 完整启动流程。

    链路（对应 main.py 的真实顺序）：
        load_events -> mark_expired_as_fired（应**先**归零再标记）-> reset_daily_counters -> check_notifications

    反例（导致 bug）：若 mark_expired_as_fired 先判 at_repeat_limit 后归零，
    则「昨天的完成状态」会把今天的第一次提醒标记掉。此测试守护该顺序。
    """

    def test_three_days_later_boot_still_notifies_today(self):
        three_days_ago = __import__("datetime").date.fromtimestamp(T0 - 3 * DAY).isoformat()
        # 昨天用满：count=5 limit=5，日期是 3 天前；anchor 已过期
        ev = make_repeat(
            repeat_limit=5,
            repeat_count=5,
            repeat_count_date=three_days_ago,
            anchor_ts=T0 - 10,
            fired_at=None,
        )

        # 1) 启动扫描：跨天归零要在 at_repeat_limit 判断之前
        changed1 = mark_expired_as_fired([ev], now=T0)
        self.assertTrue(changed1)
        today = __import__("datetime").date.fromtimestamp(T0).isoformat()
        self.assertEqual(ev.repeat_count, 0, "跨天后计数必须先归零")
        self.assertEqual(ev.repeat_count_date, today)
        # 关键：不能被标记（fired_at 保持 None），否则今天一整天都不提醒
        self.assertIsNone(ev.fired_at, "启动扫描绝不能标记归零后的事件")

        # 2) 周期性检查里的 reset_daily_counters：当天再跑应为 False（已归零）
        self.assertFalse(reset_daily_counters([ev], now=T0))

        # 3) check_notifications 等效：collect 应能返回它（注意它进过启动补齐，
        #    anchor 已被推到 now+周期；所以手动把 anchor 拉回过去模拟「补跑」）
        ev2 = make_repeat(
            repeat_limit=5,
            repeat_count=0,
            repeat_count_date=today,
            anchor_ts=T0 - 10,
            repeat_seconds=60,
            fired_at=None,
        )
        self.assertEqual(collect_due_events([ev2], now=T0), [ev2])


class TestDailyResetSerialization(unittest.TestCase):
    """新字段的序列化兼容：旧 JSON 读出默认值。"""

    def test_old_json_missing_new_fields(self):
        raw = {
            "id": "x",
            "name": "旧事件",
            "mode": "countdown",
            "anchor_ts": T0,
            "repeat_seconds": 1800,
            "repeat_limit": 5,
            "repeat_count": 3,
        }
        ev = Event.from_dict(raw)
        self.assertEqual(ev.repeat_count_date, "")
        self.assertEqual(ev.active_start, "")
        self.assertEqual(ev.active_end, "")

    def test_roundtrip_keeps_new_fields(self):
        ev = Event(
            name="循环",
            mode=EventMode.COUNTDOWN,
            anchor_ts=T0,
            repeat_seconds=1800,
            repeat_count=2,
            repeat_count_date="2026-10-29",
            active_start="09:00",
            active_end="18:00",
        )
        data = ev.to_dict()
        back = Event.from_dict(data)
        self.assertEqual(back.repeat_count_date, "2026-10-29")
        self.assertEqual(back.active_start, "09:00")
        self.assertEqual(back.active_end, "18:00")


class TestPerPointSelfCheck(unittest.TestCase):
    """自检在设计（追加指令 2）：每个读/写 repeat_count 的入口第一行都先
    ensure_daily_fresh，即使调用方忘了先 reset_daily_counters 也不会出错。

    这两条是「自检」与「批量 reset 只是性能优化」的回归测试 —— 若某入口
    删掉了自查，只靠 reset_daily_counters 兜底，它们会立刻变红。
    """

    def test_collect_self_check_resets_and_returns_after_cross_day(self):
        """collect_due_events 单独调用（调用方没先 reset）：昨天用满 5/5 的
        事件今天应被自检归零并返回（重新开始提醒）。"""
        three_days_ago = __import__("datetime").date.fromtimestamp(T0 - 3 * DAY).isoformat()
        today = __import__("datetime").date.fromtimestamp(T0).isoformat()
        ev = make_repeat(
            repeat_limit=5,
            repeat_count=5,
            repeat_count_date=three_days_ago,
            anchor_ts=T0 - 10,
            fired_at=None,
        )
        # 不调 reset_daily_counters，直接让 collect_due_events 自己自检
        self.assertEqual(collect_due_events([ev], now=T0), [ev])
        self.assertEqual(ev.repeat_count, 0, "自检必须先于上限判定归零")
        self.assertEqual(ev.repeat_count_date, today)
        self.assertTrue(at_repeat_limit(ev) is False)

    def test_advance_self_check_resets_before_limit_check(self):
        """advance_repeating 单独调用（调用方没先 reset）：自检归零后，
        昨天用满的事件今天可以重新推进（不被 at_repeat_limit 挡下）。"""
        three_days_ago = __import__("datetime").date.fromtimestamp(T0 - 3 * DAY).isoformat()
        today = __import__("datetime").date.fromtimestamp(T0).isoformat()
        ev = make_repeat(
            repeat_limit=5,
            repeat_count=5,
            repeat_count_date=three_days_ago,
            anchor_ts=T0 - 10,
            fired_at=None,
        )
        # 不调 reset_daily_counters，直接调 advance_repeating
        from core.notifier import advance_repeating
        self.assertTrue(advance_repeating(ev, now=T0))
        self.assertEqual(ev.repeat_count, 1, "归零后再推进 +1")
        self.assertEqual(ev.repeat_count_date, today)


class TestMarkExpiredCrossDayOutsideWindow(unittest.TestCase):
    """追加指令 3 契约：mark_expired_as_fired 返回值 = 「是否有任何改动」
    （标记完成 OR 跨天重置）。结合 追加指令 4：时段外 + 跨天，
    应归零、不标记（fired_at 保持 None）、返回 True，落盘后读回仍为 0。"""

    def test_cross_day_outside_window_returns_true_resets_not_marks_and_persists(self):
        # 昨天用满 5/5、时间窗 09:00-18:00，今早 08:00 开机（时段外）
        import datetime
        import tempfile
        import os
        from core.storage import load_events, save_events

        three_days_ago = datetime.date.fromtimestamp(T0 - 3 * DAY).isoformat()
        ev = make_repeat(
            repeat_limit=5,
            repeat_count=5,
            repeat_count_date=three_days_ago,
            anchor_ts=T0 - 6 * 3600,
            fired_at=None,
            enabled=True,
        )
        ev.active_start = "09:00"
        ev.active_end = "18:00"

        # 08:00 属于时段外；时刻必须与 make_repeat 的日期基准（T0 那天的本地时间）
        # 同一天，否则 anchor_ts=T0-10（相对该日出生的 "今天"）会落在未来，
        # 09:01 时判断成「未到点」而不返回，测不出本意。
        base_day = datetime.datetime.fromtimestamp(T0)
        morning = base_day.replace(hour=8, minute=0, second=0, microsecond=0).timestamp()
        changed = mark_expired_as_fired([ev], now=morning)
        today = datetime.date.fromtimestamp(morning).isoformat()
        # 跨天重置发生了 -> 有改动，返回 True
        self.assertTrue(changed)
        self.assertEqual(ev.repeat_count, 0)
        self.assertEqual(ev.repeat_count_date, today)
        # 时段外不标记：保持 None，进入时段后 collect 仍会返回它
        self.assertIsNone(ev.fired_at, "时段外被跨天重置的事件绝不能标记")

        # 落盘契约：写盘后读回，计数仍为 0、fired_at 仍为 None
        save_events([ev])
        reloaded = [e for e in load_events() if e.id == ev.id][0]
        self.assertEqual(reloaded.repeat_count, 0)
        self.assertIsNone(reloaded.fired_at)
        # 并且在时段内时确实能被 collect 返回（不丢今天的第一次提醒）
        in_window = base_day.replace(hour=9, minute=1, second=0, microsecond=0).timestamp()
        ev2_ev = [e for e in load_events() if e.id == ev.id][0]
        self.assertEqual(collect_due_events([ev2_ev], now=in_window), [ev2_ev])

if __name__ == "__main__":
    unittest.main(verbosity=2)