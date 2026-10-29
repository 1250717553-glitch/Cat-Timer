"""core 层单元测试（标准库 unittest，无第三方依赖）。

运行方式（在项目根目录执行）：
    python -m unittest discover -s tests -v
或只跑本文件：
    python -m unittest tests.test_timer -v

性能提示：
    正常情况下全部 59 个用例应在 1 秒内跑完（实测 0.15 秒）。
    如果发现耗时几十秒，那不是代码慢，而是所在环境（沙箱 / 杀软实时防护）
    对「删除文件」做了拦截 —— 存储类测试的耗时几乎全部来自 TemporaryDirectory
    的清理动作，每个文件约 1.4 秒。计时逻辑本身是纯计算，耗时约 0 秒。
"""


from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from core.models import DisplayFormat, Event, EventMode
from core.storage import SCHEMA_VERSION, get_config_path, load_events, save_events
from core.timer import (
    compute_seconds,
    elapsed_seconds,
    format_duration,
    is_expired,
    remaining_seconds,
)

# 基准用例：1056620 秒 = 12天5小时30分20秒
BASE_SECONDS = 1056620

# 测试用的固定时间戳，避免依赖真实时钟
T0 = 1_700_000_000.0
ONE_HOUR = 3600.0
ONE_DAY = 86400.0


# ---------------------------------------------------------------------------
# 一、倒计时
# ---------------------------------------------------------------------------


class TestRemainingSeconds(unittest.TestCase):
    """remaining_seconds：正常 / 刚好为 0 / 已过期"""

    def test_normal(self):
        """正常情况：目标在未来 1 小时。"""
        self.assertEqual(remaining_seconds(T0 + ONE_HOUR, now=T0), ONE_HOUR)

    def test_exactly_zero(self):
        """刚好为 0：目标时间 == 当前时间。"""
        self.assertEqual(remaining_seconds(T0, now=T0), 0.0)

    def test_expired_returns_zero(self):
        """已过期：必须夹到 0.0，不能返回负数。"""
        self.assertEqual(remaining_seconds(T0 - 10 * ONE_DAY, now=T0), 0.0)

    def test_float_precision(self):
        """小数秒也要保留，不做取整。"""
        self.assertAlmostEqual(remaining_seconds(T0 + 1.5, now=T0), 1.5, places=6)

    def test_default_now_uses_real_clock(self):
        """不传 now 时应使用真实时钟（锚点取真实时间 +1 天，只做量级断言）。"""
        value = remaining_seconds(time.time() + ONE_DAY)
        self.assertGreater(value, 0.0)
        self.assertLessEqual(value, ONE_DAY)


# ---------------------------------------------------------------------------
# 二、正计时
# ---------------------------------------------------------------------------


class TestElapsedSeconds(unittest.TestCase):
    """elapsed_seconds：正常 / 锚点在未来"""

    def test_normal(self):
        """正常情况：起始时间在 1 天前。"""
        self.assertEqual(elapsed_seconds(T0 - ONE_DAY, now=T0), ONE_DAY)

    def test_anchor_in_future_returns_zero(self):
        """锚点在未来：必须夹到 0.0，不能返回负数。"""
        self.assertEqual(elapsed_seconds(T0 + ONE_DAY, now=T0), 0.0)

    def test_anchor_equals_now(self):
        """锚点恰好等于当前时间 -> 0.0。"""
        self.assertEqual(elapsed_seconds(T0, now=T0), 0.0)


# ---------------------------------------------------------------------------
# 三、统一入口分发
# ---------------------------------------------------------------------------


