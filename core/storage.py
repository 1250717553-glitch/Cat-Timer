"""JSON 持久化层。

存储位置：%AppData%/桌面宠物计时器/config.json
写入方式：原子写（先写 .tmp，再 os.replace 替换），防止断电/崩溃写坏配置。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from core.models import Event

# 配置目录名与文件名
APP_DIR_NAME = "桌面宠物计时器"
CONFIG_FILENAME = "config.json"

# 配置结构版本号，便于以后做迁移
SCHEMA_VERSION = 1


def get_config_path() -> Path:
    """返回配置文件完整路径，并确保父目录存在。

    优先使用环境变量 APPDATA（Windows 标准做法），不硬编码绝对路径。
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata)
    else:
        # 兜底：非 Windows 或 APPDATA 缺失时退回家目录，保证模块可被测试
        base = Path.home() / "AppData" / "Roaming"

    config_dir = base / APP_DIR_NAME
    config_dir.mkdir(parents=True, exist_ok=True)  # 目录不存在时自动创建
    return config_dir / CONFIG_FILENAME


def load_events() -> list[Event]:
    """读取事件列表。

    - 文件不存在 -> 返回空列表（首次运行属正常情况，不报错）
    - JSON 解析失败 / 结构异常 -> 打印警告并返回空列表（不崩溃）
    """
    path = get_config_path()

    if not path.exists():
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"[storage] 警告：配置文件解析失败，已忽略。{path} -> {exc}")
        return []
    except OSError as exc:
        print(f"[storage] 警告：配置文件读取失败，已忽略。{path} -> {exc}")
        return []

    if not isinstance(data, dict):
        print(f"[storage] 警告：配置文件顶层结构异常，已忽略。{path}")
        return []

    raw_events = data.get("events")
    if not isinstance(raw_events, list):
        return []

    events: list[Event] = []
    for item in raw_events:
        if not isinstance(item, dict):
            continue
        try:
            events.append(Event.from_dict(item))
        except (TypeError, ValueError) as exc:
            print(f"[storage] 警告：跳过一条无法解析的事件记录 -> {exc}")
    return events


def save_events(events: list[Event]) -> None:
    """原子写入事件列表。

    步骤：写 config.json.tmp -> flush + fsync -> os.replace 替换正式文件。
    os.replace 在同一文件系统内是原子操作，因此正式文件要么是旧的、要么是新的，
    不会出现「写了一半」的损坏状态。
    """
    path = get_config_path()
    tmp_path = path.with_name(path.name + ".tmp")

    payload = {
        "schema_version": SCHEMA_VERSION,
        "events": [event.to_dict() for event in events],
    }

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())  # 确保数据真正落盘后再替换

    os.replace(tmp_path, path)
