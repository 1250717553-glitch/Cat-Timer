"""偏好设置持久化。

与事件数据（config.json）分开存 —— 事件文件带 schema_version 用于迁移，
偏好只是开关集合，结构简单且变化频繁，混在一起会污染事件结构。

存储位置：%AppData%/桌面宠物计时器/preferences.json
写入方式：与 core/storage.py 一致的原子写（.tmp + flush + fsync + os.replace）。
任何读取 / 写入失败都不抛异常，回退到默认值 —— 偏好丢了不该让程序崩溃。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from core.paths import backup_corrupt_file

APP_DIR_NAME = "桌面宠物计时器"
PREFERENCES_FILENAME = "preferences.json"

# 可见性三档（取值同时被 platform_win.fullscreen 使用，两边必须一致）
VISIBILITY_DESKTOP = "desktop"  # 仅桌面可见：别的窗口盖住桌面就收起
VISIBILITY_WINDOW = "window"    # 窗口可见（默认）：只在全屏 / 免打扰时收起
VISIBILITY_ALWAYS = "always"    # 始终可见：永不自动收起
VISIBILITY_MODES: tuple[str, ...] = (
    VISIBILITY_DESKTOP,
    VISIBILITY_WINDOW,
    VISIBILITY_ALWAYS,
)

# 语义已废弃的旧键：曾经是「全屏时自动隐藏」布尔开关，
# 与「显示范围」三档语义重叠（始终可见 == 不自动隐藏），用户看不出哪个优先。
# 现在只保留三档，此键仅在读取时用于迁移（见 load_visibility_mode）。
LEGACY_FULLSCREEN_AUTO_HIDE_KEY = "fullscreen_auto_hide"

# 默认偏好（也是读失败 / 文件损坏时的回退值）。
DEFAULT_PREFERENCES: dict[str, Any] = {
    # 默认「窗口可见」——与三档功能上线前的行为完全一致，老用户升级后无感
    "visibility_mode": VISIBILITY_WINDOW,
    # 默认开启：到点提醒本来就该有声音，静默提醒容易漏看
    "sound_enabled": True,
}


def get_preferences_path() -> Path:
    """返回偏好文件完整路径，并确保父目录存在（与 core.storage 同一目录规则）。"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        base = Path(appdata)
    else:
        base = Path.home() / "AppData" / "Roaming"
    dir_ = base / APP_DIR_NAME
    dir_.mkdir(parents=True, exist_ok=True)
    return dir_ / PREFERENCES_FILENAME


def _defaults() -> dict[str, Any]:
    return dict(DEFAULT_PREFERENCES)


def load_preferences() -> dict[str, Any]:
    """读取偏好；文件缺失 / 损坏 / 结构异常时返回默认值，绝不抛异常。"""
    path = get_preferences_path()
    if not path.exists():
        return _defaults()

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # 解析失败才隔离：这份文件已经是坏的，留着只会每次启动都报错一次，
        # 而且后续 save 会覆盖它。隔离之后程序能正常写入新配置，自愈。
        print(f"[preferences] 警告：偏好读取失败，使用默认值。{path} -> {exc}")
        backup_corrupt_file(path)
        return _defaults()
    except OSError as exc:
        # 只是打不开（权限/占用），文件内容可能完好 —— 不能 rename 搬走。
        print(f"[preferences] 警告：偏好读取失败，使用默认值。{path} -> {exc}")
        return _defaults()

    if not isinstance(data, dict):
        print(f"[preferences] 警告：偏好结构异常，使用默认值。{path}")
        backup_corrupt_file(path)
        return _defaults()

    # 取已知键，未知键忽略（向前兼容）。
    # 例外：旧键 LEGACY_FULLSCREEN_AUTO_HIDE_KEY 必须保留到 result 里，
    # 否则 load_visibility_mode 的迁移分支永远看不到它，老用户的设置会丢。
    result = _defaults()
    for key in (*DEFAULT_PREFERENCES, LEGACY_FULLSCREEN_AUTO_HIDE_KEY):
        if key in data:
            result[key] = data[key]
    return result


def _read_raw() -> dict[str, Any]:
    """读原始 JSON（不填默认值），供迁移时判断「某键到底有没有出现过」。"""
    path = get_preferences_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def load_visibility_mode() -> str:
    """读取可见性档位（默认 "window"）。

    非法值 / 缺失 / 类型不对一律回落到默认档 —— 配置写坏了不该让宠物消失。

    迁移：老版本的 preferences.json 只有 fullscreen_auto_hide（布尔），没有
    visibility_mode。此时按「关了自动隐藏 == 始终可见」映射，其余 == 窗口可见，
    保证升级后行为不变，又不让旧的假值把宠物永久藏起来。
    """
    prefs = load_preferences()
    # 关键：必须判断「文件里真的写了吗」，不能用 prefs.get() 的返回值 ——
    # load_preferences 会把默认值填进来，于是「缺失」和「显式写 window」
    # 在 result 里长得一模一样，迁移分支就永远走不到（旧键被默认值盖住）。
    raw = _read_raw()
    value = raw.get("visibility_mode")
    if isinstance(value, str) and value in VISIBILITY_MODES:
        return value

    legacy = raw.get(LEGACY_FULLSCREEN_AUTO_HIDE_KEY)
    if isinstance(legacy, bool) and not legacy:
        return VISIBILITY_ALWAYS
    return VISIBILITY_WINDOW




def load_sound_enabled() -> bool:
    """读取「到点提示音」开关（默认开启）。

    非布尔值（手工改坏 JSON）一律回落 True：宁可响一声，
    也不要因为一个坏字段把提醒变成静默的。
    """
    value = load_preferences().get("sound_enabled")
    if not isinstance(value, bool):
        return True
    return value


def save_preferences(prefs: dict[str, Any]) -> None:
    """原子写偏好，失败时打印警告而不抛异常。

    与 storage 一样先写 .tmp 再 os.replace，保证不会出现坏文件。
    """
    path = get_preferences_path()
    tmp_path = path.with_name(path.name + ".tmp")

    # 只把已知键落盘。旧键（LEGACY_FULLSCREEN_AUTO_HIDE_KEY）**不再写入** ——
    # 迁移逻辑只在读时生效，写出去只会让下个版本继续看到过期字段。
    payload = _defaults()
    if isinstance(prefs, dict):
        for key in DEFAULT_PREFERENCES:
            if key in prefs:
                payload[key] = prefs[key]

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        print(f"[preferences] 警告：偏好写入失败，已忽略。{path} -> {exc}")