class TestComputeSecondsDispatch(unittest.TestCase):
    """compute_seconds 必须按 mode 正确分发到两个函数。"""

    def test_countdown_dispatches_to_remaining(self):
        """COUNTDOWN -> 调用 remaining_seconds，且不调用 elapsed_seconds。"""
        event = Event(mode=EventMode.COUNTDOWN, anchor_ts=T0 + 60)
        with mock.patch("core.timer.remaining_seconds", return_value=111.0) as m_rem, \
                mock.patch("core.timer.elapsed_seconds", return_value=222.0) as m_ela:
            result = compute_seconds(event, now=T0)
        self.assertEqual(result, 111.0)
        m_rem.assert_called_once_with(T0 + 60, T0)
        m_ela.assert_not_called()

    def test_count_up_dispatches_to_elapsed(self):
        """COUNT_UP -> 调用 elapsed_seconds，且不调用 remaining_seconds。"""
        event = Event(mode=EventMode.COUNT_UP, anchor_ts=T0 - 60)
        with mock.patch("core.timer.remaining_seconds", return_value=111.0) as m_rem, \
                mock.patch("core.timer.elapsed_seconds", return_value=222.0) as m_ela:
            result = compute_seconds(event, now=T0)
        self.assertEqual(result, 222.0)
        m_ela.assert_called_once_with(T0 - 60, T0)
        m_rem.assert_not_called()

    def test_values_match_direct_calls(self):
        """数值一致性：分发结果 == 直接调用对应函数的结果。"""
        cd = Event(mode=EventMode.COUNTDOWN, anchor_ts=T0 + 120)
        cu = Event(mode=EventMode.COUNT_UP, anchor_ts=T0 - 120)
        self.assertEqual(compute_seconds(cd, now=T0), remaining_seconds(T0 + 120, now=T0))
        self.assertEqual(compute_seconds(cu, now=T0), elapsed_seconds(T0 - 120, now=T0))


# ---------------------------------------------------------------------------
# 四、格式化：8 种格式
# ---------------------------------------------------------------------------


class TestFormatDurationBase(unittest.TestCase):
    """用基准用例 1056620 秒（12天5小时30分20秒）覆盖全部 8 种格式。"""

    def test_days(self):
        self.assertEqual(format_duration(BASE_SECONDS, DisplayFormat.DAYS), "12天")

    def test_days_hours(self):
        self.assertEqual(format_duration(BASE_SECONDS, DisplayFormat.DAYS_HOURS), "12天 05小时")

    def test_days_hours_minutes(self):
        self.assertEqual(
            format_duration(BASE_SECONDS, DisplayFormat.DAYS_HOURS_MINUTES),
            "12天 05小时 30分钟",
        )

    def test_full(self):
        self.assertEqual(
            format_duration(BASE_SECONDS, DisplayFormat.FULL),
            "12天 05小时 30分钟 20秒",
        )

    def test_total_seconds(self):
        self.assertEqual(format_duration(BASE_SECONDS, DisplayFormat.TOTAL_SECONDS), "1056620秒")

    def test_total_minutes(self):
        self.assertEqual(format_duration(BASE_SECONDS, DisplayFormat.TOTAL_MINUTES), "17610分钟")

    def test_total_hours(self):
        self.assertEqual(format_duration(BASE_SECONDS, DisplayFormat.TOTAL_HOURS), "293小时")

    def test_total_days(self):
        self.assertEqual(format_duration(BASE_SECONDS, DisplayFormat.TOTAL_DAYS), "12天")

    def test_no_prefix(self):
        """不允许出现「剩余」「已过」这类前缀，前缀由界面层决定。"""
        for fmt in DisplayFormat:
            text = format_duration(BASE_SECONDS, fmt)
            self.assertNotIn("剩余", text)
            self.assertNotIn("已过", text)


class TestFormatFloor(unittest.TestCase):
    """单单位式必须向下取整，不能四舍五入。"""

    def test_minutes_floor_not_round(self):
        """1056620 秒 -> 17610 分钟（17610.33 向下取整，不是 17611）。"""
        self.assertEqual(format_duration(BASE_SECONDS, DisplayFormat.TOTAL_MINUTES), "17610分钟")

    def test_seconds_floor_in_segmented(self):
        """分段式里的秒同样向下取整：100.9 秒 = 1分40.9秒 -> 秒位显示 40，不是 41。"""
        self.assertEqual(format_duration(100.9, DisplayFormat.FULL), "0天 00小时 01分钟 40秒")

    def test_minutes_just_below_next_unit(self):
        """3599 秒 = 59 分钟 59 秒 -> 总分钟应为 59，不能进位到 60。"""
        self.assertEqual(format_duration(3599, DisplayFormat.TOTAL_MINUTES), "59分钟")

    def test_hours_just_below_next_unit(self):
        """86399 秒 = 23:59:59 -> 总小时 23，总天 0。"""
        self.assertEqual(format_duration(86399, DisplayFormat.TOTAL_HOURS), "23小时")
        self.assertEqual(format_duration(86399, DisplayFormat.TOTAL_DAYS), "0天")

    def test_total_days_floor(self):
        """12 天多一点点仍是 12 天。"""
        self.assertEqual(format_duration(12 * ONE_DAY + 1, DisplayFormat.TOTAL_DAYS), "12天")


