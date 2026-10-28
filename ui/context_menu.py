"""托盘 / 悬浮窗的右键菜单构造。

抽成独立模块，让托盘菜单、悬浮窗菜单（以及将来桌宠窗的菜单）复用同一套建项逻辑，
避免几处各写一份导致行为不一致。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from platform_win import autostart

if TYPE_CHECKING:  # 仅用于类型标注，避免运行时循环 import
    from ui.floating_window import FloatingWindow
    from ui.main_window import MainWindow
    from ui.tray import TrayIcon

# 菜单项 objectName（供托盘按名字取回 QAction 重新同步勾选状态）
AUTOSTART_ACTION_NAME = "trayActionAutostart"
FLOATING_ACTION_NAME = "trayActionFloating"


def _add_action(menu: QMenu, text: str, slot: Callable[[], None]) -> QAction:
    """往菜单里加一个普通菜单项。"""
    action = QAction(text, menu)
    action.triggered.connect(slot)
    menu.addAction(action)
    return action


def build_floating_menu(
    parent_window: QWidget,
    floating_window: "FloatingWindow",
    main_window: "MainWindow",
) -> QMenu:
    """悬浮窗的右键菜单：新增倒计时 / 管理倒计时 / 隐藏悬浮窗 / 退出。"""
    menu = QMenu(parent_window)
    _add_action(menu, "新增倒计时", main_window.add_event)
    _add_action(menu, "管理倒计时", main_window.show_and_activate)
    menu.addSeparator()
    _add_action(menu, "隐藏悬浮窗", floating_window.hide)
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


def _on_floating_toggled(checked: bool, action: QAction, main_window: "MainWindow") -> None:
    """显示 / 隐藏悬浮窗。

    操作后回读一次真实可见状态：若与请求不符，把勾选状态纠正过来，
    避免界面显示与实际不一致。
    """
    floating = getattr(main_window, "floating_window", None)
    if floating is None:
        return

    floating.setVisible(checked)
    if floating.isVisible() != checked:
        action.blockSignals(True)
        action.setChecked(floating.isVisible())
        action.blockSignals(False)


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
