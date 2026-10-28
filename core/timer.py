"""计时计算与格式化层。

设计原则：
1. 纯函数模块，不持有任何实例状态，也不 import 任何 GUI 库。
2. 【关键】所有结果都用「锚点时间戳 − 当前时间」实时计算，
   绝不做「每秒减 1」的累加/递减 —— 那样在程序关闭、电脑休眠唤醒后必然出错。
3. now 参数用于测试注入，默认取 time.time()。
"""

from __future__ import annotations

import time
from typing import Optional

from core.models import DisplayFormat, Event, EventMode

# 时间单位换算常量（秒）
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 60 * 60
SECONDS_PER_DAY = 24 * 60 * 60


# ---------------------------------------------------------------------------
# 一、基础计算
# ---------------------------------------------------------------------------


def remaining_seconds(anchor_ts: float, now: Optional[float] = None) -> float:
    """倒计时用：返回距离目标时间还剩多少秒。

    结果不会为负：已过期时返回 0.0。

    :param anchor_ts: 目标时间戳（秒）
    :param now: 当前时间戳（秒），默认取 time.time()，测试时可注入
    """
    current = time.time() if now is None else now
    return max(0.0, anchor_ts - current)


def elapsed_seconds(anchor_ts: float, now: Optional[float] = None) -> float:
    """正计时用：返回从起始时间到现在已经过了多少秒。

    结果不会为负：起始时间在未来时返回 0.0。

    :param anchor_ts: 起始时间戳（秒）
    :param now: 当前时间戳（秒），默认取 time.time()，测试时可注入
    """
    current = time.time() if now is None else now
    return max(0.0, current - anchor_ts)


def compute_seconds(event: Event, now: Optional[float] = None) -> float:
    """统一入口：按 event.mode 分发到 remaining_seconds / elapsed_seconds。

    :param event: 事件对象
    :param now: 当前时间戳（秒），默认取 time.time()
    """
    if event.mode == EventMode.COUNT_UP:
        return elapsed_seconds(event.anchor_ts, now)
    return remaining_seconds(event.anchor_ts, now)


def is_expired(event: Event, now: Optional[float] = None) -> bool:
    """是否已到点（仅对倒计时有意义）。

    - COUNTDOWN：已到或已过目标时间 -> True（刚好归零也算已到点）
    - COUNT_UP：恒返回 False
    """
    if event.mode != EventMode.COUNTDOWN:
        return False
    return remaining_seconds(event.anchor_ts, now) <= 0.0


# ---------------------------------------------------------------------------
# 二、格式化
# ---------------------------------------------------------------------------


def format_duration(seconds: float, fmt: DisplayFormat) -> str:
    """把秒数按指定格式渲染成中文可读字符串。

    不加「剩余」/「已过」这类前缀，前缀由界面层决定。

    :param seconds: 秒数（负数会被当作 0 处理）
    :param fmt: 8 种显示格式之一
    """
    # 统一向下取整为整数秒，并夹到非负区间
    total = int(seconds) if seconds > 0 else 0

    if fmt == DisplayFormat.TOTAL_SECONDS:
        return f"{total}秒"
    if fmt == DisplayFormat.TOTAL_MINUTES:
        return f"{total // SECONDS_PER_MINUTE}分钟"
    if fmt == DisplayFormat.TOTAL_HOURS:
        return f"{total // SECONDS_PER_HOUR}小时"
    if fmt == DisplayFormat.TOTAL_DAYS:
        return f"{total // SECONDS_PER_DAY}天"

    # ---- 以下为分段式 ----
    days, rest = divmod(total, SECONDS_PER_DAY)
    hours, rest = divmod(rest, SECONDS_PER_HOUR)
    minutes, secs = divmod(rest, SECONDS_PER_MINUTE)

    if fmt == DisplayFormat.DAYS:
        return f"{days}天"
    if fmt == DisplayFormat.DAYS_HOURS:
        return f"{days}天 {hours:02d}小时"
    if fmt == DisplayFormat.DAYS_HOURS_MINUTES:
        return f"{days}天 {hours:02d}小时 {minutes:02d}分钟"
    if fmt == DisplayFormat.FULL:
        return f"{days}天 {hours:02d}小时 {minutes:02d}分钟 {secs:02d}秒"

    raise ValueError(f"未知的显示格式：{fmt!r}")


def format_event(event: Event, now: Optional[float] = None) -> str:
    """便捷函数：算 + 渲染一步到位（界面层可直接用）。"""
    return format_duration(compute_seconds(event, now), event.display_format)