class TestFormatPadding(unittest.TestCase):
    """分段式的补零逻辑。"""

    def test_hours_padded_to_two_digits(self):
        """5 小时应显示 "05小时"。"""
        self.assertEqual(format_duration(5 * 3600, DisplayFormat.DAYS_HOURS), "0天 05小时")

    def test_minutes_padded(self):
        self.assertEqual(
            format_duration(5 * 60, DisplayFormat.DAYS_HOURS_MINUTES),
            "0天 00小时 05分钟",
        )

    def test_seconds_padded(self):
        self.assertEqual(format_duration(5, DisplayFormat.FULL), "0天 00小时 00分钟 05秒")

    def test_days_not_padded(self):
        """天不补零。"""
        self.assertEqual(format_duration(5 * ONE_DAY, DisplayFormat.DAYS), "5天")

    def test_all_units_padded_together(self):
        """1天2小时3分4秒 -> 各段都补齐两位。"""
        secs = ONE_DAY + 2 * 3600 + 3 * 60 + 4
        self.assertEqual(format_duration(secs, DisplayFormat.FULL), "1天 02小时 03分钟 04秒")


class TestFormatBoundaries(unittest.TestCase):
    """边界：跨天、恰好整点、0 秒、负数。"""

    def test_cross_day_25h30m(self):
        """25 小时 30 分 = 1天1小时30分。"""
        secs = 25 * 3600 + 30 * 60
        self.assertEqual(format_duration(secs, DisplayFormat.DAYS_HOURS), "1天 01小时")
        self.assertEqual(
            format_duration(secs, DisplayFormat.FULL), "1天 01小时 30分钟 00秒"
        )

    def test_exactly_on_hour(self):
        """恰好整点：2 小时整。"""
        self.assertEqual(
            format_duration(2 * 3600, DisplayFormat.FULL), "0天 02小时 00分钟 00秒"
        )

    def test_exactly_on_day(self):
        """恰好 1 天整。"""
        self.assertEqual(
            format_duration(ONE_DAY, DisplayFormat.FULL), "1天 00小时 00分钟 00秒"
        )
        self.assertEqual(format_duration(ONE_DAY, DisplayFormat.TOTAL_HOURS), "24小时")

    def test_zero_seconds(self):
        """0 秒：DAYS 与 TOTAL_DAYS 返回 "0天"，其余返回对应零值。"""
        self.assertEqual(format_duration(0, DisplayFormat.DAYS), "0天")
        self.assertEqual(format_duration(0, DisplayFormat.TOTAL_DAYS), "0天")
        self.assertEqual(format_duration(0, DisplayFormat.DAYS_HOURS), "0天 00小时")
        self.assertEqual(
            format_duration(0, DisplayFormat.DAYS_HOURS_MINUTES), "0天 00小时 00分钟"
        )
        self.assertEqual(
            format_duration(0, DisplayFormat.FULL), "0天 00小时 00分钟 00秒"
        )
        self.assertEqual(format_duration(0, DisplayFormat.TOTAL_SECONDS), "0秒")
        self.assertEqual(format_duration(0, DisplayFormat.TOTAL_MINUTES), "0分钟")
        self.assertEqual(format_duration(0, DisplayFormat.TOTAL_HOURS), "0小时")

    def test_negative_seconds_clamped_to_zero(self):
        """负数按 0 处理，不出现 "-1秒" 这种输出。"""
        self.assertEqual(format_duration(-5, DisplayFormat.FULL), "0天 00小时 00分钟 00秒")
        self.assertEqual(format_duration(-5, DisplayFormat.TOTAL_SECONDS), "0秒")


# ---------------------------------------------------------------------------
# 五、is_expired
# ---------------------------------------------------------------------------


class TestIsExpired(unittest.TestCase):
    """倒计时过期 -> True；正计时恒 False。"""

    def test_countdown_expired(self):
        event = Event(mode=EventMode.COUNTDOWN, anchor_ts=T0 - 1)
        self.assertTrue(is_expired(event, now=T0))

    def test_countdown_exactly_now(self):
        """刚好归零也算已到点。"""
        event = Event(mode=EventMode.COUNTDOWN, anchor_ts=T0)
        self.assertTrue(is_expired(event, now=T0))

    def test_countdown_not_yet(self):
        event = Event(mode=EventMode.COUNTDOWN, anchor_ts=T0 + 1)
        self.assertFalse(is_expired(event, now=T0))

    def test_count_up_always_false(self):
        """正计时无论锚点在过去多久都返回 False。"""
        past = Event(mode=EventMode.COUNT_UP, anchor_ts=T0 - 10 * ONE_DAY)
        future = Event(mode=EventMode.COUNT_UP, anchor_ts=T0 + 10 * ONE_DAY)
        self.assertFalse(is_expired(past, now=T0))
        self.assertFalse(is_expired(future, now=T0))


