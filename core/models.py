"""数据模型层。

本模块只做「数据的形状」定义与序列化，不含任何计时逻辑与界面逻辑。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class EventMode(str, Enum):
    """事件模式：倒计时 / 正计时。

    继承 str 是为了让枚举值能直接被 json 序列化，也便于与字符串比较。
    """

    COUNTDOWN = "countdown"  # 倒计时：从当前时间倒数到目标时间
    COUNT_UP = "count_up"    # 正计时：从起始时间正数到现在


class DisplayFormat(str, Enum):
    """显示格式，共 8 种。

    前 4 种为「分段式」：叠加显示多个单位，如 "12天 05小时 30分钟 20秒"。
    后 4 种为「单单位式」：只显示一个单位的总量，向下取整，如 "17610分钟"。
    """

    # --- 分段式 ---
    DAYS = "days"                                # 仅天
    DAYS_HOURS = "days_hours"                    # 天 + 小时
    DAYS_HOURS_MINUTES = "days_hours_minutes"    # 天 + 小时 + 分钟
    FULL = "full"                                # 天 + 小时 + 分钟 + 秒

    # --- 单单位式（向下取整）---
    TOTAL_SECONDS = "total_seconds"              # 总计秒
    TOTAL_MINUTES = "total_minutes"              # 总计分钟
    TOTAL_HOURS = "total_hours"                  # 总计小时
    TOTAL_DAYS = "total_days"                    # 总计天数

    # --- 智能省略（第 9 种）---
    # 为什么要单独做一种格式，而不是把上面某个格式改聪明：
    #   1) 已经选了「天+小时+分钟」的用户期待格式固定，不该被悄悄改掉；
    #   2) 省略会让宽度每秒变（59 分钟 -> 1 小时 多两个字），
    #      只有用户显式选择才表示他接受这个代价。
    SMART = "smart"                              # 去掉值为 0 的高位单位


# 支持的日期 / 时间输入写法，便于 from_datetime_string 宽容解析
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d")
_TIME_FORMATS = ("%H:%M", "%H:%M:%S")

# 字符串形式的「假值」，用于 _parse_bool 判定
# 采用「黑名单」策略：不在此清单内的字符串一律视为真值。
_FALSY_STRINGS = ("false", "0", "no", "off", "n", "")


@dataclass
class Event:
    """一个计时事件。

    anchor_ts 是「锚点时间戳」（秒）：
      - 倒计时模式下 = 目标时间（倒数的终点）
      - 正计时模式下 = 起始时间（正数的起点）

    所有时间都以时间戳保存，因此程序重启、电脑休眠唤醒后结果依然准确。
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))  # uuid4 字符串，创建时自动生成
    name: str = ""                                              # 事件名称
    mode: EventMode = EventMode.COUNTDOWN                       # 事件模式
    anchor_ts: float = 0.0                                      # 锚点时间戳（秒）
    display_format: DisplayFormat = DisplayFormat.DAYS_HOURS_MINUTES  # 显示格式
    enabled: bool = True                                        # 是否启用
    show_on_desktop: bool = True                                # 是否显示在桌面
    fired_at: Optional[float] = None                            # 已提醒时间戳；None 表示未提醒（仅倒计时有意义）
    repeat_seconds: Optional[int] = None                        # 重复提醒周期（秒）；None = 一次性事件
    repeat_limit: Optional[int] = None                          # 循环次数上限；None = 无限循环
    repeat_count: int = 0                                       # 已真实提醒次数（仅循环事件累计，启动补齐不计）
    repeat_count_date: str = ""                                      # 计数所属日期 ISO "2026-10-29"（用于跨天自动重置）
    active_start: str = ""                                           # 提醒时间段起点 "HH:MM"，空 = 全天
    active_end: str = ""                                             # 提醒时间段终点 "HH:MM"，空 = 全天

    # ---------- 序列化 ----------

    def to_dict(self) -> dict[str, Any]:
        """转成可 JSON 序列化的普通字典（枚举存字符串）。"""
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode.value,
            "anchor_ts": self.anchor_ts,
            "display_format": self.display_format.value,
            "enabled": self.enabled,
            "show_on_desktop": self.show_on_desktop,
            "fired_at": self.fired_at,
            "repeat_seconds": self.repeat_seconds,
            "repeat_limit": self.repeat_limit,
            "repeat_count": self.repeat_count,
            "repeat_count_date": self.repeat_count_date,
            "active_start": self.active_start,
            "active_end": self.active_end,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        """从字典还原 Event。

        对缺失字段、非法枚举值、非法数字、字符串布尔值都做了兜底，
        保证「手工改坏 JSON」也不会抛异常。
        """
        mode = _parse_enum(EventMode, data.get("mode"), EventMode.COUNTDOWN)
        display_format = _parse_enum(
            DisplayFormat, data.get("display_format"), DisplayFormat.DAYS_HOURS_MINUTES
        )

        # fired_at 缺失 / 为 null -> None；是数字或数字字符串 -> float；其余（如 "abc"）-> None
        fired_at = _parse_float(data.get("fired_at"), None)

        # 循环参数的下界校验（缺失/非法已在 _parse_optional_int 里兜底为默认值）。
        #
        # repeat_seconds <= 0 必须归 None（= 一次性）：它不是「间隔很短」，而是非法值。
        # 若放行，mark_expired_as_fired 会把 anchor 推进到 now + 负数 = 过去，
        # 于是每次启动扫描都再往前推一次，事件永久卡在「已到点」且逐次加深损坏。
        repeat_seconds = _parse_optional_int(data.get("repeat_seconds"), None)
        if repeat_seconds is not None and repeat_seconds <= 0:
            repeat_seconds = None

        # 循环次数上限：与 repeat_seconds 同样的兜底策略 ——
        # 0 / 负数 / 非法字符串一律当 None（无限循环），不当作"一次都不提醒"。
        # 若放行 0，事件会在第一次到点时就永远停住，且用户无从得知原因。
        repeat_limit = _parse_optional_int(data.get("repeat_limit"), None)
        if repeat_limit is not None and repeat_limit <= 0:
            repeat_limit = None

        # repeat_count 是**要显示在界面上**的统计值，负数会直接变成「已 -5 次」怼给用户。
        # 正常流程产生不出负数，出现只可能是手工改坏 JSON 或某个 bug ——
        # 两种都不该显示出来。这里兜底为 0，但把异常写进控制台日志，
        # 做到「界面干净、异常可追溯」。
        repeat_count = _parse_optional_int(data.get("repeat_count"), 0)
        if repeat_count is None or repeat_count < 0:
            _warn_bad_repeat_count(data.get("name"), repeat_count)
            repeat_count = 0

        # repeat_count_date / active_start / active_end 都是旧数据没有的字段，
        # 缺失 -> 默认值（""）。
        # 设计决定：重复事件的次数计数带「所属日期」。旧 JSON 读出 "" 表示该计数
        # 没有挂靠日期，reset_daily_counters 首次调用时会把 count 归零并记为今天 ——
        # 老用户升级后第一次启动，所有循环事件 count 归零一次是**可接受的**：
        # 当天本来就该重新开始。不要把它当 bug 去「修」。
        repeat_count_date = str(data.get("repeat_count_date") or "")
        active_start = str(data.get("active_start") or "")
        active_end = str(data.get("active_end") or "")

        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            name=str(data.get("name") or ""),
            mode=mode,
            anchor_ts=_parse_float(data.get("anchor_ts"), 0.0),
            display_format=display_format,
            enabled=_parse_bool(data.get("enabled"), True),
            show_on_desktop=_parse_bool(data.get("show_on_desktop"), True),
            fired_at=fired_at,
            repeat_seconds=repeat_seconds,
            repeat_limit=repeat_limit,
            repeat_count=repeat_count,
            repeat_count_date=repeat_count_date,
            active_start=active_start,
            active_end=active_end,
        )

    # ---------- 便捷构造 ----------

    @classmethod
    def from_datetime_string(
        cls,
        date_str: str,
        time_str: str,
        name: str = "",
        mode: EventMode = EventMode.COUNTDOWN,
    ) -> "Event":
        """把 "2026-10-20" + "15:30" 转成带 anchor_ts 的 Event。

        按本机本地时区解释日期时间（naive datetime 的 timestamp() 即本地时区）。
        """
        dt = _parse_datetime(date_str, time_str)
        return cls(name=name, mode=mode, anchor_ts=dt.timestamp())


