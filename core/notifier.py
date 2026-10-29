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
from datetime import date, datetime
from typing import Optional

from core.models import Event, EventMode
from core.timer import is_expired


def repeated_next_anchor(anchor_ts: float, repeat_seconds: int, now: float) -> float:
    """计算循环事件的下一次锚点时间戳（秒）。

    核心规则：跳过所有已经过去的周期，绝不在动一轮。
        next = anchor + repeat_seconds
        若 next 已经 ≤ now（说明错过了至少一个周期），直接置为 now + repeat_seconds。
    这样电脑休眠 8 小时、周期 30 分钟时，恢复后只会从「现在 + 30 分钟」重新起算，
    绝不会一口气补弹 16 次提醒。

    :param anchor_ts: 当前锚点（上次到点时间）
    :param repeat_seconds: 重复周期（秒），**必须为正整数**（调用方先做 <=0 防御）
    :param now: 当前时间戳
    """
    next_anchor = anchor_ts + repeat_seconds
    if next_anchor <= now:
        next_anchor = now + repeat_seconds
    return next_anchor



def _is_repeating(event: Event) -> bool:
    """该事件是否为「有效的循环事件」。

    与 advance_repeating / collect_due_events 对 repeat_seconds 的防御保持一致：
    只有正数周期才算循环；None / 0 / 负数(手工构造)都按一次性处理。
    集中成一个判断，避免几处各自写一遍将来不一致。
    """
    return event.mode == EventMode.COUNTDOWN and event.repeat_seconds is not None and event.repeat_seconds > 0

def at_repeat_limit(event: Event) -> bool:
    """该循环事件是否已经用满次数上限。

    上限的语义：`repeat_limit=5` 表示「一共提醒 5 次」。
    第 5 次到点时 repeat_count 从 4 推进到 5；第 6 次到点发现 5 >= 5 就停住。

    repeat_limit 为 None / 0 / 负数一律视为无限（模型的兜底已经把 0 和负数
    归一成 None，这里再判一次是防止有人手工构造 Event）。
    """
    limit = event.repeat_limit
    if limit is None or limit <= 0:
        return False
    return event.repeat_count >= limit


def ensure_daily_fresh(event: Event, now: Optional[float] = None) -> bool:
    """确保该事件的每日计数属于「今天」。同一天调用无副作用（幂等）。

    为什么要有这个「自检」而不是让调用方记得先调 reset_daily_counters：
        正确性如果靠「每个入口都记得先重置」，那么明天新增第三个入口
        （比如托盘里加个「立即检查」）就会漏，而且漏了是静默的。
        改成在每个**读 repeat_count 的地方**先自检，漏调就会立刻表现为
        「次数用满的事件当天不提醒」，测试能抓住。
        幂等是这条设计的支点：调早了没关系（只是确认日期），调重了没关系
        （第二次起直接返回 False），所以不需要关心调用顺序。

    :param event: 事件（原地修改，与 mark_expired_as_fired 风格一致）
    :param now: 当前时间戳（秒），默认取 time.time()
    :return: 是否发生了跨天重置（调用方据此决定要不要落盘）
    """
    if not _is_repeating(event):
        return False
    current = time.time() if now is None else now
    today = date.fromtimestamp(current).isoformat()
    if event.repeat_count_date == today:
        return False
    event.repeat_count = 0
    event.repeat_count_date = today
    return True


def _reaching_limit_today(event: Event, now: float) -> bool:
    """该循环事件的次数上限「在今天」是否已用尽（纯查询，不改事件）。

    昨天用满 5/5、今天还没归零的缓存：对今天而言上限并未用尽，应能提醒。
    真正的归零由 ensure_daily_fresh / reset_daily_counters 在写路径上完成。

    repeat_count_date 为空（旧数据没有这个字段）：视为「日期未知」，
    按今天的计数处理 —— 否则老用户升级后，用满次数的事件会突然变回能提醒，
    与他们看到的状态不一致。归零留给 ensure_daily_fresh 在写路径上补。
    """
    today = date.fromtimestamp(now).isoformat()
    if event.repeat_count_date and event.repeat_count_date != today:
        return False
    return at_repeat_limit(event)


