"""悬浮窗几何约束：屏幕内钳制、翻转猫锚点、拖拽猫可见。

覆盖四类真实 bug 的回归断言（需要 PySide6 + offscreen）：
  A) _keep_on_screen 窗口比屏幕宽时把 x 夹回 area.x()，而不是拉到负坐标
  B) _fit_to_content resize 后窗口仍在屏幕内
  C) 翻转前后猫的屏幕 x 坐标不变（翻转是「猫原地、卡片换边」）
  D) 拖拽约束把位置夹到「猫完整可见」，但允许卡片出界

运行方式（在项目根目录执行）：
    export QT_QPA_PLATFORM=offscreen
    export APPDATA=$(mktemp -d)
    .venv/Scripts/python.exe -m unittest tests.test_floating_geometry -v
"""


from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import os
import tempfile
import time
import unittest
from unittest import mock

# 必须在 QApplication 创建之前设置离屏平台
if not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.models import DisplayFormat, Event, EventMode
from ui.floating_window import FloatingWindow
from ui.main_window import MainWindow


def _make_running_countdown() -> Event:
    """一个 10 秒后到点的倒计时：让悬浮窗有可见内容、卡片有真实宽度。"""
    return Event(
        name="待刷新",
        mode=EventMode.COUNTDOWN,
        anchor_ts=time.time() + 10,
        display_format=DisplayFormat.FULL,
        enabled=True,
        show_on_desktop=True,
    )


# 构造一个左键鼠标事件（position 与 globalPosition 都用同一个点，够用即可）
def _mouse_event(etype, pos: QPoint) -> QMouseEvent:
    return QMouseEvent(
        etype,
        QPointF(pos.x(), pos.y()),
        QPointF(pos.x(), pos.y()),
        QPointF(pos.x(), pos.y()),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


_DOWN = QEvent.Type.MouseButtonPress
_MOVE = QEvent.Type.MouseMove
_UP = QEvent.Type.MouseButtonRelease
_DBL = QEvent.Type.MouseButtonDblClick




class RestorePositionMigrationTest(unittest.TestCase):
    """位置迁移：折叠态变大后，旧位置必须夹回屏幕并落盘。

    为什么要单独测这条：
        夹回屏幕本身很简单，但「夹完要不要落盘」是个容易漏的细节。
        不落盘的话用户只要不拖拽，每次启动都要重复一次「先出屏、再跳进来」
        的闪现 —— 而只在单次 restore 里断言位置正确，是发现不了这个问题的，
        必须**跑两次 restore**：第一次夹并落盘，第二次应该原地不动。
    """

    _app = None

    @classmethod
    def setUpClass(cls):
        import os

        if not os.environ.get("QT_QPA_PLATFORM"):
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is None:
            cls._app = QApplication([])

    def setUp(self):
        import tempfile
        import os

        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self._tmp.name

    def tearDown(self):
        import os

        if self._old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old
        self._tmp.cleanup()

    def test_collapsed_restore_clamps_and_persists_once(self):
        """旧 json（120x120 位置的折叠态）启动 -> 夹回并落盘 -> 第二次启动不再位移。"""
        import json

        from PySide6.QtGui import QGuiApplication

        from core.storage import get_config_path
        from ui.floating_window import FloatingWindow
        from ui.main_window import MainWindow

        area = QGuiApplication.primaryScreen().availableGeometry()
        # 模拟「老版本留下的位置」：screen 右下角往外 200px，折叠态窗口放不下
        old_x = area.x() + area.width() + 50
        old_y = area.y() + area.height() - 120

        path = get_config_path().with_name(FloatingWindow.POSITION_FILENAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"x": old_x, "y": old_y, "flipped": False, "collapsed": True}),
            encoding="utf-8",
        )

        main = MainWindow([])
        floating = FloatingWindow(main)
        main.floating_window = floating
        floating.restore_position()

        # 1) 窗口完整落在屏幕内
        self.assertLessEqual(
            floating.x() + floating.width(), area.x() + area.width(),
            "第一次 restore 后右边缘仍在屏幕外",
        )
        self.assertGreaterEqual(floating.x(), area.x(), "第一次 restore 后左边缘在屏幕外")

        # 2) 夹回的位置已经落盘
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((saved["x"], saved["y"]), (floating.x(), floating.y()),
                         "夹回后的位置没有落盘 —— 下次启动会重复出屏闪现")

        # 3) 第二次启动：读回已落盘的位置，不应再发生位移
        main2 = MainWindow([])
        floating2 = FloatingWindow(main2)
        main2.floating_window = floating2
        # 先把 _keep_on_screen 的实际效果记录下来，判断是否有位移
        floating2.restore_position()
        self.assertEqual(
            (floating2.x(), floating2.y()), (floating.x(), floating.y()),
            "第二次启动位置与第一次不同 —— 说明第一次的夹回没被保存",
        )




