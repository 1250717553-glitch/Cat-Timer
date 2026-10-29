"""重复提醒（core/notifier + core/models）单元测试（纯函数，不需要 PySide6）。

运行方式（在项目根目录执行）：
    python -m unittest tests.test_repeat -v

覆盖（原 5 例 + 追加 3 例）：
    1. advance_repeating：anchor 前进一个周期、repeat_count +1
    2. 错过 8 小时（周期 30 分钟）只跳一次到 now+周期，不会补弹 16 次
    3. snooze 后从新 anchor 重新起算周期（配合现有 snooze 测试）
    4. 旧 JSON（无 repeat 字段）-> repeat_seconds=None / repeat_count=0
    5. 一次性事件（repeat_seconds=None）advance 为空操作
    7. repeat_seconds=0 / 负数 -> 一次性，不每秒提醒
    8. 启动扫描：advance anchor、repeat_count 不变、collect_due_events 立即为空
    9. repeat_seconds=31536000（365 天）-> 不溢出、不抛异常（形式检查）
"""


from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import json
import os
import tempfile
import unittest
from unittest import mock

# 必须在 QApplication 创建之前设置离屏平台
if not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication

import ui.main_window as mw_module
from core.models import DisplayFormat, Event, EventMode
from core.notifier import (
    advance_repeating,
    collect_due_events,
    mark_expired_as_fired,
    snooze,
)

T0 = 2_000_000_000.0
HOUR = 3600.0
MIN30 = 1800.0


def make_repeat(
    repeat_seconds: int = 1800,
    anchor_ts: float = T0 - 1,
    repeat_count: int = 0,
    fired_at: float | None = None,
    enabled: bool = True,
) -> Event:
    return Event(
        name="循环喝水",
        mode=EventMode.COUNTDOWN,
        anchor_ts=anchor_ts,
        enabled=enabled,
        fired_at=fired_at,
        repeat_seconds=repeat_seconds,
        repeat_count=repeat_count,
    )


class TestAdvanceRepeating(unittest.TestCase):
    """advance_repeating：核心推进规则。"""

    def test_advances_anchor_and_increments_count(self):
        """已到点（刚过 1 秒）：anchor 前进一个周期，repeat_count +1（案例 1）。"""
        # anchor 在 1 秒前到点、周期 1800s：next = now + 1799 > now，只推一轮
        event = make_repeat(repeat_seconds=1800, anchor_ts=T0 - 1)
        result = advance_repeating(event, now=T0)
        self.assertTrue(result)
        self.assertEqual(event.anchor_ts, T0 - 1 + 1800)
        self.assertEqual(event.repeat_count, 1)

    def test_skips_missed_periods_in_place(self):
        """错过 8 小时（30 分钟周期）：只跳一次到 now+周期（案例 2）。

        不应把 repeat_count 加 16 —— 那是启动补齐 / 运行期单次推进该有的行为，
        或在 mark_expired_as_fired 里（不累计）处理。advance_repeating 只推一轮。
        """
        event = make_repeat(repeat_seconds=1800, anchor_ts=T0 - 8 * HOUR)
        advance_repeating(event, now=T0)
        # 只前进一轮：下一轮锚点 = now + 周期（跳过所有已过的周期）
        self.assertEqual(event.anchor_ts, T0 + MIN30)
        # 运行期单次推进：count 只 +1（本函数不负责「补弹」）
        self.assertEqual(event.repeat_count, 1)

    def test_count_up_rejected(self):
        """正计时不推进：返回 False，对象不动。"""
        event = Event(
            name="正计",
            mode=EventMode.COUNT_UP,
            anchor_ts=T0 - 100,
            repeat_seconds=1800,
        )
        original = event.anchor_ts
        result = advance_repeating(event, now=T0)
        self.assertFalse(result)
        self.assertEqual(event.anchor_ts, original)
        self.assertEqual(event.repeat_count, 0)


