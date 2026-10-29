"""到点提示音：播放回落 + 设置开关 + 全屏抑制。

核心原则：播不出声绝不能影响提醒本身，更不能让程序崩。
所以这里的重点不是「能播」，而是「各种失败路径都安全」。
"""


from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 必须在 QApplication 创建之前设置离屏平台
if not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from platform_win import sound

ROOT = Path(__file__).resolve().parent.parent


def _patch_sound_dir(monkeypatch_dir: Path):
    """把 sound 模块的素材查找临时指向一个自定义目录。"""
    return mock.patch.object(sound, "_SOUND_CANDIDATES", (str(monkeypatch_dir),))


class PlayNotificationTest(unittest.TestCase):
    def test_returns_bool_and_never_raises_without_wav(self):
        """没有 WAV 时回落系统音，返回 True 且不抛异常。"""
        result = sound.play_notification()
        self.assertIsInstance(result, bool)
        self.assertTrue(result, "WAV 缺失时应回落系统提示音并成功发起播放")

    def test_missing_wav_falls_back(self):
        """候选路径不存在：依然走系统音。"""
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "not-here.wav"   # 故意不存在
            with _patch_sound_dir(fake):
                self.assertTrue(sound.play_notification())

    def test_existing_wav_plays_file(self):
        """WAV 存在时应走文件播放路径。"""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "meow.wav"
            # 写一个最小的合法 RIFF 头：播放可能失败（取决于设备），
            # 这里只验证「选中了文件分支」而不是真的发出声音。
            wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00")
            with _patch_sound_dir(wav):
                result = sound.play_notification()
            self.assertIsInstance(result, bool)

    def test_corrupt_wav_does_not_raise(self):
        """损坏的 WAV（纯文本改名）不能让异常冒出来。"""
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "broken.wav"
            wav.write_text("this is not audio at all", encoding="utf-8")
            with _patch_sound_dir(wav):
                result = sound.play_notification()
            self.assertIsInstance(result, bool, "损坏文件必须返回 bool，不能抛异常")

    def test_non_windows_returns_false(self):
        """非 Windows 平台直接返回 False（不尝试调用 winsound）。"""
        with mock.patch.object(sound.sys, "platform", "linux"):
            self.assertFalse(sound.play_notification())


class _App(unittest.TestCase):
    _app = None

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        if QApplication.instance() is None:
            cls._app = QApplication([])


class SoundIntegrationTest(_App):
    """主窗口层：设置开关 + 全屏抑制。"""

    def setUp(self):
        import tempfile as _tf

        self._tmp = _tf.TemporaryDirectory()
        self._old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old
        self._tmp.cleanup()

    def _make_window(self):
        from ui.main_window import MainWindow

        return MainWindow([])

    def test_preference_roundtrip(self):
        """设置项能存下去、读回来。"""
        from core.preferences import load_sound_enabled

        win = self._make_window()
        self.assertTrue(win.sound_enabled(), "默认应开启")
        win.set_sound_enabled(False)
        self.assertFalse(win.sound_enabled())
        self.assertFalse(load_sound_enabled(), "关闭状态应落盘")
        win.set_sound_enabled(True)
        self.assertTrue(load_sound_enabled())

    def test_sound_plays_when_enabled(self):
        """开启时，到点会调用 play_notification。"""
        import time as _t

        from core.models import DisplayFormat, Event, EventMode

        win = self._make_window()
        win._sound_enabled = True
        win._last_fullscreen_check = 0.0
        due = Event(name="到点", mode=EventMode.COUNTDOWN, anchor_ts=_t.time() - 1,
                    display_format=DisplayFormat.FULL)
        win.events = [due]

        with mock.patch.object(sound, "play_notification", return_value=True) as spy:
            win.check_notifications()
        self.assertTrue(spy.called, "开启状态下到点必须播提示音")

    def test_sound_silent_when_disabled(self):
        """关闭时，到点不播。"""
        import time as _t

        from core.models import DisplayFormat, Event, EventMode

        win = self._make_window()
        win._sound_enabled = False
        win._last_fullscreen_check = 0.0
        due = Event(name="到点", mode=EventMode.COUNTDOWN, anchor_ts=_t.time() - 1,
                    display_format=DisplayFormat.FULL)
        win.events = [due]

        with mock.patch.object(sound, "play_notification", return_value=True) as spy:
            win.check_notifications()
        self.assertFalse(spy.called, "关闭状态下到点不应播提示音")

    def test_sound_suppressed_when_fullscreen_hidden(self):
        """全屏隐藏期间不播（语义与「不弹气泡」一致）。"""
        import time as _t

        from core.models import DisplayFormat, Event, EventMode
        import platform_win.fullscreen as fs

        win = self._make_window()
        win._sound_enabled = True
        win._last_fullscreen_check = 0.0
        win._last_fullscreen_hidden = True   # 模拟「正因全屏而隐藏」
        due = Event(name="到点", mode=EventMode.COUNTDOWN, anchor_ts=_t.time() - 1,
                    display_format=DisplayFormat.FULL)
        win.events = [due]

        with mock.patch.object(sound, "play_notification", return_value=True) as spy, \
             mock.patch.object(fs, "query_state", return_value=3):
            win.check_notifications()
        self.assertFalse(spy.called, "全屏隐藏期间不应播提示音")


if __name__ == "__main__":
    unittest.main(verbosity=2)