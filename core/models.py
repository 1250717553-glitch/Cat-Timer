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

        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            name=str(data.get("name") or ""),
            mode=mode,
            anchor_ts=_parse_float(data.get("anchor_ts"), 0.0),
            display_format=display_format,
            enabled=_parse_bool(data.get("enabled"), True),
            show_on_desktop=_parse_bool(data.get("show_on_desktop"), True),
            fired_at=fired_at,
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