def reset_daily_counters(events: list[Event], now: Optional[float] = None) -> bool:
    """跨天时把循环事件的重置计数归零。返回是否有改动（调用方据此落盘）。

    【正确性来源 = 各读写点自检，不是本函数】每个读/写 repeat_count 的入口
    （mark_expired_as_fired / collect_due_events / advance_repeating）第一行都会
    先调用幂等的 ensure_daily_fresh，拿到「今天」的状态。这里是纯批量性能路径：
    同一批事件集中归零一次，避免每个入口各跑一遍；顺序因此不再关键，漏调才有害。

    设计决定（勿当 bug 修）：旧数据没有 repeat_count_date 字段 -> 读出 ""。
    首次调用会把 count 归零并记为今天 —— 老用户升级后第一次启动，所有循环事件
    count 归零一次是**可接受的**（当天本来就该重新开始）。

    :param events: 事件列表（原地修改）
    :param now: 当前时间戳（秒），默认取 time.time()；测试可注入
    :return: True 表示有事件被改动（调用方应当落盘）
    """
    changed = False
    current = time.time() if now is None else now
    for event in events:
        # 只对循环事件生效；ensure_daily_fresh 内部已判 _is_repeating。
        if ensure_daily_fresh(event, current):
            changed = True
    return changed


def advance_repeating(event: Event, now: Optional[float] = None) -> bool:
    """把「已到点的循环事件」原地推进到下一次提醒（有列表上下文，与 mark_expired_as_fired 一致）。

    【原地修改】直接改 event 的 anchor_ts 与 repeat_count，并把 fired_at 清空。
    调用方只在 advance_repeating 返回 False（一次性 / 已达上限）时才把
    fired_at 置为当前时间，表示「完结」；返回 True 时它是「本轮已提醒」的
    历史痕迹，必须被清掉，否则下一周期不会被再次返回。

    行为：
      - mode 不是 COUNTDOWN -> 不动，返回 False（正计时没有「到点再循环」）
      - repeat_seconds 是 None 或 <= 0 -> 一次性事件，不动，返回 False（产品判断：
        一次性事件由 fired_at 完结，没有需要推进的周期，advance 对它天然是空操作）
      - 已达 repeat_limit 上限 -> 不动，返回 False（事件停在「已到点」，界面显示「已完成」）
      - 正常循环事件 -> 原地推进 anchor_ts（跳过错过的周期）、repeat_count += 1，返回 True

    :param event: 待推进的循环事件（原地修改）
    :param now: 当前时间戳（秒），默认取 time.time()
    :return: True 表示真正推进了（重复事件）；False 表示是空操作（一次性 / 非倒计时 / 已达上限）
    """
    if event.mode != EventMode.COUNTDOWN:
        return False
    if event.repeat_seconds is None or event.repeat_seconds <= 0:
        return False
    current = time.time() if now is None else now
    # 读 repeat_count（上限判定）前先自检：让计数属于「今天」。幂等设计
    # （ensure_daily_fresh 同天第二次返回 False）保证这里的自检与
    # mark_expired_as_fired / collect_due_events 的先后无关；漏调才是有害的。
    ensure_daily_fresh(event, current)
    if at_repeat_limit(event):
        return False
    event.anchor_ts = repeated_next_anchor(event.anchor_ts, event.repeat_seconds, current)
    event.repeat_count += 1
    # 必须清掉 fired_at：它表示「这一轮已提醒」，而新周期还没提醒过。
    # 不清的后果是循环事件只响第一次 —— collect_due_events 以 fired_at is None
    # 为判据，永久非 None 就等于永久静默。这是「喝水提醒」跑不起来的根因。
    event.fired_at = None
    return True



