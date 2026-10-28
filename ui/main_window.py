"""主窗口：事件列表 + 增删改 + 每秒实时刷新 + 到点提醒。

刷新策略（重要）：
    QTimer 每 1000ms 只重写「剩余时间」列的单元格文本，
    绝不重建整个表格 —— 重建会丢失选中状态并造成闪烁。
    文本与原来相同时直接跳过，避免无谓重绘。

两个定时器（刻意分开）：
    _timer        —— 界面刷新。窗口隐藏时停掉省 CPU。
    _notify_timer —— 到点提醒检查。**常驻不停**。
原因：窗口关闭后程序仍在托盘里运行，如果提醒检查跟着界面刷新一起停掉，
用户把窗口收到托盘后就再也收不到到点通知了 —— 那是本程序的核心功能。
"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.models import Event, EventMode
from core.notifier import collect_due_events, due_message, snooze
from core.storage import get_config_path, load_events, save_events
from core.timer import is_expired
from platform_win import single_instance
from ui.event_dialog import EventDialog
from ui.text_format import event_time_text_with_prefix


class MainWindow(QMainWindow):
    """事件管理主窗口。"""

    # 到点提醒信号：携带通知正文，由 main.py 接到托盘上弹出气泡。
    # 用信号而不是直接持有托盘对象，是为了让主窗口不依赖托盘（可独立测试）。
    notification_requested = Signal(str)

    # 事件数据变更信号：新增 / 编辑 / 删除后发出，main.py 接到悬浮窗的 rebuild 上
    events_changed = Signal()

    # 每秒心跳信号：由**常驻**的提醒定时器（check_notifications）发出。
    # 悬浮窗据此刷新，不依赖主窗口是否可见 —— 否则主窗口收到托盘后悬浮窗会停摆。
    time_changed = Signal(float)

    # 列索引常量
    COL_NAME = 0
    COL_MODE = 1
    COL_FORMAT = 2
    COL_TIME = 3
    COL_STATUS = 4
    COLUMNS = ("名称", "模式", "显示格式", "剩余时间", "状态")

    # 刷新间隔（毫秒）
    REFRESH_INTERVAL_MS = 1000

    # 显示格式的短标签（表格里用，比下拉框的全称短）
    _FORMAT_LABELS = {
        "days": "仅天",
        "days_hours": "天+小时",
        "days_hours_minutes": "天+小时+分钟",
        "full": "完整",
        "total_seconds": "总计秒",
        "total_minutes": "总计分钟",
        "total_hours": "总计小时",
        "total_days": "总计天数",
    }

    def __init__(self, events: Optional[list[Event]] = None) -> None:
        """
        :param events: 预先加载好的事件列表。
                       main.py 需要在建窗之前先做「重启不重弹」标记，
                       传进来可以避免重复读盘；不传则自行 load_events()。
        """
        super().__init__()
        self.setWindowTitle("桌面宠物计时器 · 事件管理")
        self.resize(760, 420)

        # 事件列表；表格行号与列表下标严格一一对应（每次数据变更后重建表格）
        self.events: list[Event] = load_events() if events is None else events

        # 悬浮窗引用，由 main.py 创建后赋值。
        # 放这里是为了让托盘菜单能直接拿到它做「显示/隐藏悬浮窗」开关。
        self.floating_window: Optional[QWidget] = None

        self._build_ui()
        self._populate_table()

        # 定时器 1：界面刷新（窗口隐藏时停掉）
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_times)
        self._timer.start(self.REFRESH_INTERVAL_MS)

        # 定时器 2：到点提醒检查（常驻，不受窗口显示/隐藏影响）
        self._notify_timer = QTimer(self)
        self._notify_timer.timeout.connect(self.check_notifications)
        self._notify_timer.start(self.REFRESH_INTERVAL_MS)

    # ------------------------------------------------------------------
    # 构建界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """搭建中央控件：表格 + 按钮行 + 配置路径提示。"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # --- 表格 ---
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)  # 只读
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(self.COL_NAME, QHeaderView.ResizeMode.Stretch)
        for col in (self.COL_MODE, self.COL_FORMAT, self.COL_TIME, self.COL_STATUS):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)  # 双击 = 编辑
        self.table.itemSelectionChanged.connect(self._update_button_state)
        layout.addWidget(self.table)

        # --- 按钮行 ---
        button_row = QHBoxLayout()
        self.add_button = QPushButton("新增")
        self.edit_button = QPushButton("编辑")
        self.delete_button = QPushButton("删除")
        self.snooze_button = QPushButton("稍后提醒")
        self.add_button.clicked.connect(self._add_event)
        self.edit_button.clicked.connect(self._edit_event)
        self.delete_button.clicked.connect(self._delete_event)
        self.snooze_button.clicked.connect(self._snooze_event)

        button_row.addWidget(self.add_button)
        button_row.addWidget(self.edit_button)
        button_row.addWidget(self.delete_button)
        button_row.addWidget(self.snooze_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        # --- 配置路径提示 ---
        path_label = QLabel(f"配置文件：{get_config_path()}")
        path_label.setToolTip("事件数据保存在这个 JSON 文件里，可直接查看或备份")
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_label.setStyleSheet("color: #666;")
        layout.addWidget(path_label)

        self._update_button_state()

    # ------------------------------------------------------------------
    # 表格填充
    # ------------------------------------------------------------------

    def _populate_table(self) -> None:
        """按 self.events 重建表格（仅在数据变更时调用，不在定时刷新里调用）。"""
        self.table.setRowCount(len(self.events))

        for row, event in enumerate(self.events):
            self.table.setItem(row, self.COL_NAME, QTableWidgetItem(event.name))
            self.table.setItem(
                row,
                self.COL_MODE,
                QTableWidgetItem("倒计时" if event.mode == EventMode.COUNTDOWN else "正计时"),
            )
            self.table.setItem(
                row,
                self.COL_FORMAT,
                QTableWidgetItem(self._FORMAT_LABELS.get(event.display_format.value, "—")),
            )
            # 「剩余时间」列的内容由 refresh_times 统一负责，这里先填一次初值
            self.table.setItem(row, self.COL_TIME, QTableWidgetItem(""))
            self.table.setItem(
                row, self.COL_STATUS, QTableWidgetItem("启用" if event.enabled else "已停用")
            )

        self.refresh_times()
        self._update_button_state()

    # ------------------------------------------------------------------
    # 定时刷新（关键：只改文本，不重建表格）
    # ------------------------------------------------------------------

    def refresh_times(self) -> None:
        """每秒刷新「剩余时间」列。文本无变化则跳过，避免无谓重绘。"""
        now = time.time()
        for row, event in enumerate(self.events):
            item = self.table.item(row, self.COL_TIME)
            if item is None:
                continue
            text = self._time_text(event, now)
            if item.text() != text:  # 内容相同就不动，减少重绘
                item.setText(text)

    @staticmethod
    def _time_text(event: Event, now: float) -> str:
        """「剩余时间」列文本。

        规则与悬浮窗共用 ui/text_format.py，区别只是表格里带「还剩 / 已过」前缀。
        """
        return event_time_text_with_prefix(event, now)

    # ------------------------------------------------------------------
    # 增 / 改 / 删
    # ------------------------------------------------------------------

    def _selected_row(self) -> int:
        """返回当前选中行号；未选中返回 -1。"""
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return -1
        return rows[0].row()

    def _update_button_state(self) -> None:
        """没有选中行时，禁用「编辑」「删除」。
        
        稍后提醒：仅当选中行是「已到点的倒计时」才可用，否则置灰。
        """
        row = self._selected_row()
        has_selection = row >= 0
        self.edit_button.setEnabled(has_selection)
        self.delete_button.setEnabled(has_selection)
        self.snooze_button.setEnabled(self._is_snoozable(row))

    def _is_snoozable(self, row: int) -> bool:
        """该行事件是否可「稍后提醒」：倒计时 且 已到点。

        判断不依赖 fired_at：到点后无论是否已提醒过（fired_at 已记录），
        都应允许稍后提醒 —— 这正是「到点后把它往后推 5/15 分钟」的入口。
        """
        if row < 0 or row >= len(self.events):
            return False
        event = self.events[row]
        return event.mode == EventMode.COUNTDOWN and is_expired(event)

    def _add_event(self) -> None:
        """新增事件。"""
        dialog = EventDialog(self)
        if dialog.exec() != EventDialog.DialogCode.Accepted:
            return
        self.events.append(dialog.get_event())
        self._after_data_changed(select_row=len(self.events) - 1)

    def _edit_event(self) -> None:
        """编辑当前选中的事件。"""
        row = self._selected_row()
        if row < 0:
            return
        dialog = EventDialog(self, event=self.events[row])
        if dialog.exec() != EventDialog.DialogCode.Accepted:
            return
        self.events[row] = dialog.get_event()
        self._after_data_changed(select_row=row)

    def _delete_event(self) -> None:
        """删除当前选中的事件（先确认）。"""
        row = self._selected_row()
        if row < 0:
            return
        event = self.events[row]

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("确认删除")
        box.setText(f"确定要删除事件「{event.name}」吗？")
        box.setInformativeText("删除后无法恢复。")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.button(QMessageBox.StandardButton.Yes).setText("删除")
        box.button(QMessageBox.StandardButton.No).setText("取消")
        box.setDefaultButton(QMessageBox.StandardButton.No)  # 默认落在「取消」，防误删
        if box.exec() != QMessageBox.StandardButton.Yes:
            return

        del self.events[row]
        self._after_data_changed(select_row=min(row, len(self.events) - 1))

    def _snooze_event(self) -> None:
        """把当前选中的已到点倒计时「稍后提醒」5 或 15 分钟。

        用 QMessageBox 弹二选一（托盘气泡不支持按钮，只能靠主窗口交互）。
        snooze 是纯函数、返回新 Event；拿到后原地替换选中行并落盘。
        """
        row = self._selected_row()
        if not self._is_snoozable(row):
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("稍后提醒")
        box.setText(f"「{self.events[row].name}」已到点，稍后多久再提醒？")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        yes = box.button(QMessageBox.StandardButton.Yes)
        yes.setText("5 分钟")
        yes.setDefault(True)
        no = box.button(QMessageBox.StandardButton.No)
        no.setText("15 分钟")
        # 用户直接关掉对话框 = 取消（No 也当取消处理？）
        box.exec()
        clicked = box.clickedButton()
        if clicked == yes:
            minutes = 5
        elif clicked == no:
            minutes = 15
        else:
            return  # 关掉对话框 / 按 Esc -> 取消

        self.snooze_event_at(row, minutes)

    def snooze_event_at(self, row: int, minutes: int) -> bool:
        """把指定行的倒计时「稍后提醒」minutes 分钟（供悬浮窗菜单复用）。

        悬浮窗没有选中概念，也要能对「第一个到点且显示在桌面」的事件做稍后提醒，
        所以把「定位后原地替换 + 落盘 + 刷新」这一步抽成公共方法。
        """
        if not self._is_snoozable(row):
            return False
        new_event = snooze(self.events[row], minutes)
        if new_event is None:  # 理论不会发生（_is_snoozable 已把关），防御性返回
            return False
        self.events[row] = new_event
        self._after_data_changed(select_row=row)
        return True

    def _after_data_changed(self, select_row: int = -1) -> None:
        """数据变更后的统一收尾：重建表格 + 立即落盘 + 通知悬浮窗 + 恢复选中行。"""
        self._populate_table()
        save_events(self.events)
        self.events_changed.emit()  # 悬浮窗据此重建行（行数可能变了）
        if 0 <= select_row < self.table.rowCount():
            self.table.selectRow(select_row)

    # ------------------------------------------------------------------
    # 事件回调
    # ------------------------------------------------------------------

    def _on_cell_double_clicked(self, row: int, _column: int) -> None:
        """双击任意单元格 = 编辑该行事件。"""
        self.table.selectRow(row)
        self._edit_event()

    # ------------------------------------------------------------------
    # 到点提醒
    # ------------------------------------------------------------------

    def check_notifications(self) -> None:
        """检查是否有事件到点；有则发通知并立即落盘。

        由 _notify_timer 每秒调用，**窗口隐藏时依然运行**。
        """
        now = time.time()
        # 常驻心跳：无论是否有到点事件，每秒都广播一次给悬浮窗。
        # 悬浮窗不再自带定时器，靠这个信号驱动刷新，主窗口隐藏也能继续。
        self.time_changed.emit(now)

        due = collect_due_events(self.events, now)
        if not due:
            return

        for event in due:
            event.fired_at = now  # 先标记，防止下一秒重复提醒
            self.notification_requested.emit(due_message(event))

        # 立即落盘：万一标记还没写盘程序就崩了，重启后会重复弹提醒
        save_events(self.events)

    # ------------------------------------------------------------------
    # 供托盘菜单调用的公开方法
    # ------------------------------------------------------------------

    def show_and_activate(self) -> None:
        """显示、还原并激活主窗口（托盘双击 / 菜单「管理倒计时」调用）。"""
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def toggle_visibility(self) -> None:
        """切换主窗口显示 / 隐藏（菜单「显示/隐藏主窗口」调用）。"""
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_and_activate()

    def add_event(self) -> None:
        """菜单「新增倒计时」入口：先把窗口显示出来，再弹对话框。

        （对话框若在窗口隐藏时弹出，用户会找不到主界面。）
        """
        self.show_and_activate()
        self._add_event()

    def quit_application(self) -> None:
        """真正退出程序：停定时器、落盘、释放单实例锁、退出事件循环。"""
        self._timer.stop()
        self._notify_timer.stop()
        save_events(self.events)
        single_instance.release()

        app = QApplication.instance()
        if app is None:
            return

        # 【关键】先隐藏所有顶层窗口，再退出。
        #
        # 原因：主窗口的 closeEvent 会 ignore() 关闭事件（这是「关窗=收到托盘」的
        # 实现方式）。而 Qt 退出时会向顶层窗口发送关闭请求，只要有窗口拒绝关闭，
        # 整个退出流程就被中断 —— 表现就是「点了退出，程序不退出，托盘还在」。
        # 实测过：只要有一个「ignore 关闭事件」的顶层窗口存在，app.quit() 就会
        # 完全失效（事件循环继续运行，aboutToQuit 也不触发）。
        # hide() 不会触发 closeEvent，所以先全部藏起来再退出最稳。
        for widget in app.topLevelWidgets():
            widget.hide()

        app.quit()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名风格)
        """窗口显示：恢复界面刷新定时器，并立刻刷新一次（隐藏期间不会刷新）。"""
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(self.REFRESH_INTERVAL_MS)
        self.refresh_times()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt 命名风格)
        """窗口隐藏：停掉界面刷新定时器省 CPU。

        注意提醒定时器 _notify_timer 不在这里停 —— 收到托盘后仍要能提醒。
        """
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名风格)
        """点「关闭」= 收到托盘，不退出程序。

        退出只能走托盘菜单的「退出」（会释放单实例锁）。
        """
        event.ignore()  # 忽略关闭事件，窗口不销毁
        save_events(self.events)  # 保留原有的「最后落盘」逻辑
        self.hide()
