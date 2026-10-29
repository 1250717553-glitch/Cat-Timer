"""窗口翻转判定（core/placement.py）的单元测试。

刻意不 import PySide6：判定规则是纯数学，测它不需要 QApplication。
运行方式（项目根目录）：
    .venv/Scripts/python.exe -m unittest tests.test_placement -v
"""


from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import unittest

from core.placement import should_flip


class ShouldFlipTest(unittest.TestCase):
    """按屏幕中线判定朝向。"""

    def test_center_in_left_half_keeps_default(self):
        """窗口中心在左半边 -> 不翻（猫在左、卡片向右）。"""
        self.assertFalse(should_flip(window_center_x=300.0, screen_left=0.0, screen_width=1920.0))

    def test_center_in_right_half_flips(self):
        """窗口中心在右半边 -> 翻转（猫在右、卡片向左）。"""
        self.assertTrue(should_flip(window_center_x=1500.0, screen_left=0.0, screen_width=1920.0))

    def test_exactly_on_middle_keeps_default(self):
        """正好压在中线 -> 按约定返回 False（平局取默认朝向，保证确定）。"""
        self.assertFalse(should_flip(window_center_x=960.0, screen_left=0.0, screen_width=1920.0))

    def test_zero_screen_width_keeps_default(self):
        """屏幕宽度为 0（信息未就绪）-> 不翻，不能拿无效宽度算中线。"""
        self.assertFalse(should_flip(window_center_x=100.0, screen_left=0.0, screen_width=0.0))
        self.assertFalse(should_flip(window_center_x=100.0, screen_left=0.0, screen_width=-1.0))

    def test_secondary_screen_with_negative_left(self):
        """副屏在主屏左侧时左边界是负数，中线判定依然成立。"""
        # 副屏：x ∈ [-1920, 0)，中线 -960
        self.assertFalse(should_flip(window_center_x=-1500.0, screen_left=-1920.0, screen_width=1920.0))
        self.assertTrue(should_flip(window_center_x=-300.0, screen_left=-1920.0, screen_width=1920.0))

    def test_taskbar_offset_is_respected(self):
        """用 availableGeometry 的左边界时，任务栏/侧边栏造成的偏移不会算错。"""
        # 屏幕可用区从 x=100 开始、宽 1000 -> 中线 600
        self.assertFalse(should_flip(window_center_x=599.0, screen_left=100.0, screen_width=1000.0))
        self.assertTrue(should_flip(window_center_x=601.0, screen_left=100.0, screen_width=1000.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