class MoreLabelCollapsedTest(unittest.TestCase):
    """「还有 N 项」必须在折叠态隐藏。

    这个标签不在 _rows 里（它不是事件行），所以遍历 _rows 的可见性控制管不到它。
    曾经的真实 bug：折叠后只剩猫和球，旁边却飘着「还有 3 项」，像渲染残留。
    """

    _app = None

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is None:
            cls._app = QApplication([])

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old
        self._tmp.cleanup()

    def _window_with_overflow(self):
        import time as _t

        from core.models import DisplayFormat, Event, EventMode
        from ui import theme
        from ui.floating_window import FloatingWindow
        from ui.main_window import MainWindow

        total = theme.MAX_ROWS + 3
        events = [
            Event(name=f"事件{i}", mode=EventMode.COUNTDOWN,
                  anchor_ts=_t.time() + (30 + i * 5) * 60,
                  display_format=DisplayFormat.SMART)
            for i in range(total)
        ]
        main = MainWindow(events)
        floating = FloatingWindow(main)
        main.floating_window = floating
        floating.rebuild()
        return floating


    def test_collapsed_size_is_exact_and_stable_over_time(self):
        """折叠态尺寸必须严格等于 COLLAPSED_*，且不会随时间被撑回去。

        两个曾经踩过的坑都在这条里：
          1) 只放开最小宽度、忘了高度 —— 宽度对了、高度卡在展开态的值；
          2) 隐藏子控件后没 invalidate 布局 —— Qt 缓存的最小尺寸仍报旧值，
             把 resize 高度顶回去，下方多出一条空白。
        只量「创建瞬间」测不出第 2 种：尺寸是之后才被撑回去的。
        """
        from PySide6.QtTest import QTest

        from ui import theme

        floating = self._window_with_overflow()
        floating.show()
        floating.set_collapsed(True, persist=False)

        # 创建瞬间
        self.assertEqual(
            (floating.width(), floating.height()),
            (theme.COLLAPSED_WIDTH, theme.COLLAPSED_HEIGHT),
            "折叠瞬间尺寸不等于 COLLAPSED_*",
        )
        # 1 秒后（一个心跳周期，refresh_texts 可能触发 adjustSize）
        QTest.qWait(1100)
        self.assertEqual(
            (floating.width(), floating.height()),
            (theme.COLLAPSED_WIDTH, theme.COLLAPSED_HEIGHT),
            "折叠 1 秒后尺寸被撑回去了",
        )
        # 再等 2 秒，确认稳定
        QTest.qWait(2000)
        self.assertEqual(
            (floating.width(), floating.height()),
            (theme.COLLAPSED_WIDTH, theme.COLLAPSED_HEIGHT),
            "折叠 3 秒后尺寸被撑回去了",
        )

    def test_pet_is_vertically_centered_when_collapsed(self):
        """折叠态下猫必须在窗口内垂直居中（下方不该有空白条）。"""
        floating = self._window_with_overflow()
        floating.set_collapsed(True, persist=False)
        box = floating._pet_box()
        top_gap = box.y()
        bottom_gap = floating.height() - box.y() - box.height()
        self.assertLessEqual(
            abs(top_gap - bottom_gap), 2,
            f"猫未垂直居中：上留白 {top_gap} vs 下留白 {bottom_gap}",
        )

    def test_more_label_exists_when_overflow(self):
        """事件超限时必须创建 _more_label（否则无从隐藏）。"""
        floating = self._window_with_overflow()
        self.assertIsNotNone(floating._more_label, "超限时应创建「还有 N 项」标签")
        self.assertIn("还有", floating._more_label.text())

    def test_more_label_hidden_when_collapsed(self):
        """折叠态下「还有 N 项」必须不可见。"""
        floating = self._window_with_overflow()
        # 用 isHidden() 而不是 isVisible()：窗口本身没有 show()，
        # isVisible() 会因父窗口未显示而恒为 False，测不出标签自身的状态。
        self.assertFalse(floating._more_label.isHidden(), "展开态标签应处于显示状态")
        floating.set_collapsed(True, persist=False)
        self.assertTrue(
            floating._more_label.isHidden(),
            "折叠态下「还有 N 项」仍然可见 —— 只剩猫和球的界面上不该有文字",
        )

    def test_more_label_restored_when_expanded(self):
        """再展开必须恢复显示（不能永久藏起来）。"""
        floating = self._window_with_overflow()
        floating.set_collapsed(True, persist=False)
        floating.set_collapsed(False, persist=False)
        self.assertFalse(floating._more_label.isHidden(), "展开后「还有 N 项」没有恢复")

    def test_rebuild_does_not_stack_stale_more_labels(self):
        """反复 rebuild 不能叠加残留标签（_clear_rows 必须清引用）。"""
        floating = self._window_with_overflow()
        first = floating._more_label
        floating.rebuild()
        second = floating._more_label
        self.assertIsNot(first, second, "rebuild 应创建新的 _more_label")
        # 网格里的 QLabel 数量不能因 rebuild 而增长
        labels = [
            floating._grid.itemAt(i).widget()
            for i in range(floating._grid.count())
            if floating._grid.itemAt(i) and floating._grid.itemAt(i).widget()
        ]
        more_like = [w for w in labels if hasattr(w, "text") and "还有" in (w.text() or "")]
        self.assertEqual(len(more_like), 1, f"「还有」标签出现了 {len(more_like)} 个，说明有残留")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class _FakeScreen:
    """模拟 QScreen：只提供几何查询，避免依赖真实多屏。"""

    def __init__(self, rect: QRect):
        self._rect = rect

    def availableGeometry(self) -> QRect:
        return self._rect

    def geometry(self) -> QRect:
        return self._rect


