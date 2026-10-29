"""毛线球配色：跨进程稳定 + 同屏互斥。

为什么这两条必须单独测：
  1) 颜色稳定性测不出来就等着线上挨骂 —— Python 3 的 str hash 有随机盐，
     在单进程里连调两次结果都一样，看不出问题，必须开子进程比对。
  2) 互斥性：纯按 id 哈希分配在事件变多时必然撞色（见色板 6 个），
     而撞色的表现是「两个不同事件看起来是同一个」，属于静默错误。
"""


from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ball_index_in_subprocess(event_id: str) -> int:
    """在独立子进程里算一次配色下标（进程 hash 盐不同，能暴露不稳定实现）。"""
    code = (
        "import sys; sys.path.insert(0, %r); "
        "from ui import theme; "
        "print(theme.ball_colors(%r)[1].name())"
    ) % (str(ROOT), event_id)
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=str(ROOT), timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(f"子进程失败：{result.stderr}")
    return result.stdout.strip()


class PaletteTest(unittest.TestCase):
    def test_ball_colors_stable_across_processes(self):
        """同一事件 id 在两个独立进程里必须得到同一种颜色。

        若把实现改成内置 hash()，这条会红 —— 而单进程内怎么调都看不出问题。
        """
        for event_id in ("a1b2c3", "一个中文id", "00000000-0000-0000-0000-000000000001"):
            first = _ball_index_in_subprocess(event_id)
            second = _ball_index_in_subprocess(event_id)
            self.assertEqual(first, second, f"{event_id} 的配色跨进程不稳定")

    def test_palette_covers_all_ids(self):
        """任意 id 都能取到颜色，不越界。"""
        from ui import theme

        for event_id in ("", "x", "中文", "a" * 200):
            fill, stroke = theme.ball_colors(event_id)
            self.assertEqual(fill.alpha(), 255)
            self.assertEqual(stroke.alpha(), 255)

    def test_distinct_indices_within_screen(self):
        """同屏 6 个事件必须拿到 6 种不同配色，不能撞。"""
        from ui import theme

        ids = [f"event-{i}" for i in range(len(theme.BALL_PALETTE))]
        mapping = theme.distinct_ball_indices(ids)
        self.assertEqual(len(set(mapping.values())), len(ids), f"同屏配色撞了：{mapping}")

    def test_distinct_indices_more_than_palette_allows_repeat(self):
        """事件数超过色板容量时允许重复，但不能崩。"""
        from ui import theme

        ids = [f"e{i}" for i in range(10)]
        mapping = theme.distinct_ball_indices(ids)
        self.assertEqual(len(mapping), 10)
        for index in mapping.values():
            self.assertLess(index, len(theme.BALL_PALETTE))


class SuffixAndProgressTest(unittest.TestCase):
    """repeat_suffix / progress_ratio 的边界。"""

    def test_one_shot_has_no_suffix_and_no_ratio(self):
        from core.models import Event
        from ui.text_format import progress_ratio, repeat_suffix

        event = Event(name="国考")
        self.assertEqual(repeat_suffix(event), "")
        self.assertIsNone(progress_ratio(event))

    def test_infinite_repeat_suffix_and_none_ratio(self):
        from core.models import Event
        from ui.text_format import progress_ratio, repeat_suffix

        event = Event(name="喝水", repeat_seconds=1800)
        self.assertEqual(repeat_suffix(event), "循环")
        self.assertIsNone(progress_ratio(event), "无限循环没有进度可画")

    def test_finite_repeat_suffix_and_ratio(self):
        from core.models import Event
        from ui.text_format import progress_ratio, repeat_suffix

        event = Event(name="喝水", repeat_seconds=1800, repeat_count=3, repeat_limit=5)
        self.assertEqual(repeat_suffix(event), "3/5")
        self.assertAlmostEqual(progress_ratio(event), 0.6)

    def test_ratio_capped_at_one(self):
        """count 超过 limit（手工改坏 JSON）时比例钳到 1.0，不能 >1 画弧溢出。"""
        from core.models import Event
        from ui.text_format import progress_ratio

        event = Event(name="x", repeat_seconds=60, repeat_count=99, repeat_limit=5)
        self.assertEqual(progress_ratio(event), 1.0)


