"""托盘图标与交互。

【坑点】QSystemTrayIcon 继承自 QObject 而**不是 QWidget**，所以它没有 style() 方法。
要拿系统标准图标必须走 QApplication.style()，写成 self.style() 会直接 AttributeError。
同理，它也不能当 QMenu 的父窗口 —— QMenu 的 parent 必须是 QWidget。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QStyle, QSystemTrayIcon, QWidget

from core.paths import first_existing
from core.preferences import VISIBILITY_MODES
from platform_win import autostart
from ui.context_menu import (
    AUTOSTART_ACTION_NAME,
    FLOATING_ACTION_NAME,
    VISIBILITY_ACTION_PREFIX,
    build_tray_menu,
)

if TYPE_CHECKING:  # 仅用于类型标注，避免运行时循环 import
    from ui.main_window import MainWindow

# 通知标题统一用程序名
APP_NAME = "桌面宠物计时器"

# 自定义图标候选路径（相对资源基准目录）。有就用，没有就退回系统标准图标。
# 注意：必须走 core.paths，打包后资源在 sys._MEIPASS 下，
# 用 __file__ 推路径在打包后会失效。
# 正式图标 app.ico 排在最前：它由 tools/make_app_icon.py 从 "App .png" 生成，
# 与宠物形象同源（奶油色圆角底座 + 橘猫大头）。后面的旧文件只是兜底，
# 保证素材被误删时托盘仍有个图标可显示。
_ICON_CANDIDATES = (
    "assets/app.ico",
    "assets/app.png",
    "assets/tray.ico",
    "assets/tray.png",
)


def load_app_icon() -> QIcon:
    """获取托盘图标。

    优先使用 assets/ 里的图标文件；找不到时退回系统标准图标 ——
    QSystemTrayIcon **必须有图标，否则根本不会显示**。
    """
    candidate = first_existing(_ICON_CANDIDATES)
    if candidate is not None:
        icon = QIcon(str(candidate))
        if not icon.isNull():
            return icon

    # 兜底：系统标准图标。注意用 QApplication.style()，不是 self.style()
    style = QApplication.style()
    if style is not None:
        return style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
    return QIcon()


class TrayIcon(QSystemTrayIcon):
    """系统托盘图标。

    :param main_window: 主窗口，菜单项与双击动作都委托给它
    """

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(main_window)  # 挂到主窗口下，随主窗口一起销毁
        self._main_window = main_window

        self.setIcon(load_app_icon())
        self.setToolTip(APP_NAME)

        # 菜单父对象必须是 QWidget，所以传主窗口而不是 self
        self.tray_menu = build_tray_menu(main_window, self, main_window)
        self.setContextMenu(self.tray_menu)
        # 每次弹出前重新读一次注册表，保证勾选状态与真实情况一致
        self.tray_menu.aboutToShow.connect(self._sync_menu_state)

        self.activated.connect(self._on_activated)

    # ------------------------------------------------------------------
    # 通知
    # ------------------------------------------------------------------

    def show_message(self, message: str, title: str = APP_NAME, msecs: int = 5000) -> None:
        """弹出系统通知气泡。

        :param message: 正文
        :param title: 标题，默认「桌面宠物计时器」
        :param msecs: 停留毫秒数
        """
        self.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, msecs)

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """双击托盘图标 = 显示并激活主窗口。"""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._main_window.show_and_activate()

    def _sync_menu_state(self) -> None:
        """菜单弹出前，把所有可勾选项的状态与真实情况对齐。

        状态可能被别的入口改掉（例如用户在悬浮窗自己的右键菜单里点了「隐藏悬浮窗」），
        不重新同步就会出现勾选与实际不符。
        """
        # 开机自启 -> 读注册表
        self._sync_check(
            self.tray_menu.findChild(QAction, AUTOSTART_ACTION_NAME), autostart.is_enabled()
        )
        # 悬浮窗 -> 读真实可见性
        floating = getattr(self._main_window, "floating_window", None)
        if floating is not None:
            self._sync_check(
                self.tray_menu.findChild(QAction, FLOATING_ACTION_NAME), floating.isVisible()
            )
        # 显示范围 -> 三个互斥项按当前档位重新勾选
        current_mode = self._main_window.visibility_mode()
        for mode in VISIBILITY_MODES:
            self._sync_check(
                self.tray_menu.findChild(QAction, VISIBILITY_ACTION_PREFIX + mode),
                mode == current_mode,
            )

    @staticmethod
    def _sync_check(action: Optional[QAction], actual: bool) -> None:
        """把勾选状态改成 actual（阻塞信号，避免反向触发一次切换动作）。"""
        if action is None or action.isChecked() == actual:
            return
        action.blockSignals(True)
        action.setChecked(actual)
        action.blockSignals(False)