class TestMultiCycleReminders(unittest.TestCase):
    """多周期连续提醒：循环事件必须每轮都响，而不是只响第一次。

    这条是本轮最关键的回归测试，它锁住一个真实的 P0 缺陷。

    真实根因已实测确认（不是猜的）：
        旧调用写的是 `advance_repeating(...); event.fired_at = now` —— 无条件标记。
        而 collect_due_events 以 fired_at is None 为判据，于是推进之后
        fired_at 仍非 None，下一周期直接不返回。
        实测对照：旧调用 due 序列 = [1, 0]（响一次就停）；
                  新调用 due 序列 = [1, 1, 1, 1, 1]（每轮都响）。

    所以修复的正确位置在**调用方**：只有 advance_repeating 返回 False
    （一次性 / 已达上限 / 正计时，即「没有下一轮」）时才标记 fired_at。
    advance_repeating 内部同时清 fired_at，是防止调用方遗漏的第二道保险；
    单独改任何一处都不足以修复（实测过：只改内部不清，行为完全不变）。

    为什么以前没测出来：之前只有「落后 8 小时只弹一次」「limit=3 只响 3 次」
    这类单周期断言；从没有一条测试连续跑多个周期、要求每轮都返回。
    单周期全绿，多周期从未被覆盖，缺陷因此长期潜伏。
    """

    def test_five_consecutive_cycles_all_remind(self):
        """每 60 秒、不限次数，连续跑 5 个周期 -> 响 5 次，count 1..5。"""
        event = Event(
            name="喝水",
            mode=EventMode.COUNTDOWN,
            anchor_ts=T0 + 2,
            display_format=DisplayFormat.FULL,
            repeat_seconds=60,
            repeat_count=0,
            enabled=True,
        )
        reminded = 0
        for step in range(5):
            now = T0 + 2 + step * 61   # 每轮都越过一个完整周期
            due = collect_due_events([event], now=now)
            self.assertEqual(len(due), 1, f"第 {step + 1} 个周期应当提醒一次")
            reminded += 1
            # 与 MainWindow.check_notifications 的真实调用方式一致：
            # 推进成功则内部已清 fired_at；不推进才标记完结。
            if not advance_repeating(event, now=now):
                event.fired_at = now
            else:
                self.assertIsNone(
                    event.fired_at,
                    f"第 {step + 1} 轮推进后 fired_at 必须被清空，否则下一轮不会被返回",
                )
            self.assertEqual(event.repeat_count, step + 1, "repeat_count 应逐轮累计")
        self.assertEqual(reminded, 5, "5 个周期必须响 5 次，而不是只响第一次")

    def test_fired_at_is_cleared_each_round(self):
        """advance_repeating 推进后必须把 fired_at 清空。"""
        event = Event(
            name="起身",
            mode=EventMode.COUNTDOWN,
            anchor_ts=T0,
            display_format=DisplayFormat.FULL,
            repeat_seconds=30,
            repeat_count=0,
        )
        for step in range(3):
            now = T0 + step * 31
            event.fired_at = now            # 模拟上一轮「已提醒」留下的痕迹
            self.assertEqual(len(collect_due_events([event], now=now)), 0,
                             "fired_at 非 None 时不应返回（防重复）")
            event.fired_at = None           # 人为清空后进入下一轮
            self.assertTrue(advance_repeating(event, now=now))
            self.assertIsNone(event.fired_at, "advance_repeating 必须把 fired_at 清空")




