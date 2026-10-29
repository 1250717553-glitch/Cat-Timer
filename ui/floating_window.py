"""桌面悬浮窗：左边一只宠物猫，右边一张计时卡片，画在同一个 QWidget 里。

为什么合成一个窗口，而不是「猫窗口 + 卡片窗口」：
    两个窗口要自己同步位置、同步 z-order、同步拖拽，还得处理「拖猫时卡片跟不上」
    的错位；两个半透明窗口叠在一起，边缘还会出现互相穿透的暗边。
    一个窗口一次 paintEvent 画完，天然对齐，拖拽/缩放/隐藏都只涉及一个对象。

窗口标志缺一不可：
    FramelessWindowHint   —— 无边框
    WindowStaysOnTopHint  —— 始终置顶
    Tool                  —— 【关键】不加会出现在任务栏和 Alt+Tab 里
    WA_TranslucentBackground —— 让圆角外和猫身后的区域真正透明

绘制顺序（重要）：先画卡片圆角背景，**再**画猫。反过来的话卡片会盖住猫的右半身。
文字仍用 QLabel 子控件而不是 drawText：布局系统能自动算好 CJK 文本尺寸，
内容自适应不用手写 QFontMetrics 排版；但布局的内边距必须按卡片几何内缩，
否则文字会压到猫身上。

几何规则：
    [ 宠物区 PET_AREA_WIDTH ][ GAP ][      卡片区（自适应宽度）      ]
    宠物区宽度**固定**，不随卡片变化 —— 否则事件名一长猫就被挤扁。
    窗口高度 = max(宠物区高度, 卡片高度)，两个区各自垂直居中。
    「卡片中线落在猫嘴高度」不是写死锚点算出来的，而是「两边都垂直居中」的
    自然结果：实测猫嘴在素材高度的 0.46 处，居中后卡片中线正好在附近。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QCursor,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from core import pet_animator, placement
from core.models import Event, EventMode
from core.pet_animator import PetPose
from core.paths import backup_corrupt_file
from core.storage import get_config_path
from core.timer import compute_seconds, is_expired
from ui import pet_render, theme
from ui.context_menu import build_floating_menu
from ui.text_format import EXPIRED_TEXT, event_time_text, progress_ratio, repeat_suffix

if TYPE_CHECKING:  # 仅类型标注，避免运行时循环 import
    from ui.main_window import MainWindow


class _PetAnimator(QObject):
    """悬浮窗的动画节拍器：33ms 一跳，**只在窗口可见时**运行。

    为什么单独成一个对象，而不是在 FloatingWindow 里直接持一个 QTimer：
        项目里有一条既有约束 ——「悬浮窗不自建**刷新**定时器」。每秒的时间刷新
        只能有一个来源（主窗口那个常驻心跳），否则两个定时器相位不同会互相打架。
        动画是另一条时间轴（33ms），把它隔离在这个驱动对象里，窗口本身仍然
        不持有任何 QTimer，两类节拍的职责边界一目了然。
        顺带的好处：时间轴状态（呼吸相位、点击倒计时、鼠标节流）集中在一处，
        FloatingWindow 只负责「按 pose 画」。

    :param widget: 宿主窗口，用来取宠物区中心、触发重绘
    """

    # ≈30fps。33ms 是 30fps 的整数近似；再快对桌宠没有意义，只是白烧 CPU。
    INTERVAL_MS = 33

    # 鼠标位置的轮询间隔。QCursor.pos() 是系统调用，30fps 全量轮询纯属浪费；
    # 10Hz 足够让歪头跟手，肉眼察觉不到延迟。
    MOUSE_POLL_INTERVAL_S = 0.1

    def __init__(self, widget: "FloatingWindow") -> None:
        super().__init__(widget)
        self._widget = widget

        self._timer = QTimer(self)
        self._timer.setInterval(self.INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

        # 呼吸相位的起点取构造时刻，而不是每次显示的瞬间：
        # 隐藏再显示时动画从原相位接着走，不会每次弹出都从「吸满」重新开始。
        self._origin = time.monotonic()
        self._click_at: Optional[float] = None
        self._pose = PetPose()
        self._flipped = False

        self._head_pose = PetPose()
        self._last_mouse_poll = 0.0

    # -- 生命周期 ------------------------------------------------------

    def start(self) -> None:
        """开始跑动画（showEvent 调用）。重复调用无副作用。"""
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        """停止动画（hideEvent 调用）。这是硬性性能要求：不可见就不该有节拍。"""
        self._timer.stop()

    def is_running(self) -> bool:
        """动画是否在跑（供测试断言「隐藏后已停」）。"""
        return self._timer.isActive()

    # -- 外部输入 ------------------------------------------------------

    def set_flipped(self, flipped: bool) -> None:
        """设置是否左右镜像宠物。"""
        self._flipped = flipped
        self._recompute(time.monotonic())

    def trigger_click(self) -> None:
        """记录点击时刻，接下来 CLICK_DURATION_S 秒走点击反馈动画。"""
        self._click_at = time.monotonic()

    def pose(self) -> PetPose:
        """当前帧姿势（paintEvent 每帧读它）。"""
        return self._pose

    # -- 内部 ----------------------------------------------------------

    def _on_tick(self) -> None:
        """每 33ms 一跳：重算姿势，然后只重绘宠物区。"""
        self._recompute(time.monotonic())
        self._widget.update_pet_area()

    def _recompute(self, now: float) -> None:
        """按当前时刻算出一个新姿势（呼吸 + 点击 + 歪头）。"""
        breath = pet_animator.breathing_pose(now - self._origin)

        click_active = (
            self._click_at is not None
            and (now - self._click_at) < pet_animator.CLICK_DURATION_S
        )
        if self._click_at is not None and not click_active:
            self._click_at = None  # 动画结束，清掉起点，下次点击重新计

        click = PetPose()
        if click_active and self._click_at is not None:
            click = pet_animator.click_bounce_pose(now - self._click_at)

        head = self._poll_head(now)
        self._pose = pet_animator.blend(
            breath,
            click,
            head,
            click_active=click_active,
            flipped=self._flipped,
        )

    def _poll_head(self, now: float) -> PetPose:
        """读全局鼠标位置算歪头角度，带节流。

        「节流」不只是省 CPU：QCursor.pos() 在 Windows 上是一次系统调用，
        30fps 下每秒 30 次毫无必要 —— 鼠标 100ms 内移动的距离肉眼看不出差别。
        """
        if now - self._last_mouse_poll < self.MOUSE_POLL_INTERVAL_S:
            return self._head_pose
        self._last_mouse_poll = now

        center = self._widget.pet_center_global()
        self._head_pose = pet_animator.head_turn_pose(QCursor.pos().x(), center.x())
        return self._head_pose


class FloatingWindow(QWidget):
    """桌面悬浮宠物 + 计时卡片（同一个窗口）。

    :param main_window: 管理窗口。悬浮窗从它那里读事件列表，动作也委托给它。
    """

    # 位置文件放在 config.json 同一个目录下
    POSITION_FILENAME = "floating_window.json"
    # 默认位置：主屏右下角，留 40px 边距
    SCREEN_MARGIN = 40

    # --- 宠物区几何（写死，用常量表达，别散落在代码里）---
    # 宽度按素材宽高比 0.935 配高度算：150 × 0.935 ≈ 140，
    # 这样宠物区与猫的轮廓几乎重合，不会出现「框很大、猫很小」的空旷感。
    PET_AREA_WIDTH = 140
    PET_AREA_HEIGHT = 150
    # 宠物区与卡片之间的空隙。14 而不是 6：猫的尾巴/胡须是软边，
    # 贴太近会和卡片的圆角糊在一起，看起来像卡片压住了猫。
    PET_GAP = 14

    # 点猫折叠的交互参数（取代被删除的折叠三角）：
    # 轻点 vs 拖动的判定阈值 —— 位移与按住时长都低于阈值才算一次单击。
    CLICK_MAX_DRIFT_PX = 5
    CLICK_MAX_DURATION_MS = 300
    # 单击延迟窗口：250ms 内没有第二次点击才真正折叠。
    # 双击的第二击会先取消这个待折叠定时器，再走管理窗口逻辑，不会闪一下
    # 「折叠→展开」。代价是单击折叠有 250ms 延迟 —— Windows 自己区分
    # 单击/双击也是同样的机制，用户感知不到。
    CLICK_PENDING_MS = 250

    # 猫在宠物区里只占「峰值缩放」那么大：点击时放大会到 1.12 倍，
    # 如果静止时就铺满宠物区，放大那一帧会被窗口边缘切掉。
    PET_FIT_SCALE = 1.0 / pet_animator.CLICK_SCALE_PEAK

    # 旋转轴心（相对猫图片的比例）。放在头部而不是图片中心：
    # 绕图片中心转，整只猫像被钉在肚子上摇；绕头部转才是「歪头」。
    PET_PIVOT_X = 0.5
    PET_PIVOT_Y = 0.38

    # 翻面时的淡入时长与起始透明度。150ms 刚好够盖住「跳一下」的突兀感，
    # 又不至于让用户觉得卡；从 0.45 淡到 1.0 比从 0 开始更不刺眼。
    FLIP_FADE_MS = 150
    FLIP_FADE_FROM = 0.45

    def __init__(self, main_window: "MainWindow") -> None:
        super().__init__(None)  # 不设父窗口：主窗口收到托盘时悬浮窗要留在桌面上
        self._main_window = main_window

        # (名称标签, 时间标签, 事件) —— 定时刷新只改标签文本，不重建控件
        # (毛线球占位, 名称, 后缀, 时间, 事件) —— 从 3 元组扩到 5 元组：
        # 球需要在 paintEvent 里按绝对坐标绘制，占位控件提供「该画在哪」的几何；
        # 后缀单独成列是为了给它定宽（计数 3→10 时不让整窗抖宽）。
        self._rows: list[tuple[QWidget, QLabel, QLabel, QLabel, Event]] = []
        self._drag_offset: Optional[QPoint] = None
        # 「还有 N 项」提示标签。它**不在 _rows 里**（不是事件行），
        # 所以 _set_rows_visible 遍历 _rows 时管不到它 —— 折叠态必须单独隐藏，
        # 否则只剩猫和球的界面上会突兀地飘着一行字。
        self._more_label: Optional[QLabel] = None
        # 点猫折叠的单击判定状态：按下时刻 / 按下位置 / 待折叠的延迟定时器。
        # 单击不立即折叠，而是等 CLICK_PENDING_MS 窗口 —— 双击的第一击会先在
        # 250ms 内被取消，不会出现「折叠再展开闪一下」再弹管理窗口。
        self._press_time: float = 0.0
        self._press_global: Optional[QPoint] = None
        self._click_timer: Optional[QTimer] = None
        # 按下到松手之间是否已判定为拖动（位移/时长超阈值）。
        # 真 → 松手不折叠；假 → 松手进入单击延迟窗口（250ms 等双击）。
        self._dragging_candidate = False

        # 朝向：False = 猫在左、卡片向右；True = 猫在右、卡片向左。
        # 必须在 _setup_layout 之前初始化 —— 布局内边距要按它算。
        self._flipped = False
        # 折叠：True = 只剩宠物区，卡片不显示。同样要在布局前初始化，
        # 因为 _layout_margins 要按它收缩内边距。
        self._collapsed = False
        # 是否已经挂上 windowHandle().screenChanged（窗口第一次显示后才有 handle）
        self._screen_hooked = False
        self._hooked_screens: set[int] = set()

        self._setup_window()
        self._setup_layout()

        # 动画节拍器（33ms，仅可见时运行）。必须在 rebuild 之前建好：
        # 首次 paintEvent 会读 animator.pose()。
        self._animator = _PetAnimator(self)
        self._setup_flip_fade()

        self.rebuild()          # 先建行，尺寸确定后才能算默认位置
        self.restore_position()  # 位置记忆（含多屏失效校验）

        # 每秒刷新由主窗口的**常驻**提醒定时器（time_changed）驱动，
        # 不再自建 QTimer —— 否则同一秒被两个定时器各刷一遍、且相位不同。
        # 关键：time_changed 由 check_notifications 发出（与窗口可见性无关），
        # 所以主窗口 hide() 后悬浮窗照样每秒更新。
        self._main_window.time_changed.connect(self._on_time_changed)

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
        """外层竖向布局：中间放「事件名 | 时间」网格，上下加弹簧让它垂直居中。

        为什么要在网格外面再套一层：
            窗口高度取 max(宠物区高度, 卡片高度)。卡片比宠物矮时，多出来的高度
            必须均分到上下，卡片才会垂直居中 —— QGridLayout 自己不会居中，
            它会从顶部开始排，卡片就会贴着窗口上沿。
        """
        self._grid = QGridLayout()
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(theme.COLUMN_SPACING)
        self._grid.setVerticalSpacing(theme.ROW_SPACING)
        # 五列：毛线球 / 名称 / 后缀 / 弹簧 / 时间。
        #
        # 弹簧必须单独一列，不能靠「名称列拉伸」：
        # 名称短时（「喝水」只有两个字）拉伸会把后缀推到很右边，
        # 看起来像后缀是时间的一部分。空白要全部落在后缀之后，
        # 让「名称 + 后缀」作为一组紧贴左边。
        self._grid.setColumnStretch(1, 0)   # 后缀列不吸收空白
        self._grid.setColumnStretch(2, 1)   # 弹簧列吸收全部空白

        outer = QVBoxLayout(self)
        outer.setContentsMargins(*self._layout_margins())
        outer.addStretch(1)
        outer.addLayout(self._grid)
        outer.addStretch(1)

    def _layout_margins(self) -> tuple[int, int, int, int]:
        """按当前朝向算四个方向的内边距（左, 上, 右, 下）。

        卡片在哪一侧，就把「宠物区 + 间距」加到那一侧 —— 文字永远被推进
        卡片里，不会压到猫身上。翻转时左右对调，窗口总宽不变。
        """
        # 折叠态没有卡片，内边距全部归零，窗口只有宠物区贴边。
        if self._collapsed:
            return (0, 0, 0, 0)
        pet_side = self.PET_AREA_WIDTH + self.PET_GAP
        if self._flipped:
            return (theme.PADDING_H, theme.PADDING_V, pet_side + theme.PADDING_H, theme.PADDING_V)
        return (pet_side + theme.PADDING_H, theme.PADDING_V, theme.PADDING_H, theme.PADDING_V)

    # ------------------------------------------------------------------
    # 朝向（翻转）
    # ------------------------------------------------------------------

    def _setup_flip_fade(self) -> None:
        """翻面时的淡入动画。

        用 setWindowOpacity（Windows 原生分层窗口）而不是 QGraphicsOpacityEffect：
        后者会把整个窗口先渲染到离屏 pixmap 再合成，和 WA_TranslucentBackground
        叠加时在部分平台上会出现黑底。原生透明度还能连子控件（文字）一起淡。
        """
        self._flip_fade = QVariantAnimation(self)
        self._flip_fade.setDuration(self.FLIP_FADE_MS)
        self._flip_fade.setStartValue(self.FLIP_FADE_FROM)
        self._flip_fade.setEndValue(1.0)
        self._flip_fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._flip_fade.valueChanged.connect(self._on_flip_fade)

    def _on_flip_fade(self, value: object) -> None:
        self.setWindowOpacity(float(value))

    def is_flipped(self) -> bool:
        """当前是否处于翻转态（猫在右、卡片向左）。"""
        return self._flipped

    def is_collapsed(self) -> bool:
        """当前是否处于折叠态（只剩宠物区，卡片隐藏）。"""
        return self._collapsed

    def toggle_collapsed(self) -> None:
        """切换折叠态（菜单项 / 箭头共用入口）。"""
        self.set_collapsed(not self._collapsed)

    def set_collapsed(
        self, collapsed: bool, *, persist: bool = True, redecide_flip: bool = True
    ) -> None:
        """切换折叠态：True 只剩宠物区（卡片隐藏），False 恢复完整卡片。

        persist：用户操作时立即写盘；restore_position 回放时关闭。
        redecide_flip：用户展开后按当前位置重判朝向；restore 时不重判（位置还没落好）。

        折叠方向：窗口变窄，猫的位置必须严格保持 —— 窗口不可能出界，
        没有取舍余地。展开方向：窗口变宽，若按猫为锚点会把卡片顶出屏幕，
        此时允许猫左移（_keep_on_screen 保证卡片完整可见）。
        折叠态没有卡片可判定翻转，展开后必须按当前位置重新判一次朝向，
        否则用户在屏幕左半边折叠、展开后跨到右半边，朝向会停在错误位置。
        """
        if collapsed == self._collapsed:
            return
        # 锚点：翻转态下猫的屏幕左边界（猫原地不动、卡片换边语义的复用）。
        pet_x_before = self.x() + (self.width() - self.PET_AREA_WIDTH if self._flipped else 0)

        self._collapsed = collapsed
        # 折叠态的两个尺寸都比展开态小，**宽和高都必须临时放开**最小限制。
        #
        # 只放开宽度是不够的：实测展开态 QVBoxLayout 的最小尺寸是 459x268，
        # resize(240, 250) 时宽度能过（因为放了 setMinimumWidth），
        # 高度却被 268 顶回去 —— 现象就是「折叠态宽度对了、高度没变」，
        # 下方多出一条空白，猫看起来偏上。
        self.setMinimumWidth(0 if collapsed else theme.MIN_WIDTH)
        self.setMinimumHeight(0)

        self.layout().setContentsMargins(*self._layout_margins())
        # 文字是 QLabel 子控件，改布局和尺寸并不会让它们消失 —— 不显式隐藏，
        # 折叠后两列文字会原样压在猫身上（卡片背景没了，字还在）。
        self._set_rows_visible(not collapsed)
        self._fit_to_content()

        # 按锚点平移：offset_after 是猫在新朝向（实际未变）下窗口内的左边界。
        offset_after = self.width() - self.PET_AREA_WIDTH if self._flipped else 0
        self.move(pet_x_before - offset_after, self.y())
        self._keep_on_screen()
        self.update()

        if persist:
            self.save_position()
        if not collapsed:
            # 展开后文本可能有一阵子没真正重算（文本只在秒边界变），
            # 主动刷一次保证显示立即正确，而不是展开瞬间的旧值。
            self.refresh_texts()
            if redecide_flip:
                # 展开后卡片回来了，按当前位置重新判定朝向（可能跨中线翻面）。
                self._update_flip_from_screen(animate=True, persist=False)

    def _set_rows_visible(self, visible: bool) -> None:
        """显示 / 隐藏所有文字标签（折叠时用 hide，恢复时用 show）。

        为什么用 hide/show 而不是 deleteLater 或重建：
            重建会丢掉每秒刷新的引用、正在拖拽的鼠标事件也会断；
            用隐藏后恢复，文本内容与对象身份都保持不变。
        """
        # 「还有 N 项」不在 _rows 里，必须单独控制 —— 否则折叠态只剩猫和球时，
        # 旁边会突兀地飘着一行字，看起来像渲染残留。
        if self._more_label is not None:
            self._more_label.setVisible(visible)

        for ball_slot, name_label, suffix_label, time_label, _event in self._rows:
            # name_label 可能是 None（名称列已去掉，事件名写在球内）
            for widget in (ball_slot, name_label, suffix_label, time_label):
                if widget is None:
                    continue
                if visible:
                    widget.show()
                else:
                    widget.hide()

    def _apply_flipped(
        self,
        flipped: bool,
        *,
        animate: bool = False,
        persist: bool = False,
    ) -> None:
        """应用朝向：宠物区换边、布局内边距对调、猫镜像、必要时淡入。

        不重建任何控件 —— 只改几何与绘制参数。重建的话每秒刷新的文本会断，
        而且正在拖拽的鼠标事件会丢。
        """
        changed = flipped != self._flipped
        # 翻转语义是「猫原地不动、卡片换边」，不是「窗口原地镜像」——后者会让猫
        # 跳到窗口另一头，看起来像跑出屏幕。先记下翻转前猫的屏幕左边界。
        pet_x_before = self.x() + (self.width() - self.PET_AREA_WIDTH if self._flipped else 0)

        self._flipped = flipped

        self.layout().setContentsMargins(*self._layout_margins())
        # 猫也要镜像，否则翻转后它会背对卡片
        self._animator.set_flipped(flipped)

        self._fit_to_content()
        if changed:
            # 平移窗口让猫的屏幕 x 回到翻转前的位置：
            # offset_after 是猫在新朝向下窗口内的左边界（0 或 width-PET_AREA_WIDTH）。
            offset_after = self.width() - self.PET_AREA_WIDTH if self._flipped else 0
            self.move(pet_x_before - offset_after, self.y())
            self._keep_on_screen()
            self.update()  # 宠物区换边了，整窗重绘一次（不能只刷宠物区）
            if animate:
                self._start_flip_fade()
        if persist:
            self.save_position()

    def _start_flip_fade(self) -> None:
        """翻面瞬间做一次淡入，掩盖「跳一下」。"""
        self._flip_fade.stop()
        self.setWindowOpacity(self.FLIP_FADE_FROM)
        self._flip_fade.start()

    def _flip_for_current_position(self) -> bool:
        """按窗口当前所在的屏幕判定朝向（纯查询，无副作用）。

        用 `screenAt(窗口中心)` 而不是主屏：多屏时用户可能把窗口拖到副屏，
        判定必须基于那块屏的中线。
        """
        screen = (
            QGuiApplication.screenAt(self.frameGeometry().center())
            or QGuiApplication.primaryScreen()
        )
        if screen is None:
            return self._flipped
        # 折叠态没有卡片可换边，判定翻转没有意义；且窗口变窄会平移中线，
        # 可能在中线附近造成来回翻的状态振荡。折叠时保持当前朝向，
        # 展开后再按当前位置重新判定（见 set_collapsed）。
        if self._collapsed:
            return self._flipped
        area = screen.availableGeometry()
        center_x = self.x() + self.width() / 2.0
        return placement.should_flip(center_x, area.x(), area.width())

    def _update_flip_from_screen(self, *, animate: bool = True, persist: bool = True) -> None:
        """重新判定并应用朝向（拖拽结束 / 换屏 / 改分辨率时调用）。"""
        self._apply_flipped(
            self._flip_for_current_position(), animate=animate, persist=persist
        )

    def _on_screen_changed(self, _screen) -> None:  # noqa: N802 (Qt 信号槽)
        """窗口被拖到另一块屏幕 / 那块屏的分辨率变了：重新判定朝向。"""
        self._update_flip_from_screen(animate=False)
        self._hook_screen_geometry()

    def _hook_screen_geometry(self) -> None:
        """盯住当前屏幕的分辨率变化（改分辨率后中线位置会变）。"""
        screen = QGuiApplication.screenAt(self.frameGeometry().center())
        if screen is None:
            return
        key = id(screen)
        if key in self._hooked_screens:
            return
        self._hooked_screens.add(key)
        screen.availableGeometryChanged.connect(self._on_screen_changed)

    # ------------------------------------------------------------------
    # 宠物区几何
    # ------------------------------------------------------------------

    def _pet_box(self) -> QRect:
        """宠物区矩形（窗口内逻辑坐标）：贴**当前朝向那一侧**的顶部。

        为什么是顶部对齐而不是垂直居中：
            卡片只有一两行时窗口高度就等于宠物区，两种对齐看起来一样；
            但事件多到卡片比猫高时（比如 6 条），垂直居中会让猫悬在窗口中间，
            上下各留一大片空白，像「飘着」。顶部对齐则让猫始终待在左上（或右上）
            角，和卡片顶边齐平。

        为什么 x 跟着翻转：窗口总宽不变，只是内部镜像 ——
        猫在右时宠物区从 `width - PET_AREA_WIDTH` 开始。
        """
        # 折叠态：猫居中，四周留出球环绕的空间（窗口比宠物区大）。
        # 这不是"留白"而是"给球让位"——球环半径 70，猫必须离边够远才不越界。
        if self._collapsed:
            x = (self.width() - self.PET_AREA_WIDTH) // 2
            y = (self.height() - self.PET_AREA_HEIGHT) // 2
            return QRect(x, y, self.PET_AREA_WIDTH, self.PET_AREA_HEIGHT)

        x = self.width() - self.PET_AREA_WIDTH if self._flipped else 0
        return QRect(x, 0, self.PET_AREA_WIDTH, self.PET_AREA_HEIGHT)

    def pet_center_global(self) -> QPoint:
        """宠物区中心在屏幕上的坐标（歪头跟随用它算鼠标偏移）。"""
        return self.mapToGlobal(self._pet_box().center())

    def update_pet_area(self) -> None:
        """只重绘宠物区，不碰卡片和文字。

        30fps 下如果整窗重绘，卡片圆角 + 描边每秒要重画 30 次，纯属浪费；
        动画只影响猫所在的矩形。
        """
        self.update(self._pet_box())

    def set_pet_flipped(self, flipped: bool) -> None:
        """左右镜像宠物（卡片与文字不镜像）。

        本期没有 UI 入口，留给后续「点一下换朝向」之类的交互。
        """
        self._animator.set_flipped(flipped)
        self.update_pet_area()

    # ------------------------------------------------------------------
    # 内容
    # ------------------------------------------------------------------

    def visible_events(self) -> list[Event]:
        """悬浮窗要显示的事件：启用 且 显示在桌面。"""
        return [e for e in self._main_window.events if e.enabled and e.show_on_desktop]

    def first_snoozable_index(self) -> int:
        """返回第一个「已到点、启用、显示在桌面」的倒计时在主窗口 events 里的下标。

        悬浮窗没有「选中」概念，稍后提醒就作用于这个事件（首个到点的可见倒计时）。
        返回 -1 时，菜单对应项应置灰。

        :return: events 列表下标；找不到返回 -1
        """
        for index, event in enumerate(self._main_window.events):
            if (
                event.enabled
                and event.show_on_desktop
                and event.mode == EventMode.COUNTDOWN
                and is_expired(event)
            ):
                return index
        return -1

    @staticmethod
    def _display_name(event) -> str:
        """悬浮窗名称列文本：循环事件在名称前加「循环 」前缀。

        设计决定（追加指令最终版）：
          - 前缀用两个字「循环」+ 一个空格，不用 ↻ 等任何符号（零字形风险，
            不依赖字体是否包含 U+21BB），同字号同次要色。
          - 加在名称列，**不加在时间列**（时间列是右对齐主视觉，加符号会撑宽整窗）。
          - 卡片不显示「第 N 次」——避免窗口宽度在 1s 刷新里跳动。
          - 由于重建时文本就带上前缀，elidedText 从尾部截断时前缀永远保留。
        """
        if event.repeat_seconds is not None and event.repeat_seconds > 0:
            return f"循环 {event.name}"
        return event.name

    def _sorted_events(self) -> list[Event]:
        """按剩余时间升序，最紧急的在最前面。

        只在 rebuild() 调用，refresh_texts() 绝不重排：
        两个相差几秒的事件会每秒互换位置，看起来像列表在抽搐。
        排序是「数据变更」事件，不是「时间流逝」事件。

        截断到 MAX_ROWS 之后，被藏起来的必须是「最不紧急的」，
        否则用户会看不到快到期的那一个 —— 排序就是为此存在的。
        """
        now = time.time()
        return sorted(self.visible_events(), key=lambda e: compute_seconds(e, now))

    def rebuild(self) -> None:
        """重建行控件（事件增删改后调用），并自适应尺寸。

        只在数据变更时调用：这里的排序与 MAX_ROWS 截断会改变行数与行序，
        若跟着每秒心跳跑，列表会持续抽搐。
        """
        self._clear_rows()

        events = self._sorted_events()
        shown = events[: theme.MAX_ROWS]
        hidden = len(events) - len(shown)

        if not events:
            hint = self._make_label("还没有倒计时，右键新增", theme.TEXT_SECONDARY, theme.FONT_SIZE_HINT)
            self._grid.addWidget(hint, 0, 0, 1, 4, Qt.AlignmentFlag.AlignCenter)
        else:
            # 同屏配色互斥：纯按 id 哈希会撞色，这里按事件顺序消解冲突
            ball_indices = theme.distinct_ball_indices([e.id for e in shown])
            for row, event in enumerate(shown):
                ball_slot = QWidget(self)
                ball_slot.setFixedSize(theme.BALL_SIZE, theme.BALL_SIZE)
                ball_slot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                ball_slot.show()

                # 名称列已去掉：事件名写在球内（球 44px 内可容 2 个汉字）。
                # 保留一个 None 占位，让 _rows 仍是 5 元组 —— 4 处解包逻辑
                # 可以原样不动，减少「改一处漏一处」的风险。
                suffix_label = self._make_label(
                    repeat_suffix(event), theme.TEXT_SECONDARY, theme.FONT_SIZE_NAME
                )
                # 定宽：3/5 -> 10/10 多一位数字时，不让整窗跟着变宽再变窄
                suffix_label.setFixedWidth(theme.SUFFIX_WIDTH)
                suffix_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                time_label = self._make_label("", theme.TEXT_PRIMARY, theme.FONT_SIZE_TIME)

                self._grid.addWidget(ball_slot, row, 0, Qt.AlignmentFlag.AlignVCenter)
                self._grid.addWidget(
                    suffix_label, row, 1,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                )
                # 弹簧：吃掉剩余宽度，让后缀紧贴球、时间靠右
                self._grid.addItem(
                    QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum),
                    row, 2,
                )
                self._grid.addWidget(
                    time_label, row, 3,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                )

                self._rows.append((ball_slot, None, suffix_label, time_label, event))
                # 画笔颜色按 id 定，存到控件属性上供 paintEvent 取用
                ball_slot.setProperty("ballIndex", ball_indices[event.id])

        if hidden > 0:
            # 存到成员里：它不在 _rows 里，折叠态必须靠 _more_label 单独隐藏。
            self._more_label = self._make_label(
                f"还有 {hidden} 项", theme.TEXT_SECONDARY, theme.FONT_SIZE_HINT
            )
            # 从后缀列跨到弹簧列：左侧留白与毛线球对齐（不画球），右侧不占时间列
            self._grid.addWidget(
                self._more_label, len(shown), 1, 1, 2, Qt.AlignmentFlag.AlignLeft
            )
            self._more_label.show()

        # 折叠态下新增的事件标签默认是可见的 —— 重建后必须立刻按当前折叠态再隐藏一次，
        # 否则「折叠着添加一个事件」会让文字突然出现在猫身上。
        self._set_rows_visible(not self._collapsed)
        self.refresh_texts()
        self._fit_to_content()
        self._keep_on_screen()

    def refresh_texts(self, now: Optional[float] = None) -> None:
        """刷新全部可见事件文本；文本无变化则跳过（可重复调用）。

        now 由主窗口的心跳信号注入；为兼容 rebuild 里无参调用保留默认值。
        """
        now = now if now is not None else time.time()
        changed = False

        for _ball_slot, _name_label, _suffix_label, time_label, event in self._rows:
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
        if changed:
            before = self.size()
            self._fit_to_content()
            if self.size() != before:
                self._keep_on_screen()

    def _on_time_changed(self, now: float) -> None:
        """主窗口心跳信号的槽：立即刷新文本（隐藏时同样收到，保持更新）。"""
        self.refresh_texts(now)

    def _fit_to_content(self) -> None:
        """按内容自适应尺寸：宽度跟着卡片，高度不低于宠物区。

        为什么不用 adjustSize()：它会把高度压到 sizeHint 的高度（卡片高度），
        卡片只有一两行时窗口就比猫矮，猫会被窗口边缘切掉。
        """
        # 折叠态不跟随卡片内容：固定成「猫 + 球环绕」的尺寸。
        # 140x150 装不下半径 70 的球环；200x210 又太大 ——
        # 那样球离猫太远，看起来像「一圈卫星」而不是「散落在猫身上」。
        if self._collapsed:
            target_size = QSize(theme.COLLAPSED_WIDTH, theme.COLLAPSED_HEIGHT)
            if self.size() != target_size:
                self.resize(target_size)
            self._keep_on_screen()
            return
        hint = self.sizeHint()
        target_width = max(hint.width(), self.minimumWidth())
        target_height = max(hint.height(), self.PET_AREA_HEIGHT)
        if self.size() != QSize(target_width, target_height):
            self.resize(target_width, target_height)
        # resize 可能让窗口变宽伸出屏幕外，必须在尺寸变化后立刻夹回
        self._keep_on_screen()

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名风格)
        """先画卡片背景，再画猫。文字由 QLabel 子控件绘制在最上层。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # 缩放/旋转猫的时候必须开，否则 1.12 倍和 2.5° 会出锯齿
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        self._paint_card(painter)
        # 猫先、球后：球必须在猫的上层。
        # 折叠态下球是环绕猫散落的，难免有几个压在猫身上；
        # 若球画在猫之前就会被整个盖住 —— 那是把「进度弧」这个信息丢了，
        # 不是审美问题。反过来「球放在猫身上」只是观感选择。
        self._paint_pet(painter)
        self._paint_balls(painter)


    # ------------------------------------------------------------------
    # 毛线球
    # ------------------------------------------------------------------


    def _paint_ball_label(
        self, painter: QPainter, cx: float, cy: float, event: Event, color
    ) -> None:
        """在球体上写事件名前两个字。

        颜色用**描边色**（深色）而不是白色：球体填充是浅色（橘/粉/黄绿），
        深色字在上面对比最好；白色字放在浅橘上会糊。
        """
        font = theme.make_font(theme.BALL_LABEL_FONT)
        painter.setFont(font)
        painter.setPen(QPen(color))
        # 宽度给到球体直径（32px），高度 16px 居中 —— 与 BALL_CORE_RADIUS 对应
        box = QRectF(cx - theme.BALL_CORE_RADIUS, cy - 8, theme.BALL_CORE_RADIUS * 2, 16)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, theme.ball_label(event))

    def _paint_balls(self, painter: QPainter) -> None:
        """在每个行首的球占位上画毛线球。

        用占位控件的实际几何定位，而不是按行高硬算：
        字号/行距/DPI 变化时，控件位置是布局算出来的真实结果，
        硬编码行高会在换字体或换缩放比时错位。

        折叠态下卡片和行控件都隐藏了，占位几何等于 0 —— 此时改走环绕布局，
        球散落在猫的周围（见 _paint_collapsed_balls）。
        """
        if self._collapsed:
            self._paint_collapsed_balls(painter)
            return

        for ball_slot, name_label, _suffix, _time, event in self._rows:
            rect = ball_slot.geometry()
            if rect.width() <= 0 or rect.height() <= 0:
                continue
            index = ball_slot.property("ballIndex")
            # 水平用球占位的中心（列位置是对的），**垂直用名称标签的中心**：
            # 球占位 28px、文字行高约 20px，两者 AlignVCenter 时中心并不重合，
            # 用球自己的 center().y() 会让球整体浮在文字上方。
            # 取标签的中心则永远和文字基线对齐，字体/行距变化都能自动跟随。
            # 名称标签可能为 None（名称列已去掉），此时退回球占位自己的中线
            center_y = (
                name_label.geometry().center().y()
                if name_label is not None
                else rect.center().y()
            )
            self._paint_yarn_ball(painter, rect.center().x(), center_y, event, index)


    def _paint_collapsed_balls(self, painter: QPainter) -> None:
        """折叠态：球绕猫散落。

        为什么折叠态要换一套布局而不是沿用行首位置：
            折叠后卡片和文字全部隐藏，行首的球占位几何是 0，
            沿用展开态坐标会把球堆在窗口左上角，被猫整个盖住。

        绘制顺序上，_paint_balls 在 _paint_pet 之后调用，所以球始终在猫上层：
        即使某个球压在猫身上，也是「球放在猫身上」而不是「球被猫遮住」——
        后者会丢掉进度弧这个信息。
        """
        if not self._rows:
            return
        center = QPointF(self.width() / 2.0, self.height() / 2.0)
        positions = theme.collapsed_ball_positions(
            [event.id for _b, _n, _s, _t, event in self._rows],
            center,
            theme.COLLAPSED_BALL_RADIUS,
        )
        for (ball_slot, _name, _suffix, _time, event), pos in zip(self._rows, positions):
            index = ball_slot.property("ballIndex")
            self._paint_yarn_ball(painter, pos.x(), pos.y(), event, index)

    def _paint_yarn_ball(
        self, painter: QPainter, cx: float, cy: float, event: Event, index: object = None
    ) -> None:
        """画一个球：分段进度弧 + 球体 + 球内事件名。

        外圈只画**已完成段**，不画未完成的底槽：
            画底槽时用户要先排除浅灰、再数深色，多一步认知。
            只画已完成段，一眼就能数出「做了几次」。

        为什么逐段 drawArc 而不是 dasharray：
            dasharray 的圆头会在段首尾各延伸半个线宽，相邻段会粘连成一段。
            drawArc + RoundCap 的圆头只朝外，段与段之间留白干净。
        """
        center = QPointF(cx, cy)

        # 一次性事件：虚线空圆占位，只为和其他行左对齐，不表达进度
        if event.repeat_seconds is None or event.repeat_seconds <= 0:
            # 2px 线宽 + dash(3,3)：原来的 1px + 默认间隔在奶油白上淡到看不见，
            # 用户会以为「这几个事件没有球」。加粗后即使远看也能认出是个空环。
            pen = QPen(theme.BALL_IDLE_STROKE, 2.0, Qt.PenStyle.DashLine)
            pen.setDashPattern([3, 3])
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, theme.BALL_RING_RADIUS, theme.BALL_RING_RADIUS)
            self._paint_ball_label(painter, cx, cy, event, theme.BALL_IDLE_STROKE)
            return

        if isinstance(index, int) and 0 <= index < len(theme.BALL_PALETTE):
            fill, stroke = theme.BALL_PALETTE[index]
        else:
            fill, stroke = theme.ball_colors(event.id)

        r = theme.BALL_RING_RADIUS
        done = min(event.repeat_count or 0, event.repeat_limit or 0)
        total = event.repeat_limit or 0

        painter.setBrush(Qt.BrushStyle.NoBrush)
        if total > 0:
            # 先铺一圈淡底槽。0/5 时没有已完成段，只画底槽 ——
            # 否则用户会以为「这个事件没有进度」而不是「有进度但还没开始」。
            # 底槽颜色刻意比段浅得多（#EFE7DC），数段时深色一眼可辨，不被干扰。
            painter.setPen(QPen(theme.BALL_TRACK_LIGHT, theme.BALL_RING_WIDTH))
            painter.drawEllipse(center, r, r)

        if total > 0 and done > 0:
            # 间隙随段数自适应：段多时间隙必须缩小，否则整圈会被间隙吃满；
            # 至少 6 度保证小段数时也能看清分割。
            gap = max(6.0, 360.0 / total * 0.25)
            span = 360.0 / total - gap
            arc_rect = QRectF(cx - r, cy - r, r * 2, r * 2)
            painter.setPen(
                QPen(stroke, theme.BALL_RING_WIDTH, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            for i in range(done):
                start = 90.0 - i * (360.0 / total)   # 从 12 点顺时针
                painter.drawArc(arc_rect, int(start * 16), int(-span * 16))
        elif total <= 0:
            # 无限循环：整圈细环。用更细的线，避免与「已完成若干段」混淆。
            # 它不画底槽 —— 无限循环没有「进度」这个概念。
            painter.setPen(QPen(stroke, 1.2))
            painter.drawEllipse(center, r, r)

        # 球体本体
        painter.setPen(QPen(stroke, 1.0))
        painter.setBrush(fill)
        painter.drawEllipse(center, theme.BALL_CORE_RADIUS, theme.BALL_CORE_RADIUS)

        # 球内写事件名（最多 2 字），取代了原来的「球 + 右侧名称列」。
        # 球 44px 之后内部有 32px 宽，写两个字是清晰的；这样右侧名称列可以整列去掉，
        # 一行只剩「球 / 后缀 / 弹簧 / 时间」，横向空间更宽松。
        self._paint_ball_label(painter, cx, cy, event, stroke)

    def _paint_card(self, painter: QPainter) -> None:
        """卡片圆角背景：给宠物区让出位置，翻转时左右对调。"""
        if self._collapsed:
            return  # 折叠态不画卡片
        pet_side = self.PET_AREA_WIDTH + self.PET_GAP
        # 内缩半个像素，否则 1px 描边会被窗口边缘裁掉一半
        if self._flipped:
            # 猫在右：卡片从左边贴到「窗口宽 - 宠物区 - 间距」
            rect = QRectF(self.rect()).adjusted(0.5, 0.5, -pet_side - 0.5, -0.5)
        else:
            rect = QRectF(self.rect()).adjusted(pet_side + 0.5, 0.5, -0.5, -0.5)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        path = QPainterPath()
        path.addRoundedRect(rect, theme.CARD_RADIUS, theme.CARD_RADIUS)

        painter.fillPath(path, theme.CARD_BG)
        if theme.CARD_BORDER_WIDTH > 0:
            painter.setPen(QPen(theme.CARD_BORDER, theme.CARD_BORDER_WIDTH))
            painter.drawPath(path)

    def _paint_pet(self, painter: QPainter) -> None:
        """按当前姿势把猫画进宠物区（缩放 / 位移 / 旋转 / 镜像）。"""
        box = self._pet_box()
        pose = self._animator.pose()

        # 静止尺寸只占宠物区的 1/1.12，给点击放大留余量
        fit_width = max(1, int(round(box.width() * self.PET_FIT_SCALE)))
        fit_height = max(1, int(round(box.height() * self.PET_FIT_SCALE)))
        pixmap = pet_render.load_pet_pixmap(fit_width, fit_height, self.devicePixelRatioF())

        # pixmap 是按 dpr 预缩放的物理位图，这里换回逻辑尺寸参与布局计算
        dpr = pixmap.devicePixelRatio() or 1.0
        logical_width = pixmap.width() / dpr
        logical_height = pixmap.height() / dpr

        width = logical_width * pose.scale
        height = logical_height * pose.scale
        center_x = box.center().x() + pose.offset_x
        center_y = box.center().y() + pose.offset_y
        target = QRectF(center_x - width / 2.0, center_y - height / 2.0, width, height)

        painter.save()

        # 旋转轴放在头部中心，而不是图片中心（理由见 PET_PIVOT_* 的注释）
        pivot = QPointF(
            target.left() + target.width() * self.PET_PIVOT_X,
            target.top() + target.height() * self.PET_PIVOT_Y,
        )
        painter.translate(pivot)
        painter.rotate(pose.rotation)
        painter.translate(-pivot)

        if pose.flipped:
            # 只镜像猫：以宠物区中线为镜面，卡片和文字不受影响
            painter.translate(center_x, 0.0)
            painter.scale(-1.0, 1.0)
            painter.translate(-center_x, 0.0)

        # 目标矩形用逻辑坐标、源矩形用位图像素坐标 —— 这样 dpr 由 Qt 自己换算
        painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
        painter.restore()

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
        # 父窗口已经显示时，新建的子控件默认是**隐藏**状态，而 QLayout 会把隐藏
        # 控件排除在 sizeHint 之外 —— 不显式 show() 的话，刚增删事件那一刻
        # 布局会算出一个「零行」的尺寸（实测 grid sizeHint 变成 0×0），
        # 窗口要等到下一次心跳刷新才自己长回来，中间那一下文字是被裁掉的。
        label.show()

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
        # 必须置空：_more_label 虽然在网格里已被上面的循环清理，
        # 但成员引用还指向已 deleteLater 的对象。不置空的话，
        # 下次 _set_rows_visible 会去操作一个已销毁的控件（RuntimeError）。
        self._more_label = None

    # ------------------------------------------------------------------
    # 位置记忆
    # ------------------------------------------------------------------

    def _position_path(self) -> Path:
        """位置文件路径（与 config.json 同目录）。"""
        return get_config_path().with_name(self.POSITION_FILENAME)

    def save_position(self) -> None:
        """原子写位置与朝向到 JSON。

        朝向必须和坐标一起落盘：重启时直接读回，**不重新判定** ——
        否则窗口正好停在中线附近时，重启会翻到另一边，用户会以为「它自己乱跳」。
        """
        path = self._position_path()
        tmp = path.with_name(path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "x": self.x(),
                        "y": self.y(),
                        "flipped": self._flipped,
                        "collapsed": self._collapsed,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except OSError as exc:
            print(f"[floating] 警告：保存悬浮窗位置失败 -> {exc}")

    def restore_position(self) -> None:
        """读取位置与朝向；文件缺失/损坏/坐标失效时回落到默认位置。"""
        position: Optional[tuple[int, int]] = None
        flipped = False  # 旧版文件没有这个字段，按 False 处理（向后兼容）
        collapsed = False  # 旧版文件没有这个字段，按 False 处理（向后兼容）
        path = self._position_path()

        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                position = (int(data["x"]), int(data["y"]))
                flipped = bool(data.get("flipped", False))
                collapsed = bool(data.get("collapsed", False))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                # 内容坏了才隔离：位置文件很小，坏了多半是被手工改错，
                # 留着每次启动都报错。隔离后下次 save_position 会写一份干净的。
                print(f"[floating] 警告：悬浮窗位置读取失败，使用默认位置 -> {exc}")
                backup_corrupt_file(path)
                position = None
            except OSError as exc:
                # 打不开不等于坏，不能把用户文件搬走
                print(f"[floating] 警告：悬浮窗位置读取失败，使用默认位置 -> {exc}")
                position = None

        # 先落朝向再定位：_apply_flipped 会改布局内边距，必须在算窗口尺寸之前完成
        self._apply_flipped(flipped)
        # 折叠态也要恢复：先套折叠布局再定位（窗口会变窄）
        if collapsed:
            self.set_collapsed(True, persist=False, redecide_flip=False)

        if position is None or not self._is_position_on_screen(*position):
            self.move_to_default_position()
        else:
            self.move(*position)

        # 折叠态窗口比展开态大（180x190 vs 140x150），旧位置可能让它探出屏幕；
        # 展开态变宽时同理。这里夹一次，并把**结果**写回磁盘。
        #
        # 不能用「比较夹边前后有没有位移」来决定要不要落盘：
        # restore_position 在 __init__ 里已经被调过一次，第二次进来时窗口
        # 可能早就被夹好了 —— 位移发生在上一次调用里，这一次比较出来是「没位移」，
        # 于是永远不落盘，每次启动都重复一次「先出屏再跳进来」的闪现。
        # 判据应该是「磁盘上的坐标是不是当前真实位置」，与本次是否移动无关。
        self._keep_on_screen()
        saved = self._read_saved_position()
        if saved != (self.x(), self.y()):
            self.save_position()

    def _read_saved_position(self) -> Optional[tuple[int, int]]:
        """读磁盘上记录的坐标；文件缺失/损坏时返回 None。"""
        path = self._position_path()
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return (int(data["x"]), int(data["y"]))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError):
            return None

    def _is_position_on_screen(self, x: int, y: int) -> bool:
        """坐标是否与任一有效屏幕有交集。

        用户拔掉副显示器后，记忆的坐标可能落在不存在的区域 —— 必须校验，
        否则窗口会「存在但看不见」，用户以为程序坏了。
        """
        rect = QRect(x, y, self.width(), self.height())
        return any(screen.availableGeometry().intersects(rect) for screen in QGuiApplication.screens())

    def move_to_default_position(self) -> None:
        """默认位置：主屏右下角，留 SCREEN_MARGIN 边距。

        落到右下角之后顺手定一次朝向（不落盘）：首次启动时没有「上次朝向」
        可继承，而右下角意味着卡片该向左展开，否则会被屏幕右边缘裁掉。
        这条只在这里做，恢复已有位置时**不重新判定**。
        """
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.x() + area.width() - self.width() - self.SCREEN_MARGIN
        y = area.y() + area.height() - self.height() - self.SCREEN_MARGIN
        # 窗口比屏幕还大时（极端情况）夹回左上角，保证可见
        self.move(max(x, area.x()), max(y, area.y()))
        self._apply_flipped(self._flip_for_current_position())

    def _keep_on_screen(self) -> None:
        """把窗口夹回屏幕内，防止自适应变宽后跑出可视区。"""
        screen = QGuiApplication.screenAt(self.frameGeometry().center()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        # 先夹下界、再夹上界：直接在同一行 min/max 里写反会让窗口比屏幕宽时
        # 被 min() 拉到屏幕外的负坐标。max(下界, min(当前, 上界)) 才是正确顺序。
        x = max(area.x(), min(self.x(), area.x() + area.width() - self.width()))
        y = max(area.y(), min(self.y(), area.y() + area.height() - self.height()))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _clamp_drag_position(self, x: int, y: int) -> tuple[int, int]:
        """把拖拽目标夹到「猫整体可见」的最小范围。

        为什么不用 _keep_on_screen()：它夹的是**整个窗口**，拖到屏幕边缘时
        会把卡片一起硬拉回屏幕内，拖拽手感会黏滞。这里只保证猫所在的
        140×150 宠物区与屏幕完全相交，卡片可暂时出界。
        """
        screen = QGuiApplication.screenAt(QPoint(x, y)) or QGuiApplication.primaryScreen()
        if screen is None:
            return x, y
        box = self._pet_box()
        area = screen.availableGeometry()
        # 宠物区屏幕坐标 = 窗口坐标 + box 偏移，约束它完全落在 area 内：
        #   area.x() - box.x() <= x <= area.x() + area.width() - box.x() - box.width()
        min_x = area.x() - box.x()
        max_x = area.x() + area.width() - box.x() - box.width()
        min_y = area.y()
        max_y = area.y() + area.height() - box.height()
        return max(min_x, min(x, max_x)), max(min_y, min(y, max_y))

    # ------------------------------------------------------------------
    # 鼠标交互
    # ------------------------------------------------------------------

    def _ensure_click_timer(self) -> QTimer:
        """惰性创建单击延迟定时器（QTimer singleton）。

        窗口只在点猫折叠时用一次：用后即弃会让对象失去持有者而被回收，
        QTimer 单发不触发时回调仍会在对象销毁后被 Qt 调。归根到底还是
        稳定的长持有 + 单发。250ms 内触发/取消都是迟到事件，定时器不
        归零，下一轮只是再 start 一次（start 会重启计时）。
        """
        if self._click_timer is None:
            self._click_timer = QTimer(self)
            self._click_timer.setSingleShot(True)
            self._click_timer.timeout.connect(self._on_click_timeout)
        return self._click_timer

    def _on_click_timeout(self) -> None:
        """CLICK_PENDING_MS 内没有第二次点击 → 执行折叠/展开。"""
        self._dragging_candidate = False
        self._press_global = None
        self.toggle_collapsed()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """左键按下：记录拖动起点与按下时刻，并触发一次「被戳了一下」的缩放反馈。

        轻点/拖动的判定完全后置：按下只记状态，不预判。拖动在 mouseMoveEvent
        里判定（位移超阈值 → 停掉待折叠定时器、标记为拖动）；单不单点由
        mouseReleaseEvent 在松手时按位移 + 时长定夺。这样点和拖永远不会打架，
        也不会有「点一下却被当成拖动」的错觉。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self._animator.trigger_click()
            self._press_time = time.monotonic()
            self._press_global = event.globalPosition().toPoint()
            self._dragging_candidate = False
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            if self._click_timer is not None:
                self._click_timer.stop()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        """左键拖动：按偏移移动窗口；一旦位移超阈值立即停掉待折叠定时器。

        拖动判定只认位移（不认时长）：位移超过 CLICK_MAX_DRIFT_PX 即视为拖动，
        `_dragging_candidate` 置真，松手就只保存位置不折叠。这避免「按住不放
        超过 300ms」这类边界把一次轻点误判成拖动 —— 轻点判定仍由松手时刻的
        时长 + 位移共同决定。
        """
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if not self._dragging_candidate:
                if self._press_global is not None:
                    delta = (event.globalPosition().toPoint() - self._press_global).manhattanLength()
                    if delta > self.CLICK_MAX_DRIFT_PX:
                        self._dragging_candidate = True
                        if self._click_timer is not None:
                            self._click_timer.stop()
            target = event.globalPosition().toPoint() - self._drag_offset
            self.move(*self._clamp_drag_position(target.x(), target.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        """松开左键：结束拖动 / 判定单击。

        拖动与单击的判定：
          - 位移 > CLICK_MAX_DRIFT_PX 或按住时长 > CLICK_MAX_DURATION_MS → 拖动：
            立即保存位置、按落点重判朝向（与旧行为一致）。
          - 否则是一下轻点：不立即折叠，而是启动 250ms 的 CLICK_PENDING_MS 定时器，
            窗口里等第二次点击 —— 250ms 内没来第二下才真正折叠；第二下来则取消
            折叠，由 mouseDoubleClickEvent 打开管理窗口。这样单击延迟是刻意为之，
            双击不会「折叠再展开闪一下」。

        朝向**只在松手时判定一次**，拖拽过程中不判（见 mouseMoveEvent 注释）。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            held = time.monotonic() - self._press_time
            drifted = self._dragging_candidate or (
                self._press_global is not None
                and (event.globalPosition().toPoint() - self._press_global).manhattanLength() > self.CLICK_MAX_DRIFT_PX
            )
            if self._drag_offset is not None and not (held < self.CLICK_MAX_DURATION_MS and not drifted):
                # 拖动：保位置、重判朝向
                self._drag_offset = None
                self._press_global = None
                self._dragging_candidate = False
                self._update_flip_from_screen()  # 内含 save_position()
                event.accept()
                return
            # 轻点：进入单击延迟窗口
            self._drag_offset = None
            self._dragging_candidate = False
            self._ensure_click_timer().start(self.CLICK_PENDING_MS)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        """双击：取消待折叠定时器，打开管理窗口。

        双击的第一击已让单击延迟定时器进入 250ms 等待；第二击到来时直接停掉
        定时器（取消折叠），并打开管理窗口 —— 不再有「折叠再展开闪一下」。
        """
        if event.button() == Qt.MouseButton.LeftButton:
            if self._click_timer is not None:
                self._click_timer.stop()
            self._drag_offset = None  # 双击不是拖动，清掉偏移
            self._press_global = None
            self._dragging_candidate = False
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

    def showEvent(self, event) -> None:  # noqa: N802
        """显示：立刻刷一次文本，启动动画节拍，并挂上「换屏」监听。

        动画必须跟着可见性开关：33ms 一跳的定时器如果隐藏后还在跑，
        就是纯粹的 CPU 浪费（Qt 仍会每帧调 update，虽然不可见时不重绘）。

        换屏监听只能在显示之后挂：windowHandle() 要等窗口真正创建才有值。
        """
        super().showEvent(event)
        self.refresh_texts()
        self._animator.start()

        handle = self.windowHandle()
        if handle is not None and not self._screen_hooked:
            handle.screenChanged.connect(self._on_screen_changed)
            self._screen_hooked = True
        self._hook_screen_geometry()

    def hideEvent(self, event) -> None:  # noqa: N802
        """隐藏：停掉动画节拍（硬性性能要求，别漏），并把透明度复位。

        复位是必须的：如果恰好在淡入过程中被隐藏，窗口会带着半透明状态
        留在下一次显示里，看起来像「掉帧」。
        """
        self._animator.stop()
        self._flip_fade.stop()
        self.setWindowOpacity(1.0)
        super().hideEvent(event)

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
