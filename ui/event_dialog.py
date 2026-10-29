"""新增 / 编辑事件的对话框。

只负责「把表单值收集成一个 Event」，不做任何持久化（由 MainWindow 统一负责）。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QDate, QDateTime, QTime
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
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
    ("智能（省略 0 值）", DisplayFormat.SMART.value),
    ("总计秒", DisplayFormat.TOTAL_SECONDS.value),
    ("总计分钟", DisplayFormat.TOTAL_MINUTES.value),
    ("总计小时", DisplayFormat.TOTAL_HOURS.value),
    ("总计天数", DisplayFormat.TOTAL_DAYS.value),
)

# 重复提醒下拉项：显示文本 -> 周期秒数（None = 一次性）
# 与 event_dialog 中「从现在起」的预设按钮互配：每 15/30/45/60/90 分钟
_REPEAT_OPTIONS: tuple[tuple[str, object], ...] = (
    ("不重复", None),
    ("每 15 分钟", 15 * 60),
    ("每 30 分钟", 30 * 60),
    ("每 45 分钟", 45 * 60),
    ("每 60 分钟", 60 * 60),
    ("每 90 分钟", 90 * 60),
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

        # 一键预设：从现在起 N 分钟后到点。把日期+时间一起设成 now+N，
        # 并把模式切到倒计时 —— 常见用法是「5 分钟后提醒我」，省去手填日期时间。
        self.preset_row = QHBoxLayout()
        for minutes in (5, 15, 25, 30, 60):
            button = QPushButton(f"{minutes} 分钟")
            button.clicked.connect(lambda _=False, m=minutes: self.apply_preset(m))
            self.preset_row.addWidget(button)
        self.preset_widget = QWidget()
        self.preset_widget.setLayout(self.preset_row)

        # 显示格式
        self.format_combo = QComboBox()
        for text, value in _FORMAT_OPTIONS:
            self.format_combo.addItem(text, value)
        self.format_combo.setCurrentIndex(
            list(v for _, v in _FORMAT_OPTIONS).index(DisplayFormat.DAYS_HOURS_MINUTES.value)
        )

        # 重复提醒：默认不重复
        self.repeat_combo = QComboBox()
        for text, value in _REPEAT_OPTIONS:
            self.repeat_combo.addItem(text, value)

        # 重复次数上限：0 用 specialValueText 显示成「无限」，
        # 这样「留空 = 无限」不需要额外的复选框，一个控件表达两种状态。
        self.repeat_limit_spin = QSpinBox()
        self.repeat_limit_spin.setRange(0, 999)
        self.repeat_limit_spin.setValue(0)
        self.repeat_limit_spin.setSpecialValueText("无限")
        self.repeat_limit_spin.setToolTip("一共提醒多少次；选「无限」就一直循环下去")

        # 两个开关，默认都勾选
        self.enabled_check = QCheckBox("启用")
        self.enabled_check.setChecked(True)
        self.show_on_desktop_check = QCheckBox("显示在桌面")
        self.show_on_desktop_check.setChecked(True)

        # 提醒时间段：QTimeEdit 没有真正的「空」状态，所以用「全天」勾选框控制。
        # 勾选「全天」= 不限（存成空字符串）；取消 = 指定一个时间段（开始/结束）。
        # 刻意**不用 00:00 表示空** —— 那会让「凌晨 0 点开始」没法表达；
        # 用一个布尔开关来区分「未设置」和「设置了 00:00」，语义才不暧昧。
        self.all_day_check = QCheckBox("全天")
        self.all_day_check.setChecked(True)
        self.active_start_edit = QTimeEdit()
        self.active_start_edit.setDisplayFormat("HH:mm")
        self.active_start_edit.setTime(QTime(9, 0))
        self.active_end_edit = QTimeEdit()
        self.active_end_edit.setDisplayFormat("HH:mm")
        self.active_end_edit.setTime(QTime(18, 0))
        self.all_day_check.toggled.connect(self._on_all_day_toggled)

        form = QFormLayout()
        form.addRow("名称", self.name_edit)
        form.addRow("模式", self.mode_combo)
        form.addRow("日期", self.date_edit)
        form.addRow("时间", self.time_edit)
        self.from_now_label = QLabel("从现在起")
        form.addRow(self.from_now_label, self.preset_widget)
        form.addRow("显示格式", self.format_combo)
        form.addRow("重复", self.repeat_combo)
        form.addRow("重复次数", self.repeat_limit_spin)
        # 时间段：先放「全天」勾选框，下面两行时间随勾选启用/置灰
        form.addRow("提醒时段", self.all_day_check)
        form.addRow("开始", self.active_start_edit)
        form.addRow("结束", self.active_end_edit)
        form.addRow("", self.enabled_check)
        form.addRow("", self.show_on_desktop_check)

        # 重新开始计数按钮：仅循环事件可见（一次性事件用「编辑」改时间）。
        # 放在信息区首页即可，不占模式切换空间。
        self.restart_button = QPushButton("重新开始计数")
        self.restart_button.setToolTip("把循环次数清零，从此刻起重新倒计一个周期")
        self.restart_button.clicked.connect(self._on_restart_clicked)

        # 底部按钮：确定 / 取消
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)  # 走 accept() 以便先做校验
        buttons.rejected.connect(self.reject)

        # 重复模式下，日期/时间预设行的提示由「从现在起」改为「到点时间」
        self.repeat_combo.currentIndexChanged.connect(self._on_repeat_changed)
        self._on_repeat_changed(self.repeat_combo.currentIndex())

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.restart_button)
        layout.addWidget(buttons)
        self._on_all_day_toggled(self.all_day_check.isChecked())

    # ------------------------------------------------------------------
    # 默认值 / 回填
    # ------------------------------------------------------------------

    def _apply_defaults(self) -> None:
        """新增模式的默认值：日期今天、时间 00:00:00。"""
        self.date_edit.setDate(QDate.currentDate())
        self.time_edit.setTime(QTime(0, 0, 0))

    def _on_repeat_changed(self, _index: int) -> None:
        """重复下拉切换时，更新「从现在起」提示标签，并启停「重复次数」。

        一次性（不重复）事件：时间是指「从现在起 N 分钟后」的预设；
        循环事件：时间直接填「到点时间」（周期到点后重新倒计），
        提示改为「到点时间」更贴切。

        「重复次数」只在循环事件下有意义：选「不重复」时置灰，
        否则用户会以为一次性事件也能设次数。

        「重新开始计数」按钮也只对循环事件可见：一次性事件没有次数可清零，
        它们重设时间走「确定」即可 —— 按钮用隐藏而非置灰，突出当前模式的可用操作。
        """
        repeating = self.repeat_combo.currentData() is not None
        self.from_now_label.setText("到点时间" if repeating else "从现在起")
        self.repeat_limit_spin.setEnabled(repeating)
        self.restart_button.setVisible(repeating)

    def _on_all_day_toggled(self, checked: bool) -> None:
        """「全天」勾选联动两行 QTimeEdit 的启用状态。

        勾选「全天」= 不限时段，开始/结束两行置灰（不可改但保留上次值，
        取消勾选时立刻能用）；取消勾选 = 指定时间段，两行可编辑。

        存盘时按勾选状态落空字符串或 "HH:MM"，详见 get_event()。
        """
        self.active_start_edit.setEnabled(not checked)
        self.active_end_edit.setEnabled(not checked)

    def _on_restart_clicked(self) -> None:
        """「重新开始计数」：把当前编辑事件的计数清零、日期记为今天、
        fired_at 清空、anchor 置为 now + 周期。

        这里只更新表单上的「编辑对象」与「当前事件」字段，真正落盘由
        MainWindow 的确定流程（get_event + save_events）负责。
        仅循环事件会走到这里（按钮在非重复时已隐藏）。
        """
        event = self._event
        if event is None or event.repeat_seconds is None or event.repeat_seconds <= 0:
            return
        import core.notifier as notifier
        restarted = notifier.restart_counting(event)
        if restarted is None:
            return
        # 把「重新开始」的结果同步到时点表单：dialog 的确定流程（get_event）会
        # 用 date_edit/time_edit 重新算 anchor_ts，若不把表单也拨到「现在」，
        # 用户点确定后 anchor 会被旧的目标时间覆盖，重新开始就白做了。
        dt = datetime.fromtimestamp(restarted.anchor_ts)
        self.date_edit.setDate(QDate(dt.year, dt.month, dt.day))
        self.time_edit.setTime(QTime(dt.hour, dt.minute, dt.second))
        self._event = restarted

    def apply_preset(self, minutes: int) -> None:
        """把日期+时间设为「从现在起 minutes 分钟后」，模式切到倒计时。

        不校验 minutes 是否可解析为 QDateTime —— 只用整数分钟做加法，
        跨日 / 跨月由 QDateTime 自动进位，远期日期也照常可用。
        """
        target = QDateTime.currentDateTime().addSecs(minutes * 60)
        self.date_edit.setDate(target.date())
        self.time_edit.setTime(target.time())
        self._select_by_data(self.mode_combo, EventMode.COUNTDOWN.value)

    def _load_from_event(self, event: Event) -> None:
        """编辑模式：把事件已有值回填到表单。"""
        self.name_edit.setText(event.name)
        self._select_by_data(self.mode_combo, self._enum_value(event.mode))
        self._select_by_data(self.format_combo, self._enum_value(event.display_format))

        # 重复下拉：选到匹配的周期秒数（不匹配的周期秒数落到「不重复」）
        self._select_by_data(self.repeat_combo, event.repeat_seconds)
        if event.repeat_seconds not in (None, *(v for _, v in _REPEAT_OPTIONS[1:])):
            self._select_by_data(self.repeat_combo, None)

        # 次数上限：None（无限）显示成 0（即「无限」）
        self.repeat_limit_spin.setValue(event.repeat_limit or 0)

        # 时间戳 -> 本地时间，再拆成日期与时间两半
        dt = datetime.fromtimestamp(event.anchor_ts)
        self.date_edit.setDate(QDate(dt.year, dt.month, dt.day))
        self.time_edit.setTime(QTime(dt.hour, dt.minute, dt.second))

        self.enabled_check.setChecked(event.enabled)
        self.show_on_desktop_check.setChecked(event.show_on_desktop)

        # 提醒时间段：非空即「指定了时间段」-> 取消「全天」并回填两行时间。
        # 两个 QTimeEdit 的格式是 HH:mm；数据里存的就是该格式的字符串。
        if event.active_start and event.active_end:
            self.all_day_check.setChecked(False)
            self.active_start_edit.setTime(QTime.fromString(event.active_start, "HH:mm"))
            self.active_end_edit.setTime(QTime.fromString(event.active_end, "HH:mm"))
        else:
            self.all_day_check.setChecked(True)

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
        repeat_seconds = self.repeat_combo.currentData()
        if repeat_seconds is not None:
            repeat_seconds = int(repeat_seconds)

        # 次数上限：控件里的 0 表示「无限」，转成 None；
        # 「不重复」的事件即使控件里残留了数字也必须归 None（否则会存进一个
        # 永远用不到的上限，以后改成循环时行为会莫名其妙）。
        repeat_limit = self.repeat_limit_spin.value()
        if repeat_seconds is None or repeat_limit <= 0:
            repeat_limit = None

        # QDate/QTime -> Python date/time -> 本地时区时间戳
        py_date = self.date_edit.date().toPython()
        py_time = self.time_edit.time().toPython()
        anchor_ts = datetime.combine(py_date, py_time).timestamp()

        # 提醒时间段：勾选「全天」= 存空字符串（不限）；否则把两行 QTimeEdit
        # 的 HH:mm 存成字符串。这里不用 00:00 表示空 —— QTimeEdit 存 00:00 是
        # 一个**合法**的开始时间（凌晨 0 点），空与 00:00 混为一谈会丢语义。
        if self.all_day_check.isChecked():
            active_start = ""
            active_end = ""
        else:
            active_start = self.active_start_edit.time().toString("HH:mm")
            active_end = self.active_end_edit.time().toString("HH:mm")

        common: dict[str, object] = dict(
            name=name,
            mode=mode,
            anchor_ts=anchor_ts,
            display_format=display_format,
            enabled=self.enabled_check.isChecked(),
            show_on_desktop=self.show_on_desktop_check.isChecked(),
            repeat_seconds=repeat_seconds,
            repeat_limit=repeat_limit,
            active_start=active_start,
            active_end=active_end,
        )

        if self._event is not None:
            fired_at = self._event.fired_at
            # 若目标时间被改到未来，说明用户重设了截止时间，应清空「已提醒」标记。
            # 否则 A4 的提醒逻辑以 fired_at is None 为判据，这个事件将永远不再提醒。
            if fired_at is not None and anchor_ts > time.time():
                fired_at = None
            return Event(
                id=self._event.id,
                fired_at=fired_at,
                **common,
                repeat_count=self._event.repeat_count,
                # 编辑对话框不改计数（重新开始由独立按钮负责），日期跟着计数走，
                # 必须延续旧值 —— 否则点一次确定就把跨天重置逻辑搞乱。
                repeat_count_date=self._event.repeat_count_date,
            )
        return Event(**common)
