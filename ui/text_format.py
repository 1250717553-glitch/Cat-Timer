"""主窗口与悬浮窗共用的时间文案。

两个窗口的文案规则一致，但**前缀不同**：
    - 主窗口（表格）：带「还剩 / 已过」前缀，如「还剩 12天 05小时」
    - 悬浮窗（卡片）：不带前缀，如「12天 05小时」
所以这里拆成两个函数，共用同一套「已到点 / 倒计时 / 正计时」判定，
避免两处各写一份导致规则走偏。
"""

from __future__ import annotations

from typing import Optional

from core.models import Event, EventMode
from core.timer import format_event, is_expired

# 文案常量（集中在此，便于统一改词）
EXPIRED_TEXT = "已到点"
REMAINING_PREFIX = "还剩 "
ELAPSED_PREFIX = "已过 "


def event_time_text(event: Event, now: Optional[float] = None) -> str:
    """不带前缀的时间文案（悬浮窗用）。

    - 倒计时已到点 -> 「已到点」
    - 倒计时未到点 -> 格式化剩余时间
    - 正计时       -> 格式化已过时间
    """
    if event.mode == EventMode.COUNT_UP:
        return format_event(event, now)
    if is_expired(event, now):
        return EXPIRED_TEXT
    return format_event(event, now)


def event_time_text_with_prefix(event: Event, now: Optional[float] = None) -> str:
    """带「还剩 / 已过」前缀的时间文案（主窗口表格用）。"""
    if event.mode == EventMode.COUNT_UP:
        return f"{ELAPSED_PREFIX}{event_time_text(event, now)}"
    if is_expired(event, now):
        return EXPIRED_TEXT  # 「已到点」本身就是完整表述，不再加前缀
    return f"{REMAINING_PREFIX}{event_time_text(event, now)}"
