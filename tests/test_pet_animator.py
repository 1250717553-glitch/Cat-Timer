"""宠物动画曲线（core/pet_animator.py）的单元测试。

刻意**不 import PySide6**：
    这些函数是纯数学，测它们不需要 QApplication、不需要离屏平台插件。
    一旦引入 Qt，这个文件就得在 QT_QPA_PLATFORM=offscreen 下才能跑，
    与其它 core 层测试的执行条件就不一致了（也对齐项目「core 不依赖 GUI」的分层）。

运行方式（在项目根目录执行，无需任何环境变量）：
    .venv/Scripts/python.exe -m unittest tests.test_pet_animator -v
"""


from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import unittest

from core.pet_animator import (
    BREATH_OFFSET_Y_PX,
    BREATH_PERIOD_S,
    BREATH_SCALE_AMPLITUDE,
    CLICK_DURATION_S,
    CLICK_SCALE_PEAK,
    HEAD_TURN_MAX_DEG,
    HEAD_TURN_MAX_OFFSET_PX,
    HEAD_TURN_RANGE_PX,
    PetPose,
    blend,
    breathing_pose,
    click_bounce_pose,
    head_turn_pose,
)

# 采样步长：0.35s 的点击动画按 1ms 采样，足够定位峰值位置
_SAMPLE_STEP = 0.001


def _sample_click_scales() -> list[float]:
    """把整个点击动画按 1ms 采样成一条 scale 序列。"""
    count = int(CLICK_DURATION_S / _SAMPLE_STEP)
    return [click_bounce_pose(i * _SAMPLE_STEP).scale for i in range(count)]


class BreathingPoseTest(unittest.TestCase):
    """呼吸：余弦曲线，t=0 吸满、半周期呼尽。"""

    def test_scale_at_start_and_half_period_are_opposite(self):
        """相位 0 与半周期处应处在缩放的两端。"""
        start = breathing_pose(0.0)
        half = breathing_pose(BREATH_PERIOD_S / 2.0)

        self.assertAlmostEqual(start.scale, 1.0 + BREATH_SCALE_AMPLITUDE, places=6)
        self.assertAlmostEqual(half.scale, 1.0, places=6)
        self.assertGreater(start.scale, half.scale)

    def test_offset_y_flips_sign_between_start_and_half_period(self):
        """吸满时上浮（负），呼尽时下沉（正）—— 符号必须相反。"""
        start = breathing_pose(0.0)
        half = breathing_pose(BREATH_PERIOD_S / 2.0)

        self.assertAlmostEqual(start.offset_y, -BREATH_OFFSET_Y_PX, places=6)
        self.assertAlmostEqual(half.offset_y, BREATH_OFFSET_Y_PX, places=6)
        self.assertLess(start.offset_y, 0.0)
        self.assertGreater(half.offset_y, 0.0)

    def test_scale_never_below_one(self):
        """全程不缩到原始尺寸以下（缩小重采样会把 1px 描边糊掉）。"""
        for step in range(0, 321):
            pose = breathing_pose(BREATH_PERIOD_S * step / 100.0)
            self.assertGreaterEqual(pose.scale, 1.0 - 1e-9)
            self.assertLessEqual(pose.scale, 1.0 + BREATH_SCALE_AMPLITUDE + 1e-9)
            self.assertLessEqual(abs(pose.offset_y), BREATH_OFFSET_Y_PX + 1e-9)

    def test_is_periodic(self):
        """一个周期后回到同一姿势；再过半个周期则处在相反的极值。"""
        for offset in (0.0, 0.37, 1.23):
            self.assertAlmostEqual(
                breathing_pose(offset).scale,
                breathing_pose(offset + BREATH_PERIOD_S).scale,
                places=9,
            )
            self.assertAlmostEqual(
                breathing_pose(offset).offset_y,
                breathing_pose(offset + BREATH_PERIOD_S).offset_y,
                places=9,
            )
            # 半个周期之后，纵向位移必须反号（吸气 <-> 呼气）
            self.assertAlmostEqual(
                breathing_pose(offset + BREATH_PERIOD_S * 1.5).offset_y,
                -breathing_pose(offset).offset_y,
                places=9,
            )

    def test_breathing_never_static(self):
        """呼吸动画任何时刻都不该是「完全静止」，否则说明幅度被写成了 0。"""
        self.assertFalse(breathing_pose(0.0).is_static())


