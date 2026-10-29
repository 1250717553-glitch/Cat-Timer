"""到点提示音。

放在 platform_win 而不是 core：winsound 是 Windows 专有模块，
core 层必须保持「可脱离 GUI/平台依赖单独测试」的性质。

优先播 assets/sounds/meow.wav，缺失时回落系统提示音。
任何失败都静默忽略 —— 播不出声不该影响提醒本身，
更不该让一个音频问题把整个程序搞崩。
"""

from __future__ import annotations

import sys

from core.paths import first_existing

# 自定义音效候选（相对资源基准目录）。有就用，没有就系统音。
_SOUND_CANDIDATES: tuple[str, ...] = ("assets/sounds/meow.wav",)

# 系统提示音的别名。SystemAsterisk 是「信息提示」音，
# 比其他几个（Exclamation / Beep）柔和，适合倒计时到点这种非紧急提醒。
_FALLBACK_ALIAS = "SystemAsterisk"


def play_notification() -> bool:
    """播一次到点提示音。

    :return: 是否成功发起播放（失败/非 Windows 一律 False，绝不抛异常）

    为什么捕获 Exception 而不是只捕 ImportError/OSError：
        winsound 在文件损坏、格式不支持、音频设备被独占时会抛出各种异常，
        具体类型随 Windows 版本和驱动而变。这里唯一正确的策略是「什么都不让它冒出来」。
    """
    if sys.platform != "win32":
        return False
    try:
        import winsound
    except ImportError:
        return False

    try:
        path = first_existing(_SOUND_CANDIDATES)
        if path is not None:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            winsound.PlaySound(_FALLBACK_ALIAS, winsound.SND_ALIAS | winsound.SND_ASYNC)
        return True
    except Exception:
        return False