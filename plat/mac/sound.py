"""到点提示音（macOS 实现）。

macOS 没有 winsound。两种可选方案：
    afplay     —— 系统自带命令行播放器，能播 wav/mp3/aiff，最简单可靠
    NSSound    —— pyobjc 原生接口，但需要额外依赖

选 afplay：零依赖、系统自带、异步播放（Popen 不等待）。
失败一律静默 —— 播不出声不该影响提醒本身。
"""

from __future__ import annotations

import subprocess
import sys

from core.paths import first_existing

# 自定义音效候选（相对资源基准目录）。有就用，没有就系统音。
_SOUND_CANDIDATES: tuple[str, ...] = (
    "assets/sounds/meow.wav",
    "assets/sounds/meow.aiff",
)

# 系统提示音。Glass 是 macOS 的「信息提示」音，柔和，适合倒计时到点。
# 完整列表在 /System/Library/Sounds/ 下（Glass / Ping / Pop / Tink 等）。
_FALLBACK_SOUND = "Glass"


def play_notification() -> bool:
    """播一次到点提示音。

    :return: 是否成功发起播放（失败/非 macOS 一律 False，绝不抛异常）
    """
    if sys.platform != "darwin":
        return False

    try:
        path = first_existing(_SOUND_CANDIDATES)
        if path is not None:
            target = str(path)
        else:
            target = "/System/Library/Sounds/%s.aiff" % _FALLBACK_SOUND

        # Popen 而不是 run：不阻塞主线程，到点提醒要立刻返回。
        # stdout/stderr 全部丢弃 —— afplay 出错不需要让用户看到。
        subprocess.Popen(
            ["/usr/bin/afplay", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        # afplay 缺失、文件损坏、设备被独占 —— 一律静默
        return False