# ---------------------------------------------------------------------------
# 六、关键回归测试：模拟「程序关闭 1 小时后再打开」
# ---------------------------------------------------------------------------


class TestRegressionRestartAfterOneHour(unittest.TestCase):
    """用注入的 now 模拟程序关闭 1 小时后重新打开。

    如果实现里用了「每秒减 1」的累加逻辑，关闭期间没有 tick，
    结果就会停留在关闭前那一刻，从而与下面的期望值不符。
    """

    def test_countdown_recomputes_after_restart(self):
        anchor = T0 + 3 * ONE_HOUR  # 目标时间：T0 之后 3 小时

        # 关闭前：还剩 3 小时
        self.assertEqual(remaining_seconds(anchor, now=T0), 3 * ONE_HOUR)

        # 1 小时后重新打开（期间程序未运行）：应实时算出还剩 2 小时
        self.assertEqual(remaining_seconds(anchor, now=T0 + ONE_HOUR), 2 * ONE_HOUR)

        # 再 3 小时后：已过期，夹到 0.0
        self.assertEqual(remaining_seconds(anchor, now=T0 + 5 * ONE_HOUR), 0.0)

    def test_count_up_recomputes_after_restart(self):
        anchor = T0 - 2 * ONE_HOUR  # 起始时间：T0 之前 2 小时

        # 关闭前：已过 2 小时
        self.assertEqual(elapsed_seconds(anchor, now=T0), 2 * ONE_HOUR)

        # 1 小时后重新打开：应实时算出已过 3 小时（而不是 2 小时 + 少量 tick）
        self.assertEqual(elapsed_seconds(anchor, now=T0 + ONE_HOUR), 3 * ONE_HOUR)

    def test_restart_across_sleep_of_days(self):
        """休眠/关机跨越多天同样正确（100 天后重新打开）。"""
        anchor = T0 + 12 * ONE_DAY
        later = T0 + 100 * ONE_DAY
        self.assertEqual(remaining_seconds(anchor, now=later), 0.0)
        self.assertEqual(
            format_duration(elapsed_seconds(T0, now=later), DisplayFormat.TOTAL_DAYS), "100天"
        )


# ---------------------------------------------------------------------------
# 七、数据模型
# ---------------------------------------------------------------------------


class TestEventModel(unittest.TestCase):
    """Event 的序列化与便捷构造。"""

    def test_defaults(self):
        e = Event(name="考试")
        self.assertTrue(e.id)  # 自动生成 uuid
        self.assertEqual(e.mode, EventMode.COUNTDOWN)
        self.assertEqual(e.display_format, DisplayFormat.DAYS_HOURS_MINUTES)
        self.assertTrue(e.enabled)
        self.assertTrue(e.show_on_desktop)
        self.assertIsNone(e.fired_at)

    def test_ids_are_unique(self):
        self.assertNotEqual(Event().id, Event().id)

    def test_to_dict_uses_string_enums(self):
        e = Event(name="考试", mode=EventMode.COUNT_UP, display_format=DisplayFormat.FULL)
        d = e.to_dict()
        self.assertEqual(d["mode"], "count_up")
        self.assertEqual(d["display_format"], "full")
        # 必须是字符串才能被 json 直接序列化
        json.dumps(d)

    def test_roundtrip(self):
        e = Event(
            name="在一起",
            mode=EventMode.COUNT_UP,
            anchor_ts=T0,
            display_format=DisplayFormat.TOTAL_DAYS,
            enabled=False,
            show_on_desktop=False,
            fired_at=None,
        )
        restored = Event.from_dict(e.to_dict())
        self.assertEqual(restored, e)
        self.assertIsInstance(restored.mode, EventMode)
        self.assertIsInstance(restored.display_format, DisplayFormat)

    def test_from_dict_tolerates_bad_values(self):
        """手工改坏 JSON（非法枚举 / 缺字段）也不应抛异常。"""
        restored = Event.from_dict({"name": "x", "mode": "???", "display_format": "???"})
        self.assertEqual(restored.mode, EventMode.COUNTDOWN)
        self.assertEqual(restored.display_format, DisplayFormat.DAYS_HOURS_MINUTES)
        self.assertEqual(restored.anchor_ts, 0.0)

    def test_from_datetime_string(self):
        e = Event.from_datetime_string("2026-10-20", "15:30", name="考试")
        expected = datetime(2026, 10, 20, 15, 30).timestamp()
        self.assertAlmostEqual(e.anchor_ts, expected, places=6)
        self.assertEqual(e.name, "考试")
        self.assertEqual(e.mode, EventMode.COUNTDOWN)
        self.assertEqual(datetime.fromtimestamp(e.anchor_ts).hour, 15)

    def test_from_datetime_string_with_seconds_and_mode(self):
        e = Event.from_datetime_string("2026/10/20", "15:30:45", mode=EventMode.COUNT_UP)
        expected = datetime(2026, 10, 20, 15, 30, 45).timestamp()
        self.assertAlmostEqual(e.anchor_ts, expected, places=6)
        self.assertEqual(e.mode, EventMode.COUNT_UP)

    def test_from_datetime_string_rejects_garbage(self):
        with self.assertRaises(ValueError):
            Event.from_datetime_string("not-a-date", "15:30")