class TestRepeatIntegrationThroughMainWindow(unittest.TestCase):
    """端到端：走真实的 MainWindow.check_notifications，循环事件必须每轮都响。

    为什么必须走真实调用链：
        修 P0 时我犯过一个错 —— 只在 notifier 层写多周期测试，
        而真正的缺陷在**调用方**（main_window 里无条件 `fired_at = now`）。
        结果那条测试对注入的「调用方缺陷」完全免疫：注入后依然全绿。
        测试不经过出问题的那条链路，就等于没测。
    """

    _app = None

    @classmethod
    def setUpClass(cls):
        app = QApplication.instance()
        if app is None:
            cls._app = QApplication([])

    def setUp(self):
        # 独立 APPDATA：本用例会调 check_notifications，它会落盘。
        self._tmp = tempfile.TemporaryDirectory()
        self._old_appdata = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self._tmp.name

    def tearDown(self):
        if self._old_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old_appdata
        self._tmp.cleanup()

    def test_five_cycles_through_check_notifications(self):
        """连续 5 个周期走 check_notifications，每次都应发一次通知。"""
        import time as _time

        from ui.main_window import MainWindow

        start = _time.time()
        event = Event(
            name="喝水",
            mode=EventMode.COUNTDOWN,
            anchor_ts=start + 1,
            display_format=DisplayFormat.FULL,
            repeat_seconds=60,
            repeat_count=0,
            enabled=True,
        )
        window = MainWindow([event])
        fired = []
        window.notification_requested.connect(fired.append)
        window._last_fullscreen_check = 0.0

        reminded = 0
        for step in range(5):
            # 每次把 now 推到一个新周期之后；直接改事件 anchor 不方便，
            # 用 monkeypatch 控制 check_notifications 里的 time.time()。
            fake_now = start + 1 + step * 61
            # 只替换 main_window 模块里那个 time 的 time()，不污染真实全局时间。
            with mock.patch.object(mw_module, "time") as fake_time:
                fake_time.time.return_value = fake_now
                window.check_notifications()
            if len(fired) > reminded:
                reminded = len(fired)
        self.assertEqual(
            reminded, 5,
            f"5 个周期应发 5 次通知，实际 {reminded} 次（fired={fired}）",
        )
        self.assertEqual(event.repeat_count, 5, "repeat_count 应涨到 5")

class TestOneShot(unittest.TestCase):
    """一次性事件行为（案例 5 / 7）。"""

    def test_none_repeat_is_noop(self):
        """repeat_seconds=None（一次性）：advance 是空操作，返回 False（案例 5）。"""
        event = Event(
            name="一次性",
            mode=EventMode.COUNTDOWN,
            anchor_ts=T0 - 1,
            repeat_seconds=None,
        )
        original = event.anchor_ts
        result = advance_repeating(event, now=T0)
        self.assertFalse(result)
        self.assertEqual(event.anchor_ts, original)
        self.assertEqual(event.repeat_count, 0)

    def test_zero_repeat_is_noop(self):
        """repeat_seconds=0：当作一次性（不每秒提醒）（案例 7）。"""
        event = make_repeat(repeat_seconds=0, anchor_ts=T0 - 1)
        advance_repeating(event, now=T0)
        self.assertEqual(event.anchor_ts, T0 - 1)  # 原地不动
        self.assertEqual(event.repeat_count, 0)

    def test_negative_repeat_is_noop(self):
        """repeat_seconds 为负：当作一次性（不报错、不循环）（案例 7）。"""
        event = make_repeat(repeat_seconds=-300, anchor_ts=T0 - 1)
        advance_repeating(event, now=T0)
        self.assertEqual(event.anchor_ts, T0 - 1)
        self.assertEqual(event.repeat_count, 0)

    def test_no_repeat_until_repeat_seconds_passed(self):
        """零/负周期不会导致每秒都提醒：到点后 collect 仍只返回一次（由调用方标记）。"""
        event = make_repeat(repeat_seconds=0, anchor_ts=T0 - 1)
        self.assertEqual(collect_due_events([event], now=T0), [event])
        # 模拟调用方标记后，不会再弹（不会因为周期 0 而无限循环）
        event.fired_at = T0
        self.assertEqual(collect_due_events([event], now=T0), [])


