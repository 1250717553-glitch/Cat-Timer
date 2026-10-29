"""全屏 / 沉浸状态检测，以及三档可见性的判定策略。

纯 ctypes 实现，不 import 任何 GUI 库 —— 保证 platform_win 层可脱离 Qt 单元测试。

用 Windows 的 SHQueryUserNotificationState 查询系统当前的"用户通知状态"。
Windows 7+ 系统会在全屏（Direct3D 独占、演示模式、屏保等）时把该状态切走，
我们可以据此判断"用户正在全屏看视频 / 打游戏"而自动收起宠物窗口。

另外提供 foreground_covers_screen()：判断前台窗口是否铺满屏幕，
供「仅桌面可见」档使用（系统不报全屏、但用户把别的窗口最大化了也算挡住桌面）。
"""

from __future__ import annotations

import ctypes
import os
from typing import Optional

# QUNS（查询用户通知状态）枚举值，见 winuser.h 的 QUERY_USER_NOTIFICATION_STATE。
# 官方只有 1..7 这七个值，**没有 0，也没有 SCREENSAVER**。
# 数值必须与文档一致 —— 错一个，将来想「区分免打扰和全屏」时就会判错。
QUNS_NOT_PRESENT = 1
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6
QUNS_APP = 7

# 视为"用户在沉浸 / 占满屏幕"、需要收起宠物窗口的状态。
# 除 5（ACCEPTS_NOTIFICATIONS，正常可打扰）之外全部收起。
_HIDE_STATES = frozenset(
    {
        QUNS_NOT_PRESENT,
        QUNS_BUSY,
        QUNS_RUNNING_D3D_FULL_SCREEN,
        QUNS_PRESENTATION_MODE,
        QUNS_QUIET_TIME,
        QUNS_APP,
    }
)

# 三档可见性（与 core.preferences 的取值一致，见 VISIBILITY_MODES）。
# 这里不 import core.preferences 是为了让 platform_win 保持「零项目内依赖」，
# 值本身很短、且有测试逐条对照，漂移会立刻被发现。
VISIBILITY_DESKTOP = "desktop"
VISIBILITY_WINDOW = "window"
VISIBILITY_ALWAYS = "always"

# 判定"前台窗口覆盖了整块屏幕"时允许的误差（像素）。
# 有些窗口带隐藏边框 / 阴影，GetWindowRect 会比显示器大或小几个像素。
COVER_TOLERANCE_PX = 4

# 显示器句柄的兜底标志：窗口所在显示器取不到时退回最近的显示器
MONITOR_DEFAULTTONEAREST = 2


def _load_shell32() -> Optional[ctypes.CDLL]:
    """加载 shell32.dll；失败（非 Windows）时返回 None。"""
    try:
        return ctypes.windll.shell32
    except (AttributeError, OSError):
        return None


def query_state() -> Optional[int]:
    """查询系统当前的用户通知状态。

    :return: QUNS_* 枚举值；无法查询（非 Windows / 函数缺失 / 调用失败）时返回 None。
    """
    shell32 = _load_shell32()
    if shell32 is None:
        return None

    value = ctypes.c_int(0)
    try:
        # SHQueryUserNotificationState(LPQUNS_STATE) -> HRESULT
        # 返回 0 (S_OK) 表示成功，value 才会被写入有效状态。
        result = shell32.SHQueryUserNotificationState(ctypes.byref(value))
    except OSError:
        return None

    if result != 0:
        return None
    return int(value.value)


def should_hide(state: Optional[int]) -> bool:
    """根据查询到的状态决定是否应隐藏宠物窗口。

    :param state: query_state() 的返回值，None 表示查询失败。
    :return: True 表示应隐藏；None（查询失败）按"宁可露出"处理，返回 False，
             避免误判导致宠物窗口消失。
    """
    if state is None:
        return False
    return state in _HIDE_STATES


# ---------------------------------------------------------------------------
# 前台窗口是否占满屏幕（「仅桌面可见」档用）
# ---------------------------------------------------------------------------


class _RECT(ctypes.Structure):
    """Win32 RECT。"""

    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFO(ctypes.Structure):
    """Win32 MONITORINFO（cbSize 必须先填好，否则 GetMonitorInfoW 直接失败）。"""

    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def _load_user32() -> Optional[ctypes.CDLL]:
    """加载 user32.dll；失败（非 Windows）时返回 None。"""
    try:
        return ctypes.windll.user32
    except (AttributeError, OSError):
        return None