# ---------------------------------------------------------------------------
# 七之二、from_dict 容错（回归：非法数字 + 字符串布尔值）
# ---------------------------------------------------------------------------


class TestModelTolerance(unittest.TestCase):
    """from_dict 对脏数据的容错能力。

    覆盖两类历史缺陷：
      1. anchor_ts 为 "abc" 时 float() 抛 ValueError；
      2. enabled 为字符串 "false" 时 bool("false") == True，开关反向失效。
    """

    # ---------- anchor_ts ----------

    def test_anchor_ts_garbage_string_falls_back_to_zero(self):
        """anchor_ts 为 "abc" -> 不抛异常，回退为 0.0。"""
        e = Event.from_dict({"anchor_ts": "abc"})
        self.assertEqual(e.anchor_ts, 0.0)

    def test_anchor_ts_none_falls_back_to_zero(self):
        """anchor_ts 显式为 None -> 0.0。"""
        self.assertEqual(Event.from_dict({"anchor_ts": None}).anchor_ts, 0.0)

    def test_anchor_ts_missing_falls_back_to_zero(self):
        """anchor_ts 字段缺失 -> 0.0。"""
        self.assertEqual(Event.from_dict({}).anchor_ts, 0.0)

    def test_anchor_ts_numeric_string_is_parsed(self):
        """anchor_ts 为 "123.5" -> 123.5（字符串数字能解析）。"""
        self.assertEqual(Event.from_dict({"anchor_ts": "123.5"}).anchor_ts, 123.5)

    def test_anchor_ts_whitespace_numeric_string_is_parsed(self):
        """带前后空白的数字字符串也能解析。"""
        self.assertEqual(Event.from_dict({"anchor_ts": " 123.5 "}).anchor_ts, 123.5)

    def test_anchor_ts_numeric_types(self):
        """int / float / 数字字符串三种写法结果一致。"""
        self.assertEqual(Event.from_dict({"anchor_ts": 1000}).anchor_ts, 1000.0)
        self.assertEqual(Event.from_dict({"anchor_ts": 1000.5}).anchor_ts, 1000.5)
        self.assertEqual(Event.from_dict({"anchor_ts": "1000"}).anchor_ts, 1000.0)

    def test_anchor_ts_other_bad_types(self):
        """列表 / 字典等非法类型同样回退为 0.0，不抛异常。"""
        self.assertEqual(Event.from_dict({"anchor_ts": [1, 2]}).anchor_ts, 0.0)
        self.assertEqual(Event.from_dict({"anchor_ts": {"a": 1}}).anchor_ts, 0.0)
        self.assertEqual(Event.from_dict({"anchor_ts": ""}).anchor_ts, 0.0)

    # ---------- enabled ----------

    def test_enabled_falsy_strings(self):
        """enabled 为各种「假」字符串 -> False。"""
        for value in ("false", "False", "FALSE", "0", "no", "off", "OFF", "n", "", " false ", "NO", " off "):
            with self.subTest(value=value):
                self.assertFalse(Event.from_dict({"enabled": value}).enabled)

    def test_enabled_truthy_strings(self):
        """enabled 为 "true" / "1" -> True。"""
        for value in ("true", "True", "TRUE", "1", "yes", " yes "):
            with self.subTest(value=value):
                self.assertTrue(Event.from_dict({"enabled": value}).enabled)

    def test_enabled_real_bool(self):
        """原生布尔值不受影响。"""
        self.assertTrue(Event.from_dict({"enabled": True}).enabled)
        self.assertFalse(Event.from_dict({"enabled": False}).enabled)

    def test_enabled_missing_defaults_to_true(self):
        """enabled 缺失 -> 默认 True。"""
        self.assertTrue(Event.from_dict({}).enabled)

    def test_enabled_none_defaults_to_true(self):
        """enabled 为 null -> 默认 True。"""
        self.assertTrue(Event.from_dict({"enabled": None}).enabled)

    def test_enabled_numbers(self):
        """数字 0 -> False，非 0 -> True（走 bool() 分支）。"""
        self.assertFalse(Event.from_dict({"enabled": 0}).enabled)
        self.assertTrue(Event.from_dict({"enabled": 1}).enabled)

    # ---------- show_on_desktop ----------

    def test_show_on_desktop_falsy_string(self):
        """show_on_desktop 为 "false" -> False。"""
        self.assertFalse(Event.from_dict({"show_on_desktop": "false"}).show_on_desktop)

    def test_show_on_desktop_truthy_and_default(self):
        """show_on_desktop 为 "true" -> True；缺失 -> True。"""
        self.assertTrue(Event.from_dict({"show_on_desktop": "true"}).show_on_desktop)
        self.assertTrue(Event.from_dict({}).show_on_desktop)

    # ---------- fired_at ----------

    def test_fired_at_garbage_string_falls_back_to_none(self):
        """fired_at 为 "abc" -> 不抛异常，回退为 None。"""
        self.assertIsNone(Event.from_dict({"fired_at": "abc"}).fired_at)

    def test_fired_at_numeric_string_is_parsed(self):
        """fired_at 为 "1000.5" -> 1000.5。"""
        self.assertEqual(Event.from_dict({"fired_at": "1000.5"}).fired_at, 1000.5)

    def test_fired_at_none_stays_none(self):
        """fired_at 为 None -> None。"""
        self.assertIsNone(Event.from_dict({"fired_at": None}).fired_at)

    def test_fired_at_missing_stays_none(self):
        """fired_at 缺失 -> None。"""
        self.assertIsNone(Event.from_dict({}).fired_at)

    def test_fired_at_real_number(self):
        """fired_at 为原生数字 -> float。"""
        self.assertEqual(Event.from_dict({"fired_at": 1000}).fired_at, 1000.0)
        self.assertEqual(Event.from_dict({"fired_at": 1000.5}).fired_at, 1000.5)

    def test_fired_at_other_bad_types(self):
        """列表 / 字典等非法类型回退为 None。"""
        self.assertIsNone(Event.from_dict({"fired_at": [1]}).fired_at)
        self.assertIsNone(Event.from_dict({"fired_at": ""}).fired_at)

    # ---------- 综合：全字段脏数据不崩溃 ----------

    def test_all_fields_garbage_does_not_raise(self):
        """整条记录全部是脏数据时也不抛异常，且各字段回退到合理默认值。"""
        e = Event.from_dict(
            {
                "id": None,
                "name": None,
                "mode": "???",
                "anchor_ts": "abc",
                "display_format": "???",
                "enabled": "false",
                "show_on_desktop": "0",
                "fired_at": "abc",
            }
        )
        self.assertTrue(e.id)  # 自动补 uuid
        self.assertEqual(e.name, "")
        self.assertEqual(e.mode, EventMode.COUNTDOWN)
        self.assertEqual(e.anchor_ts, 0.0)
        self.assertEqual(e.display_format, DisplayFormat.DAYS_HOURS_MINUTES)
        self.assertFalse(e.enabled)
        self.assertFalse(e.show_on_desktop)
        self.assertIsNone(e.fired_at)

    # ---------- 回归：正常往返 ----------

    def test_roundtrip_still_consistent(self):
        """回归：完整 to_dict -> from_dict 往返仍然一致。"""
        original = Event(
            name="在一起",
            mode=EventMode.COUNT_UP,
            anchor_ts=1_700_000_000.0,
            display_format=DisplayFormat.TOTAL_DAYS,
            enabled=False,
            show_on_desktop=False,
            fired_at=None,
        )
        restored = Event.from_dict(original.to_dict())
        self.assertEqual(restored, original)

    def test_roundtrip_with_fired_at(self):
        """回归：带 fired_at 的往返一致。"""
        original = Event(name="考试", anchor_ts=1_700_000_000.0, fired_at=1_700_000_100.5)
        restored = Event.from_dict(original.to_dict())
        self.assertEqual(restored, original)
        self.assertEqual(restored.fired_at, 1_700_000_100.5)

    def test_roundtrip_survives_json_serialization(self):
        """回归：经过真实 json.dumps/loads 往返后仍一致（字符串化路径）。"""
        original = Event(name="考试", anchor_ts=1_700_000_000.0, fired_at=1_700_000_100.0)
        restored = Event.from_dict(json.loads(json.dumps(original.to_dict())))
        self.assertEqual(restored, original)


