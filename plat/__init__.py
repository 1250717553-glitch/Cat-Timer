"""平台分派层。

所有平台专有实现放在 `plat/win/` 与 `plat/mac/`，本模块按 `sys.platform`
选出当前平台那一套，并以**同名函数**对外暴露。

设计约束（很重要）：
    调用方永远是 `from plat import autostart`，而不是 `from plat.win import ...`。
    这样 ui/ 与 main.py 完全不知道自己在哪个平台，加一个平台只需加一个子包。

为什么包名是 `plat` 而不是 `platform`：
    `platform` 是 Python 标准库模块名。同名包会遮蔽标准库，
    任何第三方库 `import platform` 都会拿到我们的包 —— 静默且难查的灾难。
"""

from __future__ import annotations

import sys

# 当前平台：'win' / 'mac' / 'other'
# 用 sys.platform 而不是 os.name：前者能区分 darwin 与 cygwin。
if sys.platform == "win32":
    PLATFORM = "win"
elif sys.platform == "darwin":
    PLATFORM = "mac"
else:
    # Linux 等未适配平台：退回 Windows 实现。它的每个函数都有
    # 「非本平台就降级」的守卫，不会崩，只是功能缺失。
    PLATFORM = "other"

__all__ = ["PLATFORM", "autostart", "fullscreen", "single_instance", "sound"]


if PLATFORM == "win":
    from plat.win import autostart, fullscreen, single_instance, sound
elif PLATFORM == "mac":
    from plat.mac import autostart, fullscreen, single_instance, sound
else:
    from plat.win import autostart, fullscreen, single_instance, sound
