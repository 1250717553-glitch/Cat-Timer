"""新增 / 编辑事件的对话框。

只负责「把表单值收集成一个 Event」，不做任何持久化（由 MainWindow 统一负责）。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QMessageBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from core.models import DisplayFormat, Event, EventMode

# 模式下拉项：显示文本 -> 枚举的 value 字符串（顺序即下拉顺序）
#
# 注意：这里存的是枚举的 .value 字符串，而不是枚举对象本身。
# 原因：EventMode / DisplayFormat 都继承自 str，PySide6 在把 Python 对象
# 放进 QVariant 时会把这类「str 子类」解包成普通 str，于是 currentData()
# 拿回来的是 "countdown" 而不是 EventMode.COUNTDOWN。
# 一旦这个纯字符串流进 Event，to_dict() 里的 self.mode.value 就会抛
# AttributeError，导致配置无法保存。所以统一存字符串、取值时显式还原枚举。
_MODE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("倒计时", EventMode.COUNTDOWN.value),
    ("正计时", EventMode.COUNT_UP.value),
)

# 显示格式下拉项：8 项，顺序严格对应 DisplayFormat 枚举定义顺序
_FORMAT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("仅天", DisplayFormat.DAYS.value),
    ("天+小时", DisplayFormat.DAYS_HOURS.value),
    ("天+小时+分钟", DisplayFormat.DAYS_HOURS_MINUTES.value),
    ("完整", DisplayFormat.FULL.value),
    ("总计秒", DisplayFormat.TOTAL_SECONDS.value),
    ("总计分钟", DisplayFormat.TOTAL_MINUTES.value),
    ("总计小时", DisplayFormat.TOTAL_HOURS.value),
    ("总计天数", DisplayFormat.TOTAL_DAYS.value),
)


def _show_warning(parent: QWidget, title: str, text: str) -> None:
    """弹出一个按钮文字为中文的警告框（Qt 默认翻译未加载时按钮是英文）。"""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.button(QMessageBox.StandardButton.Ok).setText("确定")
    box.exec()


class EventDialog(QDialog):
    """事件编辑对话框。

    :param parent: 父窗口
    :param event: None 表示新增；传入 Event 表示编辑（会把已有值回填到表单）
    """

    def __init__(self, parent: Optional[QWidget] = None, event: Optional[Event] = None) -> None:
        super().__init__(parent)
        self._event = event  # 编辑模式下的原始事件，用于保留 id 与 fired_at

        self.setWindowTitle("编辑事件" if event is not None else "新增事件")
        self.setModal(True)
        self.setMinimumWidth(360)

        self._build_ui()

        if event is not None:
            self._load_from_event(event)
        else:
            self._apply_defaults()

    # ------------------------------------------------------------------
    # 构建界面
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        """创建所有表单控件并布局。"""
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：距离考试")
        self.name_edit.setMaxLength(60)

        # 模式
        self.mode_combo = QComboBox()
        for text, value in _MODE_OPTIONS:
            self.mode_combo.addItem(text, value)

        # 日期：带日历弹窗，默认今天
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QDate.currentDate())

        # 时间：默认 00:00:00
        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm:ss")
        self.time_edit.setTime(QTime(0, 0, 0))

        # 显示格式
        self.format_combo = QComboBox()
        for text, value in _FORMAT_OPTIONS:
            self.format_combo.addItem(text, value)
        self.format_combo.setCurrentIndex(
            list(v for _, v in _FORMAT_OPTIONS).index(DisplayFormat.DAYS_HOURS_MINUTES.value)
        )

        # 两个开关，默认都勾选
        self.enabled_check = QCheckBox("启用")
        self.enabled_check.setChecked(True)
        self.show_on_desktop_check = QCheckBox("显示在桌面")
        self.show_on_desktop_check.setChecked(True)

        form = QFormLayout()
        form.addRow("名称", self.name_edit)
        form.addRow("模式", self.mode_combo)
        form.addRow("日期", self.date_edit)
        form.addRow("时间", self.time_edit)
        form.addRow("显示格式", self.format_combo)
        form.addRow("", self.enabled_check)
        form.addRow("", self.show_on_desktop_check)

        # 底部按钮：确定 / 取消
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)  # 走 accept() 以便先做校验
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    # 默认值 / 回填
    # ------------------------------------------------------------------

    def _apply_defaults(self) -> None:
        """新增模式的默认值：日期今天、时间 00:00:00。"""
        self.date_edit.setDate(QDate.currentDate())
        self.time_edit.setTime(QTime(0, 0, 0))

    def _load_from_event(self, event: Event) -> None:
        """编辑模式：把事件已有值回填到表单。"""
        self.name_edit.setText(event.name)
        self._select_by_data(self.mode_combo, self._enum_value(event.mode))
        self._select_by_data(self.format_combo, self._enum_value(event.display_format))

        # 时间戳 -> 本地时间，再拆成日期与时间两半
        dt = datetime.fromtimestamp(event.anchor_ts)
        self.date_edit.setDate(QDate(dt.year, dt.month, dt.day))
        self.time_edit.setTime(QTime(dt.hour, dt.minute, dt.second))

        self.enabled_check.setChecked(event.enabled)
        self.show_on_desktop_check.setChecked(event.show_on_desktop)

    @staticmethod
    def _enum_value(value: object) -> str:
        """取枚举的 .value。

        兜底：若传入的已是纯字符串（str 子类被解包的情况），原样返回。
        """
        return getattr(value, "value", value)

    @staticmethod
    def _select_by_data(combo: QComboBox, value: object) -> None:
        """按 itemData 选中下拉项（避免依赖 findData 对 Python 对象的比较行为）。"""
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    # ------------------------------------------------------------------
    # 校验与取值
    # ------------------------------------------------------------------

    def accept(self) -> None:
        """点击「确定」时先校验；名称为空则提示并拒绝关闭。"""
        if not self.name_edit.text().strip():
            _show_warning(self, "提示", "请填写事件名称。")
            self.name_edit.setFocus()
            return
        super().accept()

    def get_event(self) -> Event:
        """把表单内容收集成 Event。

        - 编辑模式：保留原 id 与 fired_at
        - 新增模式：id 由 Event 自动生成（uuid4）

        下拉框里存的是枚举的 value 字符串，这里显式还原成枚举对象，
        保证 Event.mode / display_format 是真正的枚举（否则 to_dict() 会崩）。
        """
        name = self.name_edit.text().strip()
        mode = EventMode(self.mode_combo.currentData())
        display_format = DisplayFormat(self.format_combo.currentData())

        # QDate/QTime -> Python date/time -> 本地时区时间戳
        py_date = self.date_edit.date().toPython()
        py_time = self.time_edit.time().toPython()
        anchor_ts = datetime.combine(py_date, py_time).timestamp()

        common: dict[str, object] = dict(
            name=name,
            mode=mode,
            anchor_ts=anchor_ts,
            display_format=display_format,
            enabled=self.enabled_check.isChecked(),
            show_on_desktop=self.show_on_desktop_check.isChecked(),
        )

        if self._event is not None:
            fired_at = self._event.fired_at
            # 若目标时间被改到未来，说明用户重设了截止时间，应清空「已提醒」标记。
            # 否则 A4 的提醒逻辑以 fired_at is None 为判据，这个事件将永远不再提醒。
            if fired_at is not None and anchor_ts > time.time():
                fired_at = None
            return Event(id=self._event.id, fired_at=fired_at, **common)
        return Event(**common)