def is_within_active_hours(event: Event, now: Optional[float] = None) -> bool:
    """该事件当前是否处于「提醒时间段」内。纯函数，便于单测。

    规则（两个字段都在 core/models.Event 上，空字符串 = 不限）：
      - 两个都为空 / 只有一个为空 / 格式非法 -> 一律视为全天（返回 True）
      - 普通区间 09:00-18:00：分钟内且在区间内才算
      - 跨零点区间 22:00-02:00：分钟内跨过 24:00 也判为在区间内

    不处理秒：配置粒度是「分钟」，立刻 >= 开始的分钟 且 立刻 < 结束的分钟，
    结束边界用开区间（[start, end)）：18:00 结束意味着 18:00 整不算。
    """
    start = (event.active_start or "").strip()
    end = (event.active_end or "").strip()
    if not start or not end:
        return True
    try:
        sh, sm = (int(p) for p in start.split(":"))
        eh, em = (int(p) for p in end.split(":"))
    except (ValueError, AttributeError):
        return True
    if not (0 <= sh <= 23 and 0 <= sm <= 59 and 0 <= eh <= 23 and 0 <= em <= 59):
        return True
    current = time.time() if now is None else now
    # 必须用 datetime（不是 date）：date 只有年月日，没有 hour/minute。
    locally = datetime.fromtimestamp(current)
    current_min = locally.hour * 60 + locally.minute
    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    if start_min < end_min:
        return start_min <= current_min < end_min
    # 跨零点：22:00-02:00，即 [22:00, 24:00) ∪ [00:00, 02:00)
    return current_min >= start_min or current_min < end_min

def collect_due_events(events: list[Event], now: Optional[float] = None) -> list[Event]:
    """返回本次应当提醒的事件列表。

    判定条件（全部满足）：
      - mode == COUNTDOWN（正计时不会到点，永不提醒）
      - enabled（停用的事件不打扰用户）
      - fired_at is None（还没提醒过，防重复）
      - 已到点（is_expired）

    注意「已达次数上限」的循环事件不再返回：它停在已到点状态，
    但界面靠 at_repeat_limit 显示「已完成」，不应每秒再弹一次提醒。

    :param events: 事件列表
    :param now: 当前时间戳（秒），默认取 time.time()
    """
    current = time.time() if now is None else now
    # 读 repeat_count（上限判定）前先自检：让计数属于「今天」。幂等设计
    # （ensure_daily_fresh 同天第二次返回 False）使这个自检与
    # mark_expired_as_fired / advance_repeating 的先后无关。正因为幂等，
    # 先被调用的入口会先拿到「今天」状态，漏调才是有害的（次数用满的事件
    # 当天不提醒，测试会抓住）。落盘契约由调用方统一保证（changed or due）。
    due = []
    for event in events:
        ensure_daily_fresh(event, current)
        if (
            event.mode == EventMode.COUNTDOWN
            and event.enabled
            and event.fired_at is None
            and is_expired(event, current)
            # 提醒时间段外的事件**跳过**：不返回、不标记、不推进。
            # 进入时间段后若已过期，会按正常提醒逻辑补一次（anchor 只推进一次）。
            and is_within_active_hours(event, current)
            # 次数用尽的循环事件留在列表里（界面显示「已完成」），但不再参与提醒，
            # 否则它会永远停在「已到点」，每秒被返回一次、每秒弹一次气泡。
            # 自检后日期 == 今天，上限判定退化为 at_repeat_limit。
            and not (_reaching_limit_today(event, current))
        ):
            due.append(event)
    return due