def _foreground_hwnd() -> Optional[int]:
    """取当前前台窗口句柄；拿不到返回 None。

    抽成独立函数是为了可测：单测可以替换它，无需真实窗口系统。
    """
    user32 = _load_user32()
    if user32 is None:
        return None
    try:
        hwnd = user32.GetForegroundWindow()
    except (OSError, AttributeError):
        return None
    return int(hwnd) if hwnd else None


def _window_pid(hwnd: int) -> Optional[int]:
    """查某个窗口属于哪个进程；查不到返回 None。

    同样抽出来为了可测（见 _foreground_hwnd 的说明）。
    """
    user32 = _load_user32()
    if user32 is None:
        return None
    try:
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    except (OSError, AttributeError):
        return None
    return int(pid.value) or None


# 桌面窗口 / 任务栏的窗口类名。它们天然占满整块屏幕，
# 但用户点击桌面 / 任务栏时系统把它们设为前台，绝不能据此判定「被遮挡」。
# 判定覆盖前先排除这几种窗口，否则隐藏任务栏后点一下桌面，猫就消失。
_DESKTOP_CLASSES = frozenset({"Progman", "WorkerW", "Shell_TrayWnd"})


def _window_class(hwnd: int) -> Optional[str]:
    """查指定窗口的类名（GetClassNameW）。查不到返回 None。

    单独抽出来是为了可测：单测直接替换它，无需真实窗口系统。
    """
    user32 = _load_user32()
    if user32 is None:
        return None
    try:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(int(hwnd), buf, 256)
    except (OSError, AttributeError):
        return None
    return buf.value if buf.value else None


def foreground_covers_screen(tolerance: int = COVER_TOLERANCE_PX) -> bool:
    """前台窗口是否铺满了它所在的那块显示器。

    用途：「仅桌面可见」档下，即使系统没报全屏（比如用户只是把一个普通窗口
    最大化），也把宠物收起来 —— 否则宠物会浮在别人的窗口上面。

    **必须排除本进程自己的窗口**：用户最大化管理窗口时，猫不该被藏起来，
    那看起来像程序自己把宠物弄丢了。

    任何一步失败都返回 False（宁可露出，不误藏）。

    :param tolerance: 判定"覆盖"时允许的像素误差（隐藏边框 / 阴影）
    """
    user32 = _load_user32()
    if user32 is None:
        return False

    try:
        hwnd = _foreground_hwnd()
        if hwnd is None:
            return False

        # 排除自己：前台是本进程的窗口 -> 不算「被别人挡住」。
        # 漏了这条，用户最大化管理窗口时猫也会被藏，看起来像程序坏了。
        if _window_pid(hwnd) == os.getpid():
            return False

        # 排除桌面 / 任务栏窗口：Progman / WorkerW / Shell_TrayWnd 天然占满全屏，
        # 但点击桌面或任务栏并不表示「有别的东西盖住桌面」，绝不能因此隐藏宠物。
        # 隐藏任务栏后点桌面时，前台正是这些窗口 —— 这一条就是那个 bug 的修复。
        # 类名查不到（None）不在此集合内，继续后续判定（宁可露出，不误藏）。
        if _window_class(hwnd) in _DESKTOP_CLASSES:
            return False

        # 最小化的窗口 rect 是 (-32000, -32000)，不判可见性会被误判成"覆盖"
        if not user32.IsWindowVisible(hwnd):
            return False

        rect = _RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False

        monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return False

        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False

        screen = info.rcMonitor
        return (
            rect.left <= screen.left + tolerance
            and rect.top <= screen.top + tolerance
            and rect.right >= screen.right - tolerance
            and rect.bottom >= screen.bottom - tolerance
        )
    except (OSError, AttributeError, ValueError):
        return False


def should_hide_for_mode(
    state: Optional[int],
    mode: str,
    foreground_covers: bool = False,
) -> bool:
    """按可见性档位决定是否隐藏宠物窗口（纯函数，便于单测）。

    三档语义：
        desktop —— 仅桌面可见：系统不在「可打扰」状态 **或** 前台窗口铺满屏幕，就隐藏
        window  —— 窗口可见（默认）：只在系统进入全屏 / 免打扰时隐藏
        always  —— 始终可见：永不隐藏

    未知档位按 window 处理（兜底，不因为配置写错就把宠物藏起来）。

    :param state: query_state() 的返回值（None = 查询失败）
    :param mode: VISIBILITY_* 之一
    :param foreground_covers: foreground_covers_screen() 的返回值
    """
    if mode == VISIBILITY_ALWAYS:
        return False
    if mode == VISIBILITY_DESKTOP:
        # 桌面档更严格：除了系统状态，还要看有没有别人的窗口盖住桌面
        return should_hide(state) or foreground_covers
    return should_hide(state)