class TestStartupScan(unittest.TestCase):
    """启动扫描 mark_expired_as_fired 的循环补齐（案例 8）。"""

    def test_startup_advances_anchor_not_count_then_empty(self):
        """8 小时前到点、周期 30 分钟：启动扫描把 anchor 推到未来，
        repeat_count 不变（0），且**不标记 fired_at** —— 否则事件永久静默。

        这条测试曾经把缺陷当成正确行为：它原先断言 `fired_at == T0`，
        等于固化「启动扫描后循环事件再也不提醒」。真实语义是：
        推进出的新周期尚未提醒，fired_at 必须保持 None；
        只有一次性 / 已达上限（没有下一轮）才标记完结。
        """
        event = make_repeat(repeat_seconds=1800, anchor_ts=T0 - 8 * HOUR)
        changed = mark_expired_as_fired([event], now=T0)
        self.assertTrue(changed)
        # anchor 推进到 now + 周期（跳过错过的所有周期）
        self.assertGreaterEqual(event.anchor_ts, T0 + MIN30)
        self.assertLess(event.anchor_ts, T0 + MIN30 + 1)
        # repeat_count 不变：启动补弹不算一次真实提醒
        self.assertEqual(event.repeat_count, 0)
        # 关键：推未来的新周期还没提醒过，fired_at 必须是 None
        self.assertIsNone(event.fired_at, "循环事件推进后不能标记 fired_at，否则永久静默")
        # 扫描当下不返回（anchor 已在未来）
        self.assertEqual(collect_due_events([event], now=T0), [])
        # 到达新 anchor 时必须能再次提醒 —— 这条才是「活着」的证据
        self.assertEqual(
            len(collect_due_events([event], now=event.anchor_ts + 1)), 1,
            "启动扫描后的循环事件到达新 anchor 时必须能再次提醒",
        )

    def test_one_shot_startup_unchanged_semantics(self):
        """一次性事件启动扫描：只标记 fired_at，不推进 anchor（原行为保留）。"""
        event = Event(
            name="一次性",
            mode=EventMode.COUNTDOWN,
            anchor_ts=T0 - 3600,
            repeat_seconds=None,
        )
        original = event.anchor_ts
        changed = mark_expired_as_fired([event], now=T0)
        self.assertTrue(changed)
        self.assertEqual(event.anchor_ts, original)  # 不推进
        self.assertEqual(event.fired_at, T0)


class TestRepeatLimit(unittest.TestCase):
    """repeat_limit：次数上限必须在「提醒」和「推进」两处同时生效。

    这条不是风格问题，是真实缺陷的回归测试：曾有版本只在 advance_repeating 里
    判上限，collect_due_events 照旧返回，结果次数用尽的事件每秒弹一次通知，
    永远不停（相当于把「无限循环」换了个形式）。
    """

    def test_stops_reminding_after_limit(self):
        """repeat_limit=3：连触发 5 次，只提醒 3 次，之后不再返回。"""
        event = make_repeat(repeat_count=0)
        event.repeat_limit = 3
        reminded = 0
        for step in range(5):
            now = T0 + step * MIN30
            if collect_due_events([event], now=now):
                reminded += 1
                advance_repeating(event, now=now)
                event.fired_at = now
            event.fired_at = None  # 模拟「下一秒重新检查」，不让 fired_at 挡住判定
        self.assertEqual(reminded, 3, "重复次数用尽后不应再提醒")
        self.assertEqual(event.repeat_count, 3)

    def test_exhausted_event_stays_out_of_due_list(self):
        """已达上限的事件留在列表里，但绝不参与提醒。

        计数必须属于「今天」：各入口先自检 ensure_daily_fresh，若 date 为空
        （旧数据形态）会被归零 —— 所以「已达上限」的夹具要带今天的日期，
        否则自检会把 count 清零，事件反而能提醒，测不出本意。
        """
        today = __import__("datetime").date.fromtimestamp(T0).isoformat()
        event = make_repeat(repeat_count=3, anchor_ts=T0 - 1)
        event.repeat_limit = 3
        event.repeat_count_date = today
        self.assertEqual(collect_due_events([event], now=T0), [])
        self.assertFalse(advance_repeating(event, now=T0))
        self.assertEqual(event.repeat_count, 3)

    def test_limit_none_is_infinite(self):
        event = make_repeat(repeat_count=5, anchor_ts=T0 - 1)
        event.repeat_limit = None
        self.assertEqual(len(collect_due_events([event], now=T0)), 1)

    def test_zero_or_negative_limit_treated_as_infinite(self):
        for limit in (0, -1):
            event = make_repeat(repeat_count=2, anchor_ts=T0 - 1)
            event.repeat_limit = limit
            self.assertEqual(
                len(collect_due_events([event], now=T0)), 1, f"limit={limit} 应按无限处理"
            )


