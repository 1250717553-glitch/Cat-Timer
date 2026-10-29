"""主窗口：事件列表 + 增删改 + 每秒实时刷新 + 到点提醒。

刷新策略（重要）：
    QTimer 每 1000ms 只重写「剩余时间」列的单元格文本，
    绝不重建整个表格 —— 重建会丢失选中状态并造成闪烁。
    文本与原来相同时直接跳过，避免无谓重绘。

唯一节拍（_notify_timer）：
    全程序只有一个 1 秒 QTimer —— 常驻的到点提醒检查器。它每秒做三件事：
        1) 广播 time_changed 心跳（悬浮窗靠它刷新，主窗口隐藏时也不停）；
        2) 检查到点事件；
        3) 顺带刷新主窗口表格 —— 但只在窗口可见时做，不可见就不刷，省 CPU。
原因：窗口关闭后程序仍在托盘里运行，如果提醒检查跟着界面刷新一起停掉，
用户把窗口收到托盘后就再也收不到到点通知了 —— 那是本程序的核心功能。
反过来，如果界面刷新单独再开一个 1000ms 定时器，两个定时器的触发时刻会
各自漂移，出现「表格比悬浮窗慢一拍」的观感；只留一个节拍就没有这个问题。
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
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.models import Event, EventMode
from platform_win import sound
from core.notifier import (
    advance_repeating,
    at_repeat_limit,
    collect_due_events,
    due_message,
    ensure_daily_fresh,
    restart_counting,
    snooze,
)
from core.preferences import (
    VISIBILITY_ALWAYS,
    VISIBILITY_DESKTOP,
    VISIBILITY_MODES,
    VISIBILITY_WINDOW,
    load_sound_enabled,
    load_visibility_mode,
    save_preferences,
)
from core.storage import get_config_path, load_events, save_events
from core.timer import is_expired
from platform_win import fullscreen
from platform_win import single_instance
from ui import theme
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
    COL_REPEAT = 3
    COL_TIME = 4
    COL_STATUS = 5
    COLUMNS = ("名称", "模式", "显示格式", "重复", "剩余时间", "状态")

    # 全程序唯一的节拍间隔（毫秒）。提醒检查、心跳广播、表格刷新共用它。
    REFRESH_INTERVAL_MS = 1000

    # 全屏状态轮询限流间隔（秒）：复用 1 秒节拍，每 2 秒最多查一次系统状态，
    # 不新增任何定时器。
    FULLSCREEN_POLL_INTERVAL = 2.0

    # 显示格式的短标签（表格里用，比下拉框的全称短）
    _FORMAT_LABELS = {
        "days": "仅天",
        "days_hours": "天+小时",
        "days_hours_minutes": "天+小时+分钟",
        "full": "完整",
        "smart": "智能（省略 0 值）",
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
        # 显式一套浅色样式，不跟随系统主题（深色模式下表格会变成一片黑，
        # 和奶油白的悬浮卡片完全是两套视觉）。色值全部来自 ui/theme.py。
        self.setStyleSheet(theme.main_window_qss())

        # 事件列表；表格行号与列表下标严格一一对应（每次数据变更后重建表格）
        self.events: list[Event] = load_events() if events is None else events

        # 悬浮窗引用，由 main.py 创建后赋值。
        # 放这里是为了让托盘菜单能直接拿到它做「显示/隐藏悬浮窗」开关。
        self.floating_window: Optional[QWidget] = None

        # 可见性档位：desktop / window / always（默认 window = 三档上线前的行为）。
        # 下面四个状态字段只在 _handle_fullscreen_auto_hide 里改，
        # 全部由主窗口那个唯一的 1 秒节拍驱动，不新增定时器。
        self._visibility_mode = load_visibility_mode()
        self._sound_enabled = load_sound_enabled()
        self._last_fullscreen_check = 0.0          # 上次真正查询系统状态的时刻
        self._last_known_fullscreen: Optional[bool] = None  # 上次查询是否在全屏
        self._fullscreen_manual_override = False   # 用户本全屏会话手动开关过悬浮窗
        self._last_fullscreen_hidden = False       # 悬浮窗当前是否正被全屏自动隐藏

        self._build_ui()
        self._populate_table()

        # 唯一节拍：到点提醒检查（常驻，不受窗口显示/隐藏影响）。
        # 界面刷新不另开定时器 —— 由 check_notifications 在同一次回调里顺带做，
        # 这样表格与悬浮窗共用同一个 now，不会出现两边差一秒的错位。
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
        for col in (self.COL_MODE, self.COL_FORMAT, self.COL_REPEAT, self.COL_TIME, self.COL_STATUS):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)  # 双击 = 编辑
        self.table.itemSelectionChanged.connect(self._update_button_state)
        # 右键菜单：自建 QMenu，不沿用 Qt 内建右键行为，便于加入「重新开始」等动作
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
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
        path_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY.name()};")
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
            self.table.setItem(
                row,
                self.COL_REPEAT,
                QTableWidgetItem(self._repeat_text(event)),
            )
            # 「剩余时间」列的内容由 refresh_times 统一负责，这里先填一次初值
            self.table.setItem(row, self.COL_TIME, QTableWidgetItem(""))
            self.table.setItem(row, self.COL_STATUS, QTableWidgetItem(self._status_text(event)))

        self.refresh_times()
        self._update_button_state()

    # ------------------------------------------------------------------
    # 定时刷新（关键：只改文本，不重建表格）
    # ------------------------------------------------------------------

    def refresh_times(self, now: Optional[float] = None) -> None:
        """刷新「剩余时间」列。文本无变化则跳过，避免无谓重绘。

        :param now: 统一时间基准（秒级时间戳）。由定时器路径传入，保证表格与
                    悬浮窗用的是同一个 now；不传则自行取当前时间，
                    这样 _populate_table() 这类无参调用也能直接用。
        """
        now = time.time() if now is None else now
        for row, event in enumerate(self.events):
            item = self.table.item(row, self.COL_TIME)
            if item is None:
                continue
            text = self._time_text(event, now)
            if item.text() != text:  # 内容相同就不动，减少重绘
                item.setText(text)
            rep_item = self.table.item(row, self.COL_REPEAT)
            if rep_item is not None:
                rep_text = self._repeat_text(event)
                if rep_item.text() != rep_text:
                    rep_item.setText(rep_text)
            # 状态列也要跟着刷：循环次数用满的那一刻，状态从「启用」变成「已完成」
            status_item = self.table.item(row, self.COL_STATUS)
            if status_item is not None:
                status_text = self._status_text(event)
                if status_item.text() != status_text:
                    status_item.setText(status_text)

    @staticmethod
    def _time_text(event: Event, now: float) -> str:
        """「剩余时间」列文本。

        规则与悬浮窗共用 ui/text_format.py，区别只是表格里带「还剩 / 已过」前缀。
        """
        return event_time_text_with_prefix(event, now)

    @staticmethod
    def _repeat_text(event: Event) -> str:
        """「重复」列文本。

        一次性事件（repeat_seconds 为 None）显示「—」；
        循环事件显示「每 N 分钟」（分钟不成整数时退化为「每 N 秒」），
        有次数上限时再带上「× N」。
        已真实提醒次数只在有计数时追加在括号里 —— 悬浮窗卡片不显示它，
        避免窗口宽度跳动。
        """
        rs = event.repeat_seconds
        if rs is None:
            return "—"
        if rs % 60 == 0:
            period = f"每 {rs // 60} 分钟"
        else:
            period = f"每 {rs} 秒"
        if event.repeat_limit is not None and event.repeat_limit > 0:
            period = f"{period} × {event.repeat_limit}"
        # repeat_count 可能为 None（手工改坏 JSON、或老版本数据缺字段）。
        # 直接比较会在「打开管理窗口」这一步抛 TypeError，等于整个程序打不开 ——
        # 而这条只是展示用文案，绝不能因为一个坏字段崩掉界面。
        # 必须同时挡住负数：表达式 `-5 or 0` 的结果仍是 -5（or 只判真假，负数也是真），
        # 界面上就会出现「已 -5 次」。max(0, ...) 把 None / 0 / 负数都收进合法区间。
        count = max(0, event.repeat_count or 0)
        if count > 0:
            return f"{period}（已 {count} 次）"
        return period

    @staticmethod
    def _status_text(event: Event) -> str:
        """「状态」列文本。

        「已完成」= 循环次数用满：事件还留在列表里（用户可能想看看结果或删掉），
        但不会再提醒。
        """
        if not event.enabled:
            return "已停用"
        if at_repeat_limit(event):
            # 次数用完显示「已完成 N/N」：表示「今天做完了」，不是「过期了」。
            # 无限次数（repeat_limit 为 None）永远不会走到这个分支（永远可提醒），
            # 因此这里 limit 恒为有限值，直接拼 x/x 不会出现 None。
            return f"已完成 {event.repeat_count}/{event.repeat_limit}"
        return "启用"

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
        # 用户直接关掉对话框 / 按 Esc = 取消
        # （QMessageBox 的 escapeButton() 实测为 None，Esc 不会误触发 No）
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

    def _show_context_menu(self, pos) -> None:
        """表格右键菜单。

        一次性事件没有「重新开始」入口（它们用「编辑」改时间）。
        """
        row = self.table.rowAt(pos.y())
        if row < 0:
            self.table.clearSelection()
        else:
            self.table.selectRow(row)
        menu = QMenu(self.table)
        edit_action = menu.addAction("编辑")
        edit_action.triggered.connect(self._edit_event)
        restart_action = menu.addAction("重新开始")
        # 仅循环事件可用；一次性事件沿用「编辑」改时间即可，置灰提示
        restart_action.setEnabled(self._is_repeating(row))
        restart_action.triggered.connect(lambda: self._restart_event(row))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _is_repeating(self, row: int) -> bool:
        """该行事件是否为有效循环事件（repeat_seconds 为正）。"""
        if row < 0 or row >= len(self.events):
            return False
        ev = self.events[row]
        return (
            ev.mode == EventMode.COUNTDOWN
            and ev.repeat_seconds is not None
            and ev.repeat_seconds > 0
        )

    def _restart_event(self, row: int) -> None:
        """手动「重新开始」一个循环事件：计数清零、日期记为今天、fired_at 清空、
        anchor 置为 now + 周期。restart_counting 返回新对象，原地替换并落盘。"""
        if not self._is_repeating(row):
            return
        event = self.events[row]
        new_event = restart_counting(event)
        if new_event is None:
            return
        self.events[row] = new_event
        self._after_data_changed(select_row=row)

    # ------------------------------------------------------------------
    # 到点提醒
    # ------------------------------------------------------------------

    def check_notifications(self) -> None:
        """检查是否有事件到点；有则发通知并立即落盘。

        由 _notify_timer 每秒调用，**窗口隐藏时依然运行**。它同时是全程序
        唯一的 1 秒节拍：先广播心跳，再顺带刷新主窗口表格。
        """
        now = time.time()
        # 常驻心跳：无论是否有到点事件，每秒都广播一次给悬浮窗。
        # 悬浮窗不再自带定时器，靠这个信号驱动刷新，主窗口隐藏也能继续。
        self.time_changed.emit(now)

        # 表格刷新并进这个节拍里：窗口不可见时不做，保留「收到托盘就省 CPU」的
        # 原设计；可见时才刷，用户看到的仍是每秒跳动的时间。
        if self.isVisible():
            self.refresh_times(now)

        # 全屏自动隐藏检查：复用当前节拍按时间差限流，不新增定时器。
        self._handle_fullscreen_auto_hide(now)

        # 单出口方案 B：一次检查 = 一次落盘。
        # 1) 先自检跨天归零（幂等，各入口内部也各自自检；这里集中拿返回值）；
        #    changed 记「是否有任何改动」（重置 OR 后续标记），供唯一落盘判定。
        # 2) 再收集到点事件。collect_due_events 内部同样自检，返回的 due 非空
        #    即表示要推进/标记（也是改动），所以落盘条件是 changed or due，
        #    恰好覆盖 main.py 启动扫描对 mark_expired_as_fired 返回值的同一约定。
        changed = False
        for _event in self.events:
            if ensure_daily_fresh(_event, now):
                changed = True

        due = collect_due_events(self.events, now)

        # 全屏隐藏期间不弹系统通知（用户正沉浸），但推进、标记、落盘照常，
        # 保证退出全屏后展示的仍是同一批「已到点」事件：不重弹、不丢失。
        suppressed = self._last_fullscreen_hidden

        for event in due:
            # 循环事件：推进到下一周期（内部已把 fired_at 清空，因为新周期
            # 尚未提醒）；只有「不推进」（一次性事件 / 已达上限）才把 fired_at
            # 置为 now 表示完结 —— 否则会把「这一轮提醒过」误写成「永久完结」，
            # 循环事件从此只响一次。
            if not advance_repeating(event, now):
                event.fired_at = now
            if not suppressed:
                self.notification_requested.emit(due_message(event))
                # 声音跟着气泡一起被抑制：全屏时用户既不看着屏幕也不在听，
                # 只响一声反而会打断（游戏里尤其明显）。语义必须与 suppressed 一致。
                if self._sound_enabled:
                    sound.play_notification()

        # 单出口落盘：有任何改动（跨天重置 / 到点标记）才写，绝不无条件保存。
        # 万一标记还没写盘程序就崩了，重启后会重复弹提醒 —— 故必须覆盖到点分支。
        if changed or due:
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

    def set_floating_visible(self, visible: bool) -> None:
        """手动开关悬浮窗（托盘 / 悬浮窗菜单调用）。

        用户主动开关过一次后，本次全屏会话的自动隐藏 / 自动恢复都让位，
        避免「刚藏起来又被弹回来」的来回打架；手动覆盖在下次进入或离开
        全屏时自动清除。
        """
        floating = self.floating_window
        if floating is None:
            return
        self._fullscreen_manual_override = True
        floating.setVisible(visible)


    # ------------------------------------------------------------------
    # 提示音开关
    # ------------------------------------------------------------------

    def sound_enabled(self) -> bool:
        """返回「到点提示音」当前是否开启（供托盘菜单同步勾选）。"""
        return self._sound_enabled

    def set_sound_enabled(self, enabled: bool) -> None:
        """切换「到点提示音」并落盘。

        只影响后续提醒，不会补播 / 补静音已经错过的那些 ——
        声音是「此刻发生」的事件，补播反而让人困惑。
        """
        self._sound_enabled = bool(enabled)
        save_preferences({"sound_enabled": self._sound_enabled})

    # ------------------------------------------------------------------
    # 可见性档位
    # ------------------------------------------------------------------

    def visibility_mode(self) -> str:
        """返回当前可见性档位（供托盘菜单同步勾选）。"""
        return self._visibility_mode

    def set_visibility_mode(self, mode: str) -> None:
        """切换可见性档位：立即生效并落盘。

        「立即生效」不只是下一次节拍 —— 从「窗口可见」切到「始终可见」时，
        正被自动隐藏的悬浮窗必须马上露出来，否则用户以为没生效。

        非法值直接忽略（保持原档位），避免把配置写坏。
        """
        if mode not in VISIBILITY_MODES or mode == self._visibility_mode:
            return

        self._visibility_mode = mode
        save_preferences({"visibility_mode": mode})

        # 换档等于换规则：手动覆盖作废、限流复位，然后立刻重新判定一次
        self._fullscreen_manual_override = False
        self._last_fullscreen_check = 0.0
        self._last_known_fullscreen = None

        if self._last_fullscreen_hidden and not self._should_hide_now():
            self._last_fullscreen_hidden = False
            if self.floating_window is not None and not self.floating_window.isVisible():
                self.floating_window.show()

    def _should_hide_now(self) -> bool:
        """按当前档位判断「现在是否该收起悬浮窗」。

        「仅桌面可见」档才去查前台窗口 —— 那个检测要多调几次 Win32 API，
        其余档位没必要每 2 秒付这个开销。
        """
        state = fullscreen.query_state()
        foreground_covers = (
            fullscreen.foreground_covers_screen()
            if self._visibility_mode == VISIBILITY_DESKTOP
            else False
        )
        return fullscreen.should_hide_for_mode(state, self._visibility_mode, foreground_covers)

    def _handle_fullscreen_auto_hide(self, now: float) -> None:
        """全屏自动隐藏检查：复用 1 秒节拍，2 秒限流，不新增定时器。

        隐藏只做 floating_window.hide()——其 hideEvent 会停掉动画定时器，
        从不销毁窗口；退出全屏时若有被自动隐藏的悬浮窗则恢复显示。
        """

        if now - self._last_fullscreen_check < self.FULLSCREEN_POLL_INTERVAL:
            return  # 限流：间隔内不重复查询系统状态（每个 1 秒节拍约 2 秒查一次）
        self._last_fullscreen_check = now

        in_fullscreen = self._should_hide_now()
        prev_fullscreen = self._last_known_fullscreen
        self._last_known_fullscreen = in_fullscreen
        floating = self.floating_window

        if in_fullscreen and not prev_fullscreen:
            # 进入新一次全屏会话：上一个会话残留的手动覆盖作废
            self._fullscreen_manual_override = False
        elif not in_fullscreen and prev_fullscreen:
            # 离开全屏：清掉手动覆盖；若有被自动隐藏的悬浮窗，恢复显示
            self._fullscreen_manual_override = False
            if self._last_fullscreen_hidden and floating is not None and not floating.isVisible():
                floating.show()
            self._last_fullscreen_hidden = False

        if not in_fullscreen or self._fullscreen_manual_override:
            return
        if floating is not None and floating.isVisible():
            floating.hide()
            self._last_fullscreen_hidden = True

    def add_event(self) -> None:
        """菜单「新增倒计时」入口：先把窗口显示出来，再弹对话框。

        （对话框若在窗口隐藏时弹出，用户会找不到主界面。）
        """
        self.show_and_activate()
        self._add_event()

    def quit_application(self) -> None:
        """真正退出程序：停定时器、落盘、释放单实例锁、退出事件循环。"""
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
        """窗口显示：立刻刷新一次，补上隐藏期间没刷的时间。

        隐藏期间 check_notifications 跳过表格刷新（省 CPU），所以重新显示时
        表格里的时间是旧的，必须先补刷一次再等下一个节拍。
        """
        super().showEvent(event)
        self.refresh_times()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt 命名风格)
        """点「关闭」= 收到托盘，不退出程序。

        退出只能走托盘菜单的「退出」（会释放单实例锁）。
        """
        event.ignore()  # 忽略关闭事件，窗口不销毁
        save_events(self.events)  # 保留原有的「最后落盘」逻辑
        self.hide()
