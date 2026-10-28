"""桌面悬浮倒计时窗（纯文字卡片）。

窗口标志缺一不可：
    FramelessWindowHint   —— 无边框
    WindowStaysOnTopHint  —— 始终置顶
    Tool                  —— 【关键】不加会出现在任务栏和 Alt+Tab 里
    WA_TranslucentBackground —— 让圆角外的区域真正透明

绘制方案：paintEvent 画圆角卡片背景，文字用 QLabel 子控件。
选 QLabel 而不是全部 drawText，是因为布局系统能自动算好 CJK 文本的尺寸，
内容自适应（adjustSize）不用手写 QFontMetrics 排版代码，更好维护。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import QGridLayout, QLabel, QMenu, QWidget

from core.models import Event
from core.storage import get_config_path
from ui import theme
from ui.context_menu import build_floating_menu
from ui.text_format import EXPIRED_TEXT, event_time_text

if TYPE_CHECKING:  # 仅类型标注，避免运行时循环 import
    from ui.main_window import MainWindow


class FloatingWindow(QWidget):
    """桌面悬浮卡片。

    :param main_window: 管理窗口。悬浮窗从它那里读事件列表，动作也委托给它。
    """

    # 位置文件放在 config.json 同一个目录下
    POSITION_FILENAME = "floating_window.json"
    REFRESH_INTERVAL_MS = 1000
    # 默认位置：主屏右下角，留 40px 边距
    SCREEN_MARGIN = 40

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(None)  # 不设父窗口：主窗口收到托盘时悬浮窗要留在桌面上
        self._main_window = main_window

        # (名称标签, 时间标签, 事件) —— 定时刷新只改标签文本，不重建控件
        self._rows: list[tuple[QLabel, QLabel, Event]] = []
        self._drag_offset: Optional[QPoint] = None

        self._setup_window()
        self._setup_layout()

        self.rebuild()          # 先建行，尺寸确定后才能算默认位置
        self.restore_position()  # 位置记忆（含多屏失效校验）

        # 每秒刷新一次文本
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_texts)
        self._timer.start(self.REFRESH_INTERVAL_MS)

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def _setup_window(self) -> None:
        """窗口标志与属性。"""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            # 桌面小组件不该抢输入焦点（否则点一下就把你正在打的字打断了）
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle("桌面宠物计时器 · 悬浮窗")
        self.setMinimumWidth(theme.MIN_WIDTH)

        # 右键菜单：自定义策略 + 信号，才能拿到点击位置
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu_requested)

    def _setup_layout(self) -> None:
        """网格布局：左列事件名，右列时间。"""
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(
            theme.PADDING_H, theme.PADDING_V, theme.PADDING_H, theme.PADDING_V
        )
        self._grid.setHorizontalSpacing(theme.COLUMN_SPACING)
        self._grid.setVerticalSpacing(theme.ROW_SPACING)
        # 左列吸收多余宽度，时间列自然贴右
        self._grid.setColumnStretch(0, 1)

    # ------------------------------------------------------------------
    # 内容
    # ------------------------------------------------------------------

    def visible_events(self) -> list[Event]:
        """悬浮窗要显示的事件：启用 且 显示在桌面。"""
        return [e for e in self._main_window.events if e.enabled and e.show_on_desktop]

    def rebuild(self) -> None:
        """重建行控件（事件增删改后调用），并自适应尺寸。"""
        self._clear_rows()

        events = self.visible_events()
        if not events:
            hint = self._make_label("还没有倒计时，右键新增", theme.TEXT_SECONDARY, theme.FONT_SIZE_HINT)
            self._grid.addWidget(hint, 0, 0, 1, 2, Qt.AlignmentFlag.AlignCenter)
        else:
            for row, event in enumerate(events):
                name_label = self._make_label(
                    event.name, theme.TEXT_SECONDARY, theme.FONT_SIZE_NAME, elide=True
                )
                time_label = self._make_label("", theme.TEXT_PRIMARY, theme.FONT_SIZE_TIME)
                self._grid.addWidget(
                    name_label, row, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                self._grid.addWidget(
                    time_label, row, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self._rows.append((name_label, time_label, event))

        self.refresh_texts()
        self.adjustSize()
        self._keep_on_screen()

    def refresh_texts(self) -> None:
        """每秒调用：只改文本，文本无变化则跳过。"""
        now = time.time()
        changed = False

        for _name_label, time_label, event in self._rows:
            text = event_time_text(event, now)
            if time_label.text() != text:
                time_label.setText(text)
                changed = True

            # 「已到点」用高亮色，其余用主色。用属性记录当前色调，避免重复设调色板。
            tone = "accent" if text == EXPIRED_TEXT else "primary"
            if time_label.property("tone") != tone:
                time_label.setProperty("tone", tone)
                self._apply_color(
                    time_label, theme.TEXT_ACCENT if tone == "accent" else theme.TEXT_PRIMARY
                )
                changed = True

        # 位数变化会让文本变宽/变窄，必要时重新自适应尺寸，避免被裁切
        if changed and self.sizeHint() != self.size():
            self.adjustSize()
            self._keep_on_screen()

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名风格)
        """画圆角卡片背景。文字由 QLabel 子控件绘制在上层。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 内缩半个像素，否则 1px 描边会被窗口边缘裁掉一半
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, theme.CARD_RADIUS, theme.CARD_RADIUS)

        painter.fillPath(path, theme.CARD_BG)
        if theme.CARD_BORDER_WIDTH > 0:
            painter.setPen(QPen(theme.CARD_BORDER, theme.CARD_BORDER_WIDTH))
            painter.drawPath(path)

    # ------------------------------------------------------------------
    # 控件辅助
    # ------------------------------------------------------------------

    def _make_label(
        self,
        text: str,
        color,
        point_size: int,
        elide: bool = False,
    ) -> QLabel:
        """创建一个不拦截鼠标事件的文字标签。"""
        label = QLabel(text, self)
        # 关键：鼠标事件穿透到父窗口，否则拖不动、双击也失效
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        font = theme.make_font(point_size)
        label.setFont(font)
        self._apply_color(label, color)

        if elide:
            label.setMaximumWidth(theme.MAX_NAME_WIDTH)
            label.setText(
                QFontMetrics(font).elidedText(
                    text, Qt.TextElideMode.ElideRight, theme.MAX_NAME_WIDTH
                )
            )
        return label

    @staticmethod
    def _apply_color(label: QLabel, color) -> None:
        """设置标签文字颜色（用调色板，避免样式表解析开销）。"""
        palette = label.palette()
        palette.setColor(QPalette.ColorRole.WindowText, color)
        label.setPalette(palette)

    def _clear_rows(self) -> None:
        """清空布局里的所有控件。"""
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._rows.clear()

    # ------------------------------------------------------------------
    # 位置记忆
    # ------------------------------------------------------------------

    def _position_path(self) -> Path:
        """位置文件路径（与 config.json 同目录）。"""
        return get_config_path().with_name(self.POSITION_FILENAME)

    def save_position(self) -> None:
        """原子写位置到 JSON。"""
        path = self._position_path()
        tmp = path.with_name(path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"x": self.x(), "y": self.y()}, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            print(f"[floating] 警告：保存悬浮窗位置失败 -> {exc}")

    def restore_position(self) -> None:
        """读取位置；文件缺失/损坏/坐标失效时回落到默认位置。"""
        position: Optional[tuple[int, int]] = None
        path = self._position_path()

        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                position = (int(data["x"]), int(data["y"]))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as exc:
                print(f"[floating] 警告：悬浮窗位置读取失败，使用默认位置 -> {exc}")
                position = None

        if position is None or not self._is_position_on_screen(*position):
            self.move_to_default_position()
        else:
            self.move(*position)

    def _is_position_on_screen(self, x: int, y: int) -> bool:
        """坐标是否与任一有效屏幕有交集。

        用户拔掉副显示器后，记忆的坐标可能落在不存在的区域 —— 必须校验，
        否则窗口会「存在但看不见」，用户以为程序坏了。
        """
        rect = QRect(x, y, self.width(), self.height())
        return any(screen.availableGeometry().intersects(rect) for screen in QGuiApplication.screens())

    def move_to_default_position(self) -> None:
        """默认位置：主屏右下角，留 SCREEN_MARGIN 边距。"""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.x() + area.width() - self.width() - self.SCREEN_MARGIN
        y = area.y() + area.height() - self.height() - self.SCREEN_MARGIN
        # 窗口比屏幕还大时（极端情况）夹回左上角，保证可见
        self.move(max(x, area.x()), max(y, area.y()))

    def _keep_on_screen(self) -> None:
        """把窗口夹回屏幕内，防止自适应变宽后跑出可视区。"""
        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = min(max(self.x(), area.x()), area.x() + area.width() - self.width())
        y = min(max(self.y(), area.y()), area.y() + area.height() - self.height())
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    # ------------------------------------------------------------------
    # 鼠标交互
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """左键按下：记录鼠标与窗口左上角的偏移。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        """左键拖动：按偏移移动窗口。"""
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        """松开左键：结束拖动并保存位置。"""
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.save_position()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """双击：打开管理窗口。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None  # 双击不是拖动，清掉偏移
            self._main_window.show_and_activate()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _on_context_menu_requested(self, position: QPoint) -> None:
        """右键：弹出悬浮窗自己的菜单。"""
        menu = build_floating_menu(self, self, self._main_window)
        menu.exec(self.mapToGlobal(position))

    # ------------------------------------------------------------------
    # 显示 / 隐藏
    # ------------------------------------------------------------------

    def toggle_visibility(self) -> None:
        """切换显示 / 隐藏（托盘菜单调用）。"""
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def hideEvent(self, event) -> None:  # noqa: N802
        """隐藏时停掉刷新定时器省 CPU。"""
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        """显示时恢复定时器并立刻刷一次。"""
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(self.REFRESH_INTERVAL_MS)
        self.refresh_texts()

    # 注意：这里**刻意不重写 closeEvent**。
    #
    # 曾写过一版 closeEvent（event.ignore() + self.hide()），结果导致
    # QApplication.quit() 完全失效 —— 点了「退出」程序不退出。
    # 原因：Qt 退出时会向顶层窗口发送关闭请求，只要有窗口在 closeEvent 里
    # ignore()，整个退出流程就会被中断，事件循环继续运行。
    #
    # 本窗口没有标题栏和关闭按钮，用户点不到「关闭」；即使有人调用 close()，
    # QWidget 的默认行为也是隐藏窗口（不销毁），正好就是想要的效果。
    # 所以不重写反而是正确做法。
