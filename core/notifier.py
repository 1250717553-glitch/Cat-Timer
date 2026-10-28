"""到点提醒的判定逻辑。

【纯函数模块】不 import 任何 GUI 库，便于单元测试。

两个核心场景：
1. 运行期：每秒调用 collect_due_events() 找出「本次该提醒」的事件。
2. 启动期：调用 mark_expired_as_fired() 把「早就过期但从未提醒过」的事件
   直接标记为已提醒 —— 这是实现「重启后不重复弹提醒」的关键。
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Optional

from core.models import Event, EventMode
from core.timer import is_expired


def collect_due_events(events: list[Event], now: Optional[float] = None) -> list[Event]:
    """返回本次应当提醒的事件列表。

    判定条件（全部满足）：
      - mode == COUNTDOWN（正计时不会到点，永不提醒）
      - enabled（停用的事件不打扰用户）
      - fired_at is None（还没提醒过，防重复）
      - 已到点（is_expired）

    :param events: 事件列表
    :param now: 当前时间戳（秒），默认取 time.time()
    """
    return [
        event
        for event in events
        if event.mode == EventMode.COUNTDOWN
        and event.enabled
        and event.fired_at is None
        and is_expired(event, now)
    ]


def mark_expired_as_fired(events: list[Event], now: Optional[float] = None) -> bool:
    """把所有「已到点但 fired_at is None」的倒计时直接标记为已提醒。

    【用途】程序启动时调用一次：
    用户关机期间到点的事件，重启后不应该补弹一堆过期提醒，
    直接标记掉即可实现「重启不重弹」。

    注意：这里**不检查 enabled** —— 停用的过期事件同样标记，
    否则用户之后重新启用它时会突然弹出一个陈旧的提醒。

    :param events: 事件列表（原地修改）
    :param now: 当前时间戳（秒），默认取 time.time()
    :return: 是否有事件被改动（True 表示调用方应当保存）
    """
    changed = False
    current = time.time() if now is None else now
    for event in events:
        if event.mode == EventMode.COUNTDOWN and event.fired_at is None and is_expired(event, current):
            event.fired_at = current
            changed = True
    return changed


def due_message(event: Event) -> str:
    """到点通知的正文文案（集中在这里，便于统一改动与测试）。"""
    return f"{event.name} 已到点"


def snooze(event: Event, minutes: int, now: Optional[float] = None) -> Optional[Event]:
    """把倒计时事件改为「从现在起 minutes 分钟后再次提醒」。

    返回**新的** Event（绝不原地改动传入对象）：
      - anchor_ts 前移到 now + minutes*60
      - fired_at 清空为 None —— 否则 collect_due_events 以 fired_at is None
        为判据，清不掉就永远不会再提醒
      - 其余字段原样保留

    仅对 COUNTDOWN 生效；非倒计时返回 None（调用方应忽略）。
    这里**不校验是否已到点**：是否“已到点”由界面把关（按钮只对已到点启用），
    纯函数只需保证「模式正确即可前移」。正计时的 anchor_ts 是起跑点，
    前移它会破坏语义，因此必须拒绝。

    :param event: 倒计时事件
    :param minutes: 稍后提醒的分钟数（正数）
    :param now: 当前时间戳（秒），默认取 time.time()，测试时可注入
    """
    if event.mode != EventMode.COUNTDOWN:
        return None
    current = time.time() if now is None else now
    return replace(event, anchor_ts=current + minutes * 60, fired_at=None)