class ClickBouncePoseTest(unittest.TestCase):
    """点击反馈：0.35s 内 1.0 -> 1.12 -> 1.0，带轻微回弹。"""

    def test_static_at_zero(self):
        self.assertTrue(click_bounce_pose(0.0).is_static())

    def test_static_after_duration(self):
        """超时后必须精确回到静止（不能留 0.999 之类的尾巴）。"""
        for elapsed in (CLICK_DURATION_S, CLICK_DURATION_S + 0.01, CLICK_DURATION_S * 10):
            pose = click_bounce_pose(elapsed)
            self.assertTrue(pose.is_static(), f"elapsed={elapsed} 未回落静止")
            self.assertEqual(pose.scale, 1.0)

    def test_static_for_negative_elapsed(self):
        """时钟回拨等异常输入不该产生负向缩放。"""
        self.assertTrue(click_bounce_pose(-0.1).is_static())

    def test_peak_reaches_configured_scale(self):
        """动画过程中必须真的到达 CLICK_SCALE_PEAK。"""
        peak = max(_sample_click_scales())
        self.assertAlmostEqual(peak, CLICK_SCALE_PEAK, places=3)

    def test_rebound_dips_slightly_below_one(self):
        """回落后要穿过 1.0 一点点形成回弹，但不能压成「瘪掉」。"""
        low = min(_sample_click_scales())
        self.assertLess(low, 1.0)
        self.assertGreater(low, 0.97)

    def test_ends_near_one(self):
        """结束前一帧已经贴近 1.0，不会出现「突然弹回」的跳变。"""
        self.assertAlmostEqual(click_bounce_pose(CLICK_DURATION_S * 0.999).scale, 1.0, places=2)

    def test_no_offset_or_rotation(self):
        """点击动画只动缩放，不该顺带改位移/旋转（那是歪头的职责）。"""
        for step in range(0, 35):
            pose = click_bounce_pose(step * 0.01)
            self.assertEqual(pose.offset_x, 0.0)
            self.assertEqual(pose.offset_y, 0.0)
            self.assertEqual(pose.rotation, 0.0)


class HeadTurnPoseTest(unittest.TestCase):
    """歪头跟随：线性映射 + 饱和钳制。"""

    def test_centered_mouse_is_static(self):
        """鼠标正好在猫中心 -> 完全不歪。"""
        pose = head_turn_pose(500.0, 500.0)
        self.assertTrue(pose.is_static())
        self.assertEqual(pose.rotation, 0.0)
        self.assertEqual(pose.offset_x, 0.0)

    def test_left_and_right_have_opposite_signs(self):
        center = 500.0
        right = head_turn_pose(center + 80.0, center)
        left = head_turn_pose(center - 80.0, center)

        self.assertGreater(right.rotation, 0.0)
        self.assertLess(left.rotation, 0.0)
        self.assertAlmostEqual(right.rotation, -left.rotation, places=9)
        self.assertAlmostEqual(right.offset_x, -left.offset_x, places=9)

    def test_linear_inside_range(self):
        """半行程处应正好是一半角度。"""
        center = 500.0
        pose = head_turn_pose(center + HEAD_TURN_RANGE_PX / 2.0, center)
        self.assertAlmostEqual(pose.rotation, HEAD_TURN_MAX_DEG / 2.0, places=9)

    def test_clamped_beyond_range(self):
        """鼠标跑到屏幕外也只到 ±max_deg，不会转过头。"""
        center = 500.0
        far_right = head_turn_pose(center + 100000.0, center)
        far_left = head_turn_pose(center - 100000.0, center)

        self.assertAlmostEqual(far_right.rotation, HEAD_TURN_MAX_DEG, places=9)
        self.assertAlmostEqual(far_left.rotation, -HEAD_TURN_MAX_DEG, places=9)
        self.assertAlmostEqual(far_right.offset_x, HEAD_TURN_MAX_OFFSET_PX, places=9)

    def test_custom_max_deg(self):
        """max_deg 参数生效（调用方可能要更克制的幅度）。"""
        pose = head_turn_pose(500.0 + 100000.0, 500.0, max_deg=10.0)
        self.assertAlmostEqual(pose.rotation, 10.0, places=9)


class BlendTest(unittest.TestCase):
    """三路动画的叠加规则。"""

    def test_click_overrides_breathing_scale(self):
        """点击期间缩放由点击独占，呼吸幅度置零（否则会叠成 1.15 像抽筋）。"""
        breath = breathing_pose(0.0)          # scale = 1.03
        click = click_bounce_pose(CLICK_DURATION_S * 0.5)  # scale ≈ 1.12
        pose = blend(breath, click, PetPose(), click_active=True)

        self.assertAlmostEqual(pose.scale, click.scale, places=9)
        self.assertLess(pose.scale, breath.scale + click.scale - 1.0)  # 不是相加

    def test_breathing_applies_when_click_inactive(self):
        breath = breathing_pose(0.0)
        pose = blend(breath, PetPose(), PetPose(), click_active=False)
        self.assertAlmostEqual(pose.scale, breath.scale, places=9)
        self.assertAlmostEqual(pose.offset_y, breath.offset_y, places=9)

    def test_head_offset_and_rotation_always_apply(self):
        """歪头与缩放无关，点击期间也要生效。"""
        head = head_turn_pose(500.0 + 80.0, 500.0)
        for click_active in (True, False):
            pose = blend(PetPose(), PetPose(), head, click_active=click_active)
            self.assertAlmostEqual(pose.rotation, head.rotation, places=9)
            self.assertAlmostEqual(pose.offset_x, head.offset_x, places=9)

    def test_flipped_passthrough(self):
        pose = blend(PetPose(), PetPose(), PetPose(), click_active=False, flipped=True)
        self.assertTrue(pose.flipped)


class PetPoseTest(unittest.TestCase):
    """姿势数据类本身的行为。"""

    def test_default_is_static(self):
        self.assertTrue(PetPose().is_static())

    def test_any_field_makes_it_dynamic(self):
        self.assertFalse(PetPose(scale=1.01).is_static())
        self.assertFalse(PetPose(offset_x=1.0).is_static())
        self.assertFalse(PetPose(offset_y=-1.0).is_static())
        self.assertFalse(PetPose(rotation=0.5).is_static())


if __name__ == "__main__":
    unittest.main(verbosity=2)
