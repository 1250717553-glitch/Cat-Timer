"""桌面宠物计时器 · 程序入口。

运行方式（在项目根目录执行）：
    .venv\\Scripts\\python.exe main.py

启动顺序（顺序很重要，别随意调整）：
    1. 建 QApplication
    2. setQuitOnLastWindowClosed(False) —— 否则关窗即退出，托盘就没了
    3. 抢单实例锁 —— 拿不到说明已有实例，提示后退出
    4. 读事件 -> 标记过期事件为已提醒 -> 落盘（实现「重启不重弹」）
    5. 建主窗口
    6. 建悬浮窗并显示，把「数据变更」信号接到它的重建上
       （悬浮窗必须先于托盘创建，托盘菜单要读它做显示/隐藏开关）
    7. 建托盘，连好「到点通知」信号
    8. 安装退出冒烟钩子（仅当环境变量存在时生效）
    9. 进入事件循环
"""

from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from core.notifier import mark_expired_as_fired
from core.storage import load_events, save_events
from platform_win import single_instance
from ui.floating_window import FloatingWindow
from ui.main_window import MainWindow
from ui.tray import TrayIcon, load_app_icon

APP_NAME = "桌面宠物计时器"

# 退出冒烟钩子的环境变量名（见 tools/check_quit.py）。
# 打包成 exe 后没法往进程里注入代码，只能靠环境变量触发一次「退出」，
# 才能验证打包产物「点退出能不能真的退出」。
AUTO_QUIT_ENV = "DPT_AUTO_QUIT_MS"


def _install_auto_quit_hook(app: QApplication, window: MainWindow) -> bool:
    """安装退出冒烟钩子。返回是否启用。

    仅当环境变量 DPT_AUTO_QUIT_MS 存在时生效，正常使用（不设该变量）完全无影响。
    走的路径与托盘菜单「退出」完全一致（MainWindow.quit_application）。
    """
    raw = os.environ.get(AUTO_QUIT_ENV)
    if not raw:
        return False
    try:
        delay_ms = max(0, int(raw))
    except ValueError:
        return False

    QTimer.singleShot(delay_ms, window.quit_application)
    return True


def main() -> int:
    """创建应用、托盘与主窗口，进入事件循环。"""
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # 全程序统一图标：设一次就成了所有窗口（主窗口 / 悬浮窗）和任务栏的默认图标，
    # 不必在每个窗口里各设一遍 —— 漏设的那个窗口就会显示 Python 默认图标。
    app.setWindowIcon(load_app_icon())

    # 【关键】关闭最后一个窗口时不退出程序。
    # 本程序是托盘常驻型：关掉主窗口只是收到托盘，进程要继续跑着做提醒。
    app.setQuitOnLastWindowClosed(False)

    # --- 单实例检查 ---
    if not single_instance.try_lock():
        _warn_already_running()
        return 0
    # 兜底：任何退出路径都释放锁，避免异常退出后残留
    app.aboutToQuit.connect(single_instance.release)

    # --- 启动扫描：把「关机期间就已到点」的事件直接标记为已提醒 ---
    # 这样重启后不会补弹一堆过期提醒。
    events = load_events()
    if mark_expired_as_fired(events, time.time()):
        save_events(events)

    # --- 主窗口 ---
    window = MainWindow(events)

    # --- 悬浮窗 ---
    floating = FloatingWindow(window)
    window.floating_window = floating
    # 增删改后悬浮窗要重建行（行数可能变了），用信号连线而不是让主窗口直接依赖它
    window.events_changed.connect(floating.rebuild)
    floating.show()

    # --- 托盘（必须在悬浮窗之后建：菜单要读 window.floating_window）---
    tray = TrayIcon(window)
    # 主窗口只发信号、不直接依赖托盘：这里把信号接到气泡通知上
    window.notification_requested.connect(tray.show_message)
    tray.show()

    window.show()

    # --- 退出冒烟钩子（仅测试时生效）---
    if _install_auto_quit_hook(app, window):
        print(f"[main] 已启用退出冒烟钩子，{os.environ[AUTO_QUIT_ENV]} ms 后自动退出。")

    return app.exec()


def _warn_already_running() -> None:
    """提示「程序已在运行」。"""
    box = QMessageBox()
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(APP_NAME)
    box.setText("程序已经在运行了。")
    box.setInformativeText("请在系统托盘区（右下角）找到它的图标。")
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.button(QMessageBox.StandardButton.Ok).setText("知道了")
    box.exec()


if __name__ == "__main__":
    sys.exit(main())
