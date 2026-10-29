"""托盘 / 悬浮窗的右键菜单构造。

抽成独立模块，让托盘菜单、悬浮窗菜单（以及将来桌宠窗的菜单）复用同一套建项逻辑，
避免几处各写一份导致行为不一致。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PySide6.QtGui import QAction, QActionGroup, QActionGroup
from PySide6.QtWidgets import QMenu, QWidget

from core.preferences import (
    VISIBILITY_ALWAYS,
    VISIBILITY_DESKTOP,
    VISIBILITY_WINDOW,
)
from platform_win import autostart

if TYPE_CHECKING:  # 仅用于类型标注，避免运行时循环 import
    from ui.floating_window import FloatingWindow
    from ui.main_window import MainWindow
    from ui.tray import TrayIcon

# 菜单项 objectName（供托盘按名字取回 QAction 重新同步勾选状态）
AUTOSTART_ACTION_NAME = "trayActionAutostart"
FLOATING_ACTION_NAME = "trayActionFloating"
# 曾经有 trayActionFullscreen（「全屏时自动隐藏」开关），已随「显示范围」三档
# 上线而删除 —— 它与「始终可见」同义，两个入口做一件事会让用户困惑。
SOUND_ACTION_NAME = "trayActionSound"
VISIBILITY_MENU_NAME = "trayMenuVisibility"
VISIBILITY_ACTION_PREFIX = "trayActionVisibility_"

# 「显示范围」三个互斥选项：(档位, 菜单文字, 说明)
_VISIBILITY_ITEMS: tuple[tuple[str, str, str], ...] = (
    (VISIBILITY_DESKTOP, "仅桌面可见", "有窗口盖住桌面时自动收起"),
    (VISIBILITY_WINDOW, "窗口可见", "只在全屏 / 免打扰时收起（默认）"),
    (VISIBILITY_ALWAYS, "始终可见", "任何情况下都不自动收起"),
)


def _add_action(menu: QMenu, text: str, slot: Callable[[], None]) -> QAction:
    """往菜单里加一个普通菜单项。"""
    action = QAction(text, menu)
    action.triggered.connect(slot)
    menu.addAction(action)
    return action


def _add_snooze_action(
    menu: QMenu,
    text: str,
    minutes: int,
    floating_window: "FloatingWindow",
    main_window: "MainWindow",
) -> QAction:
    """加一个「稍后提醒 N 分钟」菜单项。

    悬浮窗没有选中概念，作用于第一个到点且显示在桌面的倒计时：
    floating_window.first_snoozable_index() 找不到时置灰。
    """
    index = floating_window.first_snoozable_index()
    action = QAction(text, menu)
    # 找到可提醒事件才可用，否则置灰 —— 但置灰项不该可点击，
    # 因此 slot 里也再校验一次，双保险。
    action.setEnabled(index >= 0)
    action.triggered.connect(lambda: main_window.snooze_event_at(index, minutes))
    menu.addAction(action)
    return action


def build_floating_menu(
    parent_window: QWidget,
    floating_window: "FloatingWindow",
    main_window: "MainWindow",
) -> QMenu:
    """悬浮窗的右键菜单：新增 / 管理 / 稍后提醒 / 隐藏 / 退出。"""
    menu = QMenu(parent_window)
    _add_action(menu, "新增倒计时", main_window.add_event)
    _add_action(menu, "管理倒计时", main_window.show_and_activate)
    menu.addSeparator()
    _add_snooze_action(menu, "稍后提醒 5 分钟", 5, floating_window, main_window)
    _add_snooze_action(menu, "稍后提醒 15 分钟", 15, floating_window, main_window)
    menu.addSeparator()
    # 折叠/展开：点击箭头也是同一入口，菜单只是多一条更显眼的通道
    collapse_text = "展开悬浮窗" if floating_window.is_collapsed() else "折叠悬浮窗"
    _add_action(menu, collapse_text, floating_window.toggle_collapsed)
    menu.addSeparator()
    # 走主窗口入口：记录手动干预，防止与全屏自动隐藏打架
    _add_action(menu, "隐藏悬浮窗", lambda: main_window.set_floating_visible(False))
    menu.addSeparator()
    _add_action(menu, "退出", main_window.quit_application)
    return menu


def build_tray_menu(
    parent_window: QWidget,
    tray: "TrayIcon",
    main_window: "MainWindow",
) -> QMenu:
    """构造托盘右键菜单。

    :param parent_window: 菜单的父对象（必须是 QWidget，用于生命周期管理）
    :param tray: 托盘对象，用于在设置失败时弹提示
    :param main_window: 主窗口，菜单项的动作都委托给它
    """
    menu = QMenu(parent_window)

    # --- 显示 / 隐藏主窗口 ---
    _add_action(menu, "显示/隐藏主窗口", main_window.toggle_visibility)
    menu.addSeparator()

    # --- 新增 / 管理 ---
    _add_action(menu, "新增倒计时", main_window.add_event)
    _add_action(menu, "管理倒计时", main_window.show_and_activate)

    # --- 悬浮窗开关（悬浮窗不存在时整项不显示）---
    floating = getattr(main_window, "floating_window", None)
    if floating is not None:
        action_floating = QAction("显示/隐藏悬浮窗", menu)
        action_floating.setObjectName(FLOATING_ACTION_NAME)
        action_floating.setCheckable(True)
        # 先设初值再连信号，否则 setChecked 会触发一次 toggled 回调
        action_floating.setChecked(floating.isVisible())
        action_floating.toggled.connect(
            lambda checked: _on_floating_toggled(checked, action_floating, main_window)
        )
        menu.addAction(action_floating)

    # --- 显示范围（三档互斥单选）---
    # 这里**刻意不再提供**「全屏时自动隐藏」那样的布尔开关：
    # 它与「始终可见」同义，两个入口控制一件事，用户看不出哪个优先。
    menu.addMenu(_build_visibility_menu(menu, main_window))

    # --- 到点提示音（可勾选）---
    # 为什么这是独立开关而不是并进「显示范围」：
    #   显示范围管的是「窗口藏不藏」，声音管的是「响不响」。
    #   有人想要桌面常驻但静音（开会），也有人只想窗口藏起来但有声音（后台提醒），
    #   两者不是同一个维度，强行合并会让用户做不出想要的那一种组合。
    action_sound = QAction("到点提示音", menu)
    action_sound.setObjectName(SOUND_ACTION_NAME)
    action_sound.setCheckable(True)
    # 先设初值再连信号，否则 setChecked 会触发一次 toggled 回调
    action_sound.setChecked(main_window.sound_enabled())
    action_sound.toggled.connect(
        lambda checked, a=action_sound: _on_sound_toggled(checked, a, main_window)
    )
    menu.addAction(action_sound)

    menu.addSeparator()

    # --- 开机自启（可勾选）---
    action_autostart = QAction("开机自启", menu)
    action_autostart.setObjectName(AUTOSTART_ACTION_NAME)
    action_autostart.setCheckable(True)
    action_autostart.setChecked(autostart.is_enabled())
    action_autostart.toggled.connect(
        lambda checked: _on_autostart_toggled(checked, action_autostart, tray)
    )
    menu.addAction(action_autostart)

    menu.addSeparator()

    # --- 退出 ---
    _add_action(menu, "退出", main_window.quit_application)

    return menu


def _on_sound_toggled(checked: bool, action: QAction, main_window: "MainWindow") -> None:
    """切换到点提示音：委托主窗口写偏好。"""
    main_window.set_sound_enabled(checked)


def _build_visibility_menu(parent: QMenu, main_window: "MainWindow") -> QMenu:
    """构造「显示范围」子菜单：三档互斥单选。

    用 QActionGroup(exclusive) 而不是三个独立 checkable —— 互斥由 Qt 保证，
    就不会出现「两个同时打勾」的非法状态。勾选状态在弹出前由托盘重新同步
    （见 ui/tray.py 的 _sync_menu_state），避免用户在别处改过之后显示不一致。
    """
    submenu = QMenu("显示范围", parent)
    submenu.setObjectName(VISIBILITY_MENU_NAME)

    group = QActionGroup(submenu)
    group.setExclusive(True)

    current = main_window.visibility_mode()
    for mode, text, tip in _VISIBILITY_ITEMS:
        action = QAction(text, submenu)
        action.setObjectName(VISIBILITY_ACTION_PREFIX + mode)
        action.setCheckable(True)
        action.setChecked(mode == current)
        action.setToolTip(tip)
        action.triggered.connect(lambda _checked=False, m=mode: main_window.set_visibility_mode(m))
        group.addAction(action)
        submenu.addAction(action)

    return submenu


def _on_floating_toggled(checked: bool, action: QAction, main_window: "MainWindow") -> None:
    """显示 / 隐藏悬浮窗。

    操作后回读一次真实可见状态：若与请求不符，把勾选状态纠正过来，
    避免界面显示与实际不一致。
    """
    floating = getattr(main_window, "floating_window", None)
    if floating is None:
        return

    # 走主窗口入口：记录手动干预，防止全屏自动隐藏与手动操作打架
    main_window.set_floating_visible(checked)
    if floating.isVisible() != checked:
        action.blockSignals(True)
        action.setChecked(floating.isVisible())
        action.blockSignals(False)


def _on_visibility_toggled(checked: bool, mode: str, main_window: "MainWindow") -> None:
    """显示范围换档：委托主窗口立即生效并落盘（非法值在那边被忽略）。"""
    if checked:
        main_window.set_visibility_mode(mode)


def _on_autostart_toggled(checked: bool, action: QAction, tray: "TrayIcon") -> None:
    """切换开机自启。

    写入注册表可能失败（被安全软件拦截等），失败时把勾选状态回滚，
    避免出现「界面显示已开启、实际没写进去」的假象。
    """
    ok = autostart.enable() if checked else autostart.disable()
    if ok:
        return

    action.blockSignals(True)  # 回滚时不要再次触发 toggled
    action.setChecked(autostart.is_enabled())
    action.blockSignals(False)
    tray.show_message("开机自启设置失败，请检查系统权限。")