class MaxRowsTest(unittest.TestCase):
    """卡片限高：MAX_ROWS 之上要显示「还有 N 项」，且最紧急的必须在前。"""

    _app = None

    @classmethod
    def setUpClass(cls):
        import os
        if not os.environ.get("QT_QPA_PLATFORM"):
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is None:
            cls._app = QApplication([])

    def test_shows_at_most_max_rows(self):
        """7 个事件只渲染 5 行，并出现「还有 2 项」。"""
        import os
        import tempfile
        import time as _t

        from core.models import DisplayFormat, Event, EventMode
        from ui import theme
        from ui.floating_window import FloatingWindow
        from ui.main_window import MainWindow

        tmp = tempfile.mkdtemp()
        old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = tmp
        try:
            # 故意让最紧急的排在最后（列表原序）：验证 rebuild 会按剩余时间重排
            total = theme.MAX_ROWS + 3   # 比限高多 3 个，溢出数量不依赖 MAX_ROWS 具体值
            events = [
                Event(name=f"晚{i}", mode=EventMode.COUNTDOWN,
                      anchor_ts=_t.time() + (100 - i) * 60,
                      display_format=DisplayFormat.DAYS_HOURS_MINUTES)
                for i in range(total)
            ]
            main = MainWindow(events)
            floating = FloatingWindow(main)
            main.floating_window = floating
            floating.rebuild()
            self.assertEqual(len(floating._rows), theme.MAX_ROWS, "最多只渲染 MAX_ROWS 行")

            texts = [
                floating._grid.itemAt(i).widget().text()
                for i in range(floating._grid.count())
                if floating._grid.itemAt(i).widget() is not None
                and hasattr(floating._grid.itemAt(i).widget(), "text")
            ]
            expected_hint = f"还有 {total - theme.MAX_ROWS} 项"
            self.assertIn(expected_hint, texts, f"缺少溢出提示 {expected_hint}，实际文本：{texts}")

            # 排序：最紧急（剩余最少）必须在第一行
            first_event = floating._rows[0][4]
            self.assertEqual(first_event.name, "晚6", "最紧急的事件必须排在第一行")
        finally:
            if old is None:
                os.environ.pop("APPDATA", None)
            else:
                os.environ["APPDATA"] = old




class CollapsedBallLayoutTest(unittest.TestCase):
    """折叠态球环绕：不重叠是硬要求（重叠会遮住进度弧）。"""

    def test_positions_do_not_overlap(self):
        """任意球数下，球心间距都必须大于球直径。"""
        import math

        from PySide6.QtCore import QPointF

        from ui import theme

        center = QPointF(90.0, 95.0)
        for n in range(2, 9):
            ids = [f"event-{i}" for i in range(n)]
            positions = theme.collapsed_ball_positions(ids, center, theme.COLLAPSED_BALL_RADIUS)
            self.assertEqual(len(positions), n)
            for i in range(n):
                for j in range(i + 1, n):
                    dist = math.hypot(
                        positions[i].x() - positions[j].x(),
                        positions[i].y() - positions[j].y(),
                    )
                    self.assertGreater(
                        dist, theme.BALL_SIZE,
                        f"{n} 个球时第 {i} 与第 {j} 个球重叠（间距 {dist:.1f} < {theme.BALL_SIZE}）",
                    )

    def test_empty_list_returns_empty(self):
        from PySide6.QtCore import QPointF

        from ui import theme

        self.assertEqual(theme.collapsed_ball_positions([], QPointF(50, 50)), [])

    def test_positions_are_deterministic(self):
        """同一批 id 每次算出的坐标必须一致（否则每秒重绘时球会抖动）。"""
        from PySide6.QtCore import QPointF

        from ui import theme

        ids = ["a", "b", "c"]
        first = theme.collapsed_ball_positions(ids, QPointF(90, 95))
        second = theme.collapsed_ball_positions(ids, QPointF(90, 95))
        for p, q in zip(first, second):
            self.assertEqual((round(p.x(), 6), round(p.y(), 6)), (round(q.x(), 6), round(q.y(), 6)))


if __name__ == "__main__":
    unittest.main(verbosity=2)