class _EnvPatch:
    """临时把 APPDATA 指到测试目录（不写真实用户目录）。"""

    def __init__(self):
        self._old = None

    def start(self, root: str):
        self._old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = root

    def restore(self):
        if self._old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old


class FloatingGeometryTest(unittest.TestCase):
    """GUI 层几何约束：窗口 / 猫与屏幕可用区的关系。"""

    _app = None
    _env_patch = None
    _tmp = None

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._env_patch = _EnvPatch()
        cls._env_patch.start(cls._tmp.name)
        app = QApplication.instance()
        if app is None:
            cls._app = QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls._env_patch.restore()
        cls._tmp.cleanup()

    def setUp(self):
        # 默认给一个足够大的可用区，让构造/默认定位不会触发钳制干扰后续断言
        self._area = QRect(0, 0, 2000, 2000)
        self._fake = _FakeScreen(self._area)
        # 静态方法用 mock 替换：screenAt 与 primaryScreen 都返回假屏幕
        self._screen_patchers = [
            mock.patch.object(QGuiApplication, "screenAt", lambda p: self._fake),
            mock.patch.object(QGuiApplication, "primaryScreen", lambda: self._fake),
        ]
        for p in self._screen_patchers:
            p.start()

        self.main = MainWindow(events=[_make_running_countdown()])
        self.floating = FloatingWindow(self.main)
        self.main.floating_window = self.floating
        # 注意：不调用 self.floating.show()——show() 的 showEvent 会 hook 屏幕信号，
        # 需要假屏幕实现 availableGeometryChanged；这些用例只测几何方法，无需显示窗口。

    def tearDown(self):
        self.main.hide()
        self.floating.close()
        self.floating = None
        self.main = None
        for p in self._screen_patchers:
            p.stop()

    def _set_area(self, x: int, y: int, w: int, h: int) -> None:
        self._area.setRect(x, y, w, h)

    # --- A) _keep_on_screen 窗口比屏幕宽 -------------------------------------------------

    def test_keep_on_screen_clamps_wider_than_screen_to_left_edge(self):
        """窗口比屏幕宽时，x 必须夹到 area.x()，不能是负数。"""
        self._set_area(0, 0, 400, 300)
        self.assertGreater(self.floating.width(), 400, "用例前提：窗口应比假屏幕宽")
        # 从屏幕内出发
        self.floating.move(250, 100)
        self.floating._keep_on_screen()
        self.assertEqual(self.floating.x(), 0, "窗口比屏幕宽时应贴左，而不是被 min() 拉到负坐标")
        self.assertEqual(self.floating.y(), 100)

    def test_keep_on_screen_clamps_offscreen_right_edge(self):
        """窗口伸出右/下边界时被夹回，但不越过左/上边界。"""
        self._set_area(0, 0, 400, 300)
        self.floating.resize(min(self.floating.width(), 200), 150)
        w = self.floating.width()
        h = self.floating.height()
        # 真实 bug 场景：位置合法但伸出右/下边界
        self.floating.move(400, 300)
        self.floating._keep_on_screen()
        self.assertEqual(self.floating.x(), 400 - w)
        self.assertEqual(self.floating.y(), 300 - h)
        # 翻转的负坐标屏幕也不能把窗口拖出左/上边界
        self.floating.move(-50, -50)
        self.floating._keep_on_screen()
        self.assertEqual(self.floating.x(), 0)
        self.assertEqual(self.floating.y(), 0)

    # --- B) _fit_to_content 会夹回屏幕 ---------------------------------------------------

    def test_fit_to_content_keeps_window_on_screen_after_growth(self):
        """_fit_to_content resize 变大后必须调用 _keep_on_screen，窗口不能伸出屏幕。"""
        # 可用区要够大，让卡片自然宽度能完全放下（否则"窗口比屏宽"是另一码事，
        # 由 test_keep_on_screen_clamps_wider_than_screen 单独覆盖）
        # 可用区必须比卡片的自然宽度更宽，否则验的是「窗口比屏宽」那条路径
        # （由 test_keep_on_screen_clamps_wider_than_screen 单独覆盖）。
        # 卡片从 3 列变成 4 列（多了毛线球与后缀）之后自然宽度明显变大，
        # 原来是 600，现在要放宽，否则本用例的前提不成立。
        self._set_area(0, 0, 900, 500)
        # 先压窄 + 放到右/下边缘外，_fit_to_content 撑宽后再夹回
        self.floating.resize(120, 150)
        self.floating.move(880, 450)
        self.floating._fit_to_content()
        box = self.floating.frameGeometry()
        self.assertEqual(box.width(), self.floating.width())
        self.assertGreaterEqual(box.x(), 0, "左边缘不应伸出屏幕")
        self.assertGreaterEqual(box.y(), 0, "上边缘不应伸出屏幕")
        self.assertLessEqual(box.x() + box.width(), 900, "撑宽后右边缘不应伸出屏幕")
        self.assertLessEqual(box.y() + box.height(), 500, "下边缘不应伸出屏幕")

    # --- C) 翻转猫锚点 -------------------------------------------------------------------

    def test_flip_keeps_pet_screen_x_unchanged(self):
        """翻转前后猫的屏幕 x 坐标必须一致（翻转 = 猫原地、卡片换边）。"""
        self.floating.move(500, 300)
        before_box = self.floating._pet_box()
        pet_x_before = self.floating.x() + before_box.x()
        before_flipped = self.floating.is_flipped()

        self.floating._apply_flipped(not before_flipped, persist=False)

        after_box = self.floating._pet_box()
        pet_x_after = self.floating.x() + after_box.x()
        self.assertEqual(
            pet_x_after, pet_x_before,
            f"翻转后猫的屏幕 x 从 {pet_x_before} 变成 {pet_x_after}，猫没有原地不动",
        )

    def test_flip_back_keeps_pet_screen_x_unchanged(self):
        """再翻回来，猫的屏幕 x 也应回到原位。"""
        self.floating.move(500, 300)
        self.floating._apply_flipped(True, persist=False)
        start_box = self.floating._pet_box()
        pet_x_before = self.floating.x() + start_box.x()

        self.floating._apply_flipped(False, persist=False)

        after_box = self.floating._pet_box()
        self.assertEqual(
            self.floating.x() + after_box.x(), pet_x_before,
            "从翻转态翻回非翻转态后猫的屏幕 x 发生了变化",
        )

    # --- E) 折叠：猫锚点不变、箭头热区、翻转判定 ----------------------------------------

    def test_collapse_keeps_pet_screen_x_centered(self):
        """折叠后猫必须落在窗口水平中心（球环绕需要四周等距留白）。

        语义变更：折叠态以前是「只剩猫、窗口收缩到宠物区」，现在是
        「猫居中 + 球绕猫散落」，窗口放大到 180x190。
        所以不能再断言「猫的屏幕 x 不变」——那是旧规格。
        """
        self.floating._apply_flipped(True, persist=False)
        self.floating.move(500, 300)

        self.floating.set_collapsed(True, persist=False)

        self.assertTrue(self.floating.is_collapsed())
        box = self.floating._pet_box()
        left_gap = box.x()
        right_gap = self.floating.width() - box.x() - box.width()
        self.assertLessEqual(
            abs(left_gap - right_gap), 2,
            f"猫未居中：左留白 {left_gap} vs 右留白 {right_gap}（球环绕需要等距）",
        )

    def test_expand_keeps_card_fully_visible_not_pet_x(self):
        """展开方向断言的是「卡片完整可见」，**不是**「猫 x 不变」。

        为什么两个方向语义不同（这是刻意的不对称）：
            折叠 = 窗口变窄，不可能出界，猫原地不动是硬要求；
            展开 = 窗口变宽，若坚持猫原地不动，卡片会被顶出屏幕 ——
            用户点开却看不见卡片，功能就白做。此时必须让猫让位。
        所以这里断言「窗口右边界不超出屏幕」，而不是猫的 x 相等。
        用屏幕中间的宽松位置测「猫 x 不变」会掩盖这条取舍的真实行为。
        """
        area = self._area
        self.floating._apply_flipped(False, persist=False)
        # 贴近屏幕右缘：展开后若不移动猫，卡片必然越界
        self.floating.set_collapsed(True, persist=False)
        self.floating.move(area.x() + area.width() - self.floating.width(), area.y() + 50)
        self.floating._keep_on_screen()

        self.floating.set_collapsed(False, persist=False, redecide_flip=False)

        self.assertFalse(self.floating.is_collapsed())
        right_edge = self.floating.x() + self.floating.width()
        self.assertLessEqual(
            right_edge, area.x() + area.width(),
            f"展开后卡片右边界 {right_edge} 超出屏幕右边界 {area.x() + area.width()}",
        )
        self.assertGreaterEqual(
            self.floating.x(), area.x(), "展开后窗口左边界不应被推到屏幕外"
        )

    def test_collapse_hides_all_text_labels(self):
        """折叠后所有文字标签必须隐藏 —— 否则字会压在猫身上。

        曾经的缺陷：折叠只改了窗口宽度和卡片背景绘制，QLabel 子控件照旧可见，
        结果两列文字浮在猫身上，看起来像坏掉的半成品。
        """
        self.floating.rebuild()
        self.assertFalse(self.floating.is_collapsed())
        self.floating.set_collapsed(True, persist=False)
        for _ball, _name_label, suffix_label, time_label, _event in self.floating._rows:
            # name_label 恒为 None（名称列已去掉，事件名写在球内），跳过它
            self.assertFalse(suffix_label.isVisible(), "折叠后后缀标签仍然可见")
            self.assertFalse(time_label.isVisible(), "折叠后时间标签仍然可见")

        self.floating.set_collapsed(False, persist=False)
        for _ball, _name_label, suffix_label, time_label, _event in self.floating._rows:
            self.assertFalse(suffix_label.isHidden(), "展开后后缀标签没有恢复显示")
            self.assertFalse(time_label.isHidden(), "展开后时间标签没有恢复显示")

    def test_collapsed_window_size_fits_ball_ring(self):
        """折叠态窗口必须装得下球环（180x190）。

        语义变更：旧规格是「收缩到宠物区 140x150」，但球环半径 70 会越界，
        球被裁掉。现在窗口放大以容纳环绕布局。
        """
        from ui import theme

        self.floating.rebuild()
        self.floating.set_collapsed(True, persist=False)
        self.assertEqual(self.floating.width(), theme.COLLAPSED_WIDTH)
        self.assertEqual(self.floating.height(), theme.COLLAPSED_HEIGHT)
        self.assertGreater(
            self.floating.width(), FloatingWindow.PET_AREA_WIDTH,
            "折叠态窗口应比宠物区宽（给球环绕留白）",
        )

    def test_keep_on_screen_when_window_wider_than_screen(self):
        """窗口比屏幕还宽时退化为贴左边界，不能算出负坐标把窗口推出屏幕外。

        _keep_on_screen 的 min/max 顺序曾写反过一次；折叠/展开又引入了一个
        宽度变化的入口，这条防止同一类 bug 复发。
        """
        self._set_area(0, 0, 200, 200)
        self.floating.rebuild()
        self.floating.set_collapsed(False, persist=False)
        self.floating.resize(400, 150)   # 比 200 宽的屏幕还宽
        self.floating.move(-50, 10)
        self.floating._keep_on_screen()
        self.assertEqual(self.floating.x(), 0, "窗口比屏幕宽时 x 应退化为屏幕左边界 0")

    def test_drag_preserves_position_and_no_collapse(self):
        """按住猫拖走（位移超阈值）→ 不折叠，且位置已保存。

        原折叠三角有「热区按下不拖动、松手切换折叠」的语义；改成点猫折叠后，
        拖动与单击必须分清：位移 > CLICK_MAX_DRIFT_PX 就是拖动，松手只走
        save_position，绝不触发折叠。这是冲突 1（拖动 vs 单击）的回归防线。
        """
        self.floating.rebuild()
        before = self.floating.frameGeometry().topLeft()
        self.assertFalse(self.floating.is_collapsed())
        # 模拟从猫身上按下、拖出 20px、再松手
        press_pos = QPoint(20, 20)
        release_pos = QPoint(40, 20)
        self.floating.mousePressEvent(_mouse_event(_DOWN, press_pos))
        self.floating.mouseMoveEvent(_mouse_event(_MOVE, release_pos))
        self.floating.mouseReleaseEvent(_mouse_event(_UP, release_pos))
        # 拖动后的窗口位置：松手前已按 offset 移动过，save_position 已写入
        after = self.floating.frameGeometry().topLeft()
        self.assertEqual(after, before + QPoint(20, 0), "拖动后窗口应随手指移动")
        self.assertFalse(self.floating.is_collapsed(), "拖动松手不应触发折叠")

    def test_flip_for_current_position_skips_collapsed(self):
        """折叠态 _flip_for_current_position 返回当前朝向，不重判（防中线振荡）。"""
        self.floating._apply_flipped(False, persist=False)
        self.floating.set_collapsed(True, persist=False)
        # 把窗口放到屏幕中线右侧：若折叠态还判定，会翻成 True
        self.floating.move(1200, 300)
        # _flip_for_current_position 直接看窗口几何，不调用 _update_flip_from_screen
        self.assertEqual(self.floating._flip_for_current_position(), False,
                         "折叠态不应重新判定朝向")

    def test_light_click_collapses(self):
        """轻点猫（位移 < 5px、时长 < 300ms）→ 250ms 后折叠。"""
        self.floating.rebuild()
        self.floating.set_collapsed(False, persist=False)
        self.assertFalse(self.floating.is_collapsed())
        pos = QPoint(20, 20)
        self.floating.mousePressEvent(_mouse_event(_DOWN, pos))
        self.floating.mouseReleaseEvent(_mouse_event(_UP, pos))
        self.assertFalse(self.floating.is_collapsed(),
                         "250ms 延迟窗口内还不到折叠时机")
        QTest.qWait(FloatingWindow.CLICK_PENDING_MS + 80)
        self.assertTrue(self.floating.is_collapsed(), "轻点后应折叠")
        self.floating.set_collapsed(False, persist=False)

    def test_click_again_expands_and_text_immediate(self):
        """折叠态点猫 → 展开，且文本立即正确（一点击展开就要看到正确时间）。"""
        self.floating.rebuild()
        self.floating.set_collapsed(True, persist=False)
        self.assertTrue(self.floating.is_collapsed())
        pos = QPoint(20, 20)
        self.floating.mousePressEvent(_mouse_event(_DOWN, pos))
        self.floating.mouseReleaseEvent(_mouse_event(_UP, pos))
        QTest.qWait(FloatingWindow.CLICK_PENDING_MS + 80)
        self.assertFalse(self.floating.is_collapsed(), "折叠态点猫后应展开")
        self.assertTrue(self.floating._rows, "展开态应有文本行")
        for _ball, _name_label, _suffix, time_label, _event in self.floating._rows:
            self.assertNotEqual(time_label.text(), "", "展开后时间文本不应为空")
            self.assertFalse(time_label.isHidden(), "展开后时间标签不应被隐藏（应立即恢复显示）")

    def test_double_click_does_not_collapse(self):
        """双击 → 打开管理窗口，且不折叠（249ms 延迟窗口被第二击取消）。"""
        self.floating.rebuild()
        self.floating.set_collapsed(False, persist=False)
        self.assertFalse(self.floating.is_collapsed())
        pos = QPoint(20, 20)
        with mock.patch.object(self.floating._main_window, "show_and_activate") as show:
            self.floating.mousePressEvent(_mouse_event(_DOWN, pos))
            self.floating.mouseReleaseEvent(_mouse_event(_UP, pos))
            # 双击的第二击：取消待折叠定时器，打开管理窗口
            self.floating.mouseDoubleClickEvent(_mouse_event(_DBL, pos))
            show.assert_called_once_with()
            # 等待超过 250ms，确认折叠被取消、没有发生
            QTest.qWait(FloatingWindow.CLICK_PENDING_MS + 80)
            self.assertFalse(self.floating.is_collapsed(),
                             "双击不应触发折叠（第一击的待折叠定时器必须被取消）")

    # --- D) 拖拽：猫完整可见、卡片可出界 -------------------------------------------------

    def test_drag_clamp_keeps_pet_fully_visible(self):
        """把拖拽目标设为屏幕外，钳制后猫整体仍落在屏幕内。"""
        self._set_area(0, 0, 400, 300)
        self.floating._apply_flipped(False, persist=False)
        # 模拟拖到右下角屏幕外很远处
        x, y = self.floating._clamp_drag_position(99999, 99999)
        self.floating.move(x, y)
        box = self.floating._pet_box()
        pet_left = self.floating.x() + box.x()
        pet_top = self.floating.y() + box.y()
        self.assertGreaterEqual(pet_left, 0)
        self.assertGreaterEqual(pet_top, 0)
        self.assertLessEqual(pet_left + box.width(), 400, "猫右边缘不应出界")
        self.assertLessEqual(pet_top + box.height(), 300, "猫下边缘不应出界")

    def test_drag_clamp_allows_card_to_go_offscreen(self):
        """拖拽钳制只保猫可见：卡片区整体可以伸出屏幕（这不会触发整窗回拉）。"""
        self._set_area(0, 0, 400, 300)
        self.floating._apply_flipped(False, persist=False)
        x, y = self.floating._clamp_drag_position(99999, 99999)
        self.floating.move(x, y)
        box = self.floating._pet_box()
        pet_right = self.floating.x() + box.x() + box.width()
        window_right = self.floating.x() + self.floating.width()
        self.assertEqual(pet_right, 400, "猫右边缘应刚好贴在屏幕右界")
        self.assertGreater(window_right, 400, "卡片可以伸出屏幕右边界，不受整窗钳制")


if __name__ == "__main__":
    unittest.main(verbosity=2)