def mark_expired_as_fired(events: list[Event], now: Optional[float] = None) -> bool:
    """把所有「已到点但 fired_at is None」的倒计时直接标记为已提醒。

    【用途】程序启动时调用一次：
    用户关机期间到点的事件，重启后不应该补弹一堆过期提醒，
    直接标记掉即可实现「重启不重弹」。

    注意：这里**不检查 enabled** —— 停用的过期事件同样标记，
    否则用户之后重新启用它时会突然弹出一个陈旧的提醒。

    循环事件（repeat_seconds 为正）额外做「启动补齐」：
      把 anchor_ts 推进到 now + repeat_seconds（跳过错过的所有周期），
      但 **不累计 repeat_count**（启动期间没真的提醒，不算一次提醒），
      并把 fired_at 置为当前时间，确保下次 collect_due_events 不会立刻重复弹出。

    :param events: 事件列表（原地修改）
    :param now: 当前时间戳（秒），默认取 time.time()
    :return: 是否有事件被改动（True 表示调用方应当保存）
    """
    changed = False
    current = time.time() if now is None else now
    today = date.fromtimestamp(current).isoformat()
    for event in events:
        # 第一步：跨天归零（只对循环事件），必须在 at_repeat_limit 判断之前。
        # main.py 的启动扫描先于周期性检查执行；若先判「已达上限」，昨天的完成
        # 状态会把今天的第一次提醒标记掉（fired_at 置上）。先归零再判断，
        # 启动路径与运行路径各自保序，不依赖调用方记得先调 reset_daily_counters。
        fresh_today = False
        # 必须在归零**之前**取「昨天的完成状态」：ensure_daily_fresh 一执行，
        # count 就变成 0，at_repeat_limit 永远返回 False，这个标志会恒为假。
        # （这就是它一度看似有效、实际从未生效的原因。）
        was_at_limit = at_repeat_limit(event)
        if ensure_daily_fresh(event, current):
            changed = True
            fresh_today = was_at_limit
        if (
            event.mode == EventMode.COUNTDOWN
            and event.fired_at is None
            and is_expired(event, current)
            and is_within_active_hours(event, current)
        ):
            # fresh_today：跨天清零后的「今天新周期」，不补弹也不标记，保持
            # fired_at=None，让 collect_due_events 正常把它作为今天的第一次提醒。
            if not fresh_today:
                # 已达次数上限的事件不再推进（否则重启会把它「复活」一次）
                if (
                    event.repeat_seconds is not None
                    and event.repeat_seconds > 0
                    and not at_repeat_limit(event)
                ):
                    event.anchor_ts = repeated_next_anchor(event.anchor_ts, event.repeat_seconds, current)
                    # 循环事件推进后不能标记 fired_at：它表示「这一轮已提醒」，
                    # 而推进出的新周期尚未提醒。标记上就等于把「已提醒」写成了
                    # 「永久完结」，collect_due_events 从此不再返回 —— 现象是
                    # 循环事件重启后永久静默。与 check_notifications 同一语义。
                    changed = True
                else:
                    # 一次性事件 / 已达上限：没有下一轮，标记为「完结」防重启补弹。
                    event.fired_at = current
                    changed = True
    return changed


def due_message(event: Event) -> str:
    """到点通知的正文文案（集中在这里，便于统一改动与测试）。"""
    return f"{event.name} 已到点"



def restart_counting(event: Event, now: Optional[float] = None) -> Optional[Event]:
    """手动「重新开始」一个循环事件。

    只对有效的循环事件（repeat_seconds 为正）生效；一次性事件返回 None。
    返回**新的** Event（绝不原地改动传入对象，与 snooze 保持一致）：

        repeat_count = 0
        repeat_count_date = 今天
        fired_at = None          # 否则 collect_due_events 以 fired_at is None 为判据不再提醒
        anchor_ts = now + repeat_seconds   # 从此刻起重新倒计一个周期

    :param event: 循环事件
    :param now: 当前时间戳（秒），默认取 time.time()；测试可注入
    :return: 新的 Event；一次性 / 非倒计时返回 None
    """
    if not _is_repeating(event):
        return None
    current = time.time() if now is None else now
    return replace(
        event,
        repeat_count=0,
        repeat_count_date=date.fromtimestamp(current).isoformat(),
        fired_at=None,
        anchor_ts=current + event.repeat_seconds,
    )

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
