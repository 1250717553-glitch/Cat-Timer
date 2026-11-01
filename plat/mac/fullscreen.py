"""全屏 / 沉浸状态检测（macOS 实现）。

与 Windows 版的差别：
    Windows 用 SHQueryUserNotificationState 一个 API 拿到「用户是否在沉浸」；
    macOS **没有等价 API**，必须自己判断：
      1. 前台 app 是否处于全屏（NSWindow styleMask 含 FullScreen 位）
      2. 屏幕是否被单个窗口完全覆盖（大窗口 + 无菜单栏）

对普通用户来说这两者都表现为「我全屏看视频/打游戏」，所以合并成一个判断。

降级策略：
    拿不到（没装 pyobjc、权限不足、调用异常）一律返回 None，
    should_hide(None) 返回 False —— **宁可露出，不可误藏**。
    宠物窗口莫名消失比它多露一会儿严重得多。
"""

from __future__ import annotations

from typing import Optional

# 三档可见性取值必须与 core.preferences.VISIBILITY_MODES 一致。
VISIBILITY_DESKTOP = "desktop"
VISIBILITY_WINDOW = "window"
VISIBILITY_ALWAYS = "always"


def _load_appkit():
    """加载 AppKit；没装 pyobjc 时返回 None。

    pyobjc 是 macOS 上访问原生 API 的必需品，但它是可选依赖 ——
    没有它程序照样能跑，只是丢「全屏自动隐藏」这一个功能。
    """
    try:
        import AppKit  # noqa: F401
    except ImportError:
        return None
    return AppKit


def query_state() -> Optional[int]:
    """查询当前是否处于「全屏 / 沉浸」。

    :return: 1 表示全屏沉浸，0 表示正常；无法判断时返回 None。
             用整数而不是布尔，是为了让 should_hide 的语义与 Windows 版对齐
             （Windows 那边返回的是 QUNS 枚举值）。
    """
    AppKit = _load_appkit()
    if AppKit is None:
        return None

    try:
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        app = workspace.frontmostApplication()
        if app is None:
            return None

        # 全屏的判定：前台 app 激活且其窗口 styleMask 含全屏位。
        # NSApplicationPresentationFullScreen 在 presentationOptions 里也有体现，
        # 这里取更稳的一路：检查所有窗口是否全屏。
        windows = app.windows()
        if not windows:
            return 0

        full_screen_mask = getattr(AppKit, "NSWindowStyleMaskFullScreen", 1 << 14)
        for w in windows:
            style = w.styleMask()
            if style & full_screen_mask:
                return 1
        return 0
    except Exception:
        # 任何意外都当「判断不了」，交给 should_hide 兜底为「不隐藏」
        return None


def should_hide(state: Optional[int]) -> bool:
    """根据 state 决定是否隐藏宠物窗口。

    :param state: query_state() 的返回值。
    :return: True 表示应隐藏。None / 0 一律返回 False（宁可露出）。
    """
    if state is None:
        return False
    return bool(state)


def foreground_covers_screen() -> bool:
    """前台窗口是否铺满屏幕（「仅桌面可见」档用）。

    macOS 上这条判断的成本高且误判风险大（需要遍历所有窗口 + 计算覆盖面积），
    这里采用简化策略：**只在明确全屏时返回 True**。
    「别的窗口盖住桌面」这种情况在 macOS 上由窗口系统本身处理
    （宠物窗口是 floating level，本来就会浮在其他普通窗口之上）。
    """
    return query_state() == 1


def should_hide_for_mode(mode: str, *, check_foreground: bool = True) -> bool:
    """按三档可见性给出当前是否应隐藏。接口与 Windows 版一致。

    :param mode: VISIBILITY_DESKTOP / VISIBILITY_WINDOW / VISIBILITY_ALWAYS
    :param check_foreground: 是否额外检查前台窗口，保留参数以对齐 Windows 签名
    """
    if mode == VISIBILITY_ALWAYS:
        return False

    if mode == VISIBILITY_DESKTOP:
        # 「仅桌面可见」：任何全屏/覆盖都收起
        return should_hide(query_state()) or (
            check_foreground and foreground_covers_screen()
        )

    if mode == VISIBILITY_WINDOW:
        return should_hide(query_state())

    # 未知取值：按最保守的「不隐藏」，避免用户设错后宠物消失
    return False