# ---------- 内部工具 ----------


def _parse_enum(enum_cls: type[Enum], value: Any, default: Any) -> Any:
    """宽容地把字符串还原为枚举；失败时返回默认值。"""
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except (ValueError, TypeError):
        return default


def _parse_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    """宽容地把值转成 float。

    能正确处理数字、数字字符串（含前后空白，如 "123.5"、" 123.5 "）；
    对 None、""、"abc"、列表等无法转换的值返回 default，绝不抛异常。

    :param value: 待转换的值
    :param default: 转换失败时的返回值（fired_at 场景传 None）
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _warn_bad_repeat_count(name: Any, value: Optional[int]) -> None:
    """为「非法 repeat_count」打印一行控制台警告。

    为什么不抛异常、也不静默：
        这是持久化数据里的一处异常，用户界面不该看到它（会显示成「已 -5 次」），
        但彻底咽掉又会让问题永远查不出来。所以「界面兜底 + 日志留痕」。
    与 core/storage.py 的警告前缀保持一致的 [storage]，便于统一检索。
    """
    print(f"[storage] 警告：事件「{name or '(未命名)'}」的 repeat_count={value!r} 非法，已按 0 处理")


def _parse_optional_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    """宽容地把值转成 int，允许为 None（repeat_seconds / repeat_count 用）。

    数字或数字字符串（含小数与空白，如 "1800"、"1800.0"、" 30 "）-> int（截断小数）；
    None、""、"abc"、列表等无法转换的值 -> default。绝不抛异常。
    """
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parse_bool(value: Any, default: bool = True) -> bool:
    """宽容地把值转成 bool，避免 bool("false") == True 这类陷阱。

    规则：
      - None      -> default
      - 字符串     -> strip().lower() 后，命中 ("false", "0", "no", "") 判为 False，其余为 True
      - 其他类型   -> 直接 bool(value)

    :param value: 待转换的值
    :param default: value 为 None 时的返回值
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in _FALSY_STRINGS
    return bool(value)


def _parse_datetime(date_str: str, time_str: str) -> datetime:
    """解析日期与时间字符串，返回 datetime；支持少量常见写法。"""
    date_str = (date_str or "").strip()
    time_str = (time_str or "").strip()

    dt_date: Optional[datetime] = None
    for fmt in _DATE_FORMATS:
        try:
            dt_date = datetime.strptime(date_str, fmt)
            break
        except ValueError:
            continue
    if dt_date is None:
        raise ValueError(f"无法解析日期：{date_str!r}（期望形如 2026-10-20）")

    # 时间允许为空 -> 默认 00:00
    if not time_str:
        return dt_date

    for fmt in _TIME_FORMATS:
        try:
            t = datetime.strptime(time_str, fmt)
            return dt_date.replace(hour=t.hour, minute=t.minute, second=t.second)
        except ValueError:
            continue
    raise ValueError(f"无法解析时间：{time_str!r}（期望形如 15:30 或 15:30:00）")