# ---------------------------------------------------------------------------
# 八、持久化
# ---------------------------------------------------------------------------


class TestStorage(unittest.TestCase):
    """JSON 持久化：路径、原子写、容错。

    隔离策略：整个测试类只建一个临时根目录，每个用例用独立子目录隔离
    （子目录以测试方法名命名），互不污染。
    注意：Windows 上删除文件会被杀软实时扫描，单个文件删除可能耗时 1 秒以上，
    因此测试期间不做删除，只在整类结束时清理一次临时根目录。
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp_root = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls._tmp_root.cleanup()

    def setUp(self):
        # 用测试方法名做子目录名，天然唯一且互不污染（mkdir 很快）
        self._appdata = Path(self._tmp_root.name) / self._testMethodName
        self._appdata.mkdir(parents=True, exist_ok=True)
        self._env_patch = mock.patch.dict(
            os.environ, {"APPDATA": str(self._appdata)}, clear=False
        )
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()

    def test_config_path_under_appdata(self):
        path = get_config_path()
        self.assertTrue(str(path).startswith(str(self._appdata)))
        self.assertEqual(path.name, "config.json")
        self.assertTrue(path.parent.exists())  # 目录自动创建

    def test_load_events_missing_file_returns_empty(self):
        """文件不存在 -> 空列表，不报错。"""
        self.assertEqual(load_events(), [])

    def test_save_then_load_roundtrip(self):
        events = [
            Event(name="考试", mode=EventMode.COUNTDOWN, anchor_ts=T0 + ONE_DAY),
            Event(
                name="在一起",
                mode=EventMode.COUNT_UP,
                anchor_ts=T0 - 365 * ONE_DAY,
                display_format=DisplayFormat.TOTAL_DAYS,
            ),
        ]
        save_events(events)
        loaded = load_events()
        self.assertEqual(loaded, events)

    def test_file_contains_schema_version(self):
        save_events([Event(name="考试")])
        with open(get_config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertEqual(len(data["events"]), 1)

    def test_atomic_write_leaves_no_tmp_file(self):
        """原子写完成后不应残留 .tmp 文件。"""
        save_events([Event(name="考试")])
        tmp = get_config_path().with_name("config.json.tmp")
        self.assertFalse(tmp.exists())
        self.assertTrue(get_config_path().exists())

    def test_save_overwrites_previous_content(self):
        save_events([Event(name="A")])
        save_events([Event(name="B")])
        loaded = load_events()
        self.assertEqual([e.name for e in loaded], ["B"])

    def test_corrupt_json_returns_empty_without_crash(self):
        """JSON 解析失败 -> 空列表 + 警告，不崩溃。"""
        path = get_config_path()
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ this is not valid json ")
        with mock.patch("builtins.print") as m_print:
            self.assertEqual(load_events(), [])
        self.assertTrue(m_print.called)  # 打印了警告

    def test_skips_malformed_event_entry(self):
        """单条记录坏掉时跳过它，其余正常读出。"""
        path = get_config_path()
        good = Event(name="好的").to_dict()
        payload = {"schema_version": SCHEMA_VERSION, "events": [good, "坏数据", 42]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        loaded = load_events()
        self.assertEqual([e.name for e in loaded], ["好的"])

    def test_non_dict_top_level_returns_empty(self):
        path = get_config_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        self.assertEqual(load_events(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