class TestSerializationCompat(unittest.TestCase):
    """旧 JSON 兼容（案例 4）。"""

    def test_old_json_without_repeat_fields_loads_none_zero(self):
        """手工改坏的旧 JSON（无 repeat 字段）-> repeat_seconds=None / repeat_count=0。"""
        old = {
            "id": "abc",
            "name": "老事件",
            "mode": "countdown",
            "anchor_ts": T0,
            "display_format": "days_hours_minutes",
            "enabled": True,
            "show_on_desktop": True,
            "fired_at": None,
        }
        ev = Event.from_dict(json.loads(json.dumps(old)))
        self.assertIsNone(ev.repeat_seconds)
        self.assertEqual(ev.repeat_count, 0)

    def test_roundtrip_preserves_repeat_fields(self):
        """新 JSON 往返：repeat_seconds / repeat_count 保留。"""
        event = make_repeat(repeat_seconds=1800, repeat_count=3)
        data = json.loads(json.dumps(event.to_dict()))
        restored = Event.from_dict(data)
        self.assertEqual(restored.repeat_seconds, 1800)
        self.assertEqual(restored.repeat_count, 3)

    def test_invalid_repeat_values_fall_back(self):
        """非法 repeat 值（"abc" / 负数）不抛异常，回退 None / 按值解析。"""
        ev = Event.from_dict({
            "id": "x", "name": "x", "mode": "countdown", "anchor_ts": T0,
            "display_format": "days_hours_minutes", "enabled": True,
            "show_on_desktop": True, "fired_at": None,
            "repeat_seconds": "abc",
        })
        self.assertIsNone(ev.repeat_seconds)
        ev2 = Event.from_dict({
            "id": "x", "name": "x", "mode": "countdown", "anchor_ts": T0,
            "display_format": "days_hours_minutes", "enabled": True,
            "show_on_desktop": True, "fired_at": None,
            "repeat_seconds": "1800",
        })
        self.assertEqual(ev2.repeat_seconds, 1800)


class TestLargePeriod(unittest.TestCase):
    """极长周期形式检查（案例 9）。"""

    def test_365_days_no_overflow(self):
        """repeat_seconds=31536000（365 天）：Python int 不溢出，正常推进。"""
        event = make_repeat(repeat_seconds=31_536_000, anchor_ts=T0)
        result = advance_repeating(event, now=T0)
        self.assertTrue(result)
        self.assertEqual(event.anchor_ts, T0 + 31_536_000)
        self.assertEqual(event.repeat_count, 1)


class TestSnoozeRestart(unittest.TestCase):
    """snooze 对循环事件：从新 anchor 重新起算周期（与原 snooze 语义一致）。"""

    def test_snooze_restarts_period_from_new_anchor(self):
        """已到点的循环事件 snooze 后：anchor 变 now+minutes，fired_at 清空，
        期间不再到点，minutes 后再到点。"""
        event = make_repeat(repeat_seconds=1800, anchor_ts=T0 - 1800)
        res = snooze(event, 10, now=T0)
        self.assertIsNotNone(res)
        self.assertEqual(res.anchor_ts, T0 + 600)
        self.assertIsNone(res.fired_at)
        # 10 分钟内不返回；10 分钟后重新到点
        self.assertEqual(collect_due_events([res], now=T0), [])
        self.assertEqual(collect_due_events([res], now=T0 + 600), [res])


if __name__ == "__main__":
    unittest.main(verbosity=2)
