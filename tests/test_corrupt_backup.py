"""损坏配置的备份与自愈。

这是数据安全问题，优先级高于任何 UI 改动：
    配置文件坏一个逗号，load_events 返回空列表，紧接着 main.py 的 save_events
    会把用户所有事件覆盖成空数组 —— 永久丢失，连恢复机会都没有。

本模块验证三件事：
    1) 损坏时生成 .corrupt-*.json 备份，且备份内容 == 原始损坏内容；
    2) 原路径被腾空，程序能正常写入新配置（rename 而非 copy 的原因）；
    3) 再次启动读到的是新配置，不再报错（真正的自愈）。
"""


from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

# 必须在任何 core 模块读 APPDATA 之前设置
if not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from core import paths, preferences, storage


class _TempAppData(unittest.TestCase):
    """每个用例独立的 APPDATA 目录，绝不碰真实用户配置。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("APPDATA")
        os.environ["APPDATA"] = self._tmp.name

    def tearDown(self):
        if self._old is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = self._old
        self._tmp.cleanup()


class BackupFunctionTest(unittest.TestCase):
    """backup_corrupt_file 本身的行为。"""

    def test_uses_rename_not_copy(self):
        """备份后原路径必须消失（rename 语义）。

        copy 的话原文件还在，下次启动又读到同样的损坏内容，反复报错，
        用户永远修不好 —— 这条是本设计的核心。
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{ broken", encoding="utf-8")
            backup = paths.backup_corrupt_file(path)
            self.assertIsNotNone(backup)
            self.assertFalse(path.exists(), "原路径应被腾空（rename 而非 copy）")
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_text(encoding="utf-8"), "{ broken")

    def test_second_corruption_gets_suffix(self):
        """同一秒内两次损坏必须生成两个不同文件，不能覆盖。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("first", encoding="utf-8")
            b1 = paths.backup_corrupt_file(path)
            path.write_text("second", encoding="utf-8")
            b2 = paths.backup_corrupt_file(path)
            self.assertNotEqual(b1.name, b2.name, "两次备份撞名，前一份被覆盖")
            self.assertEqual(b1.read_text(encoding="utf-8"), "first")
            self.assertEqual(b2.read_text(encoding="utf-8"), "second")

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(paths.backup_corrupt_file(Path(tmp) / "nope.json"))

    def test_never_raises(self):
        """它是异常处理里调用的，绝不能自己再抛。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "weird.json"
            p.write_text("x", encoding="utf-8")
            self.assertIsInstance(paths.backup_corrupt_file(p), (Path, type(None)))


class StorageBackupTest(_TempAppData):
    """config.json 损坏 -> 备份 + 自愈。"""

    def test_corrupt_config_backed_up_and_recovered(self):
        """坏 JSON -> 返回 [] + 生成备份；写入后再次读取不再报错。"""
        path = storage.get_config_path()
        path.write_text("{ this is not json", encoding="utf-8")

        events = storage.load_events()
        self.assertEqual(events, [], "损坏时应返回空列表")

        backups = list(path.parent.glob("config.corrupt-*.json"))
        self.assertEqual(len(backups), 1, f"应生成一个备份，实际 {backups}")
        self.assertEqual(
            backups[0].read_text(encoding="utf-8"), "{ this is not json",
            "备份内容必须等于原始损坏内容（不能是空文件）",
        )
        self.assertFalse(path.exists(), "原路径应被腾空，供程序写入新配置")

        # 自愈：程序写入新配置后，再读能成功
        storage.save_events([])
        self.assertEqual(storage.load_events(), [])
        self.assertTrue(path.exists())

    def test_non_dict_top_level_backed_up(self):
        """顶层不是 dict（比如是数组）也算结构损坏。"""
        path = storage.get_config_path()
        path.write_text("[1, 2, 3]", encoding="utf-8")
        storage.load_events()
        backups = list(path.parent.glob("config.corrupt-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "[1, 2, 3]")

    def test_missing_config_is_not_backed_up(self):
        """文件不存在是正常首次运行，不该生成备份。"""
        storage.load_events()
        self.assertEqual(list(storage.get_config_path().parent.glob("*.corrupt-*")), [])


class PreferencesBackupTest(_TempAppData):
    def test_corrupt_preferences_backed_up_and_recovered(self):
        path = preferences.get_preferences_path()
        path.write_text("{ broken prefs", encoding="utf-8")

        prefs = preferences.load_preferences()
        self.assertEqual(prefs, preferences._defaults())

        backups = list(path.parent.glob("preferences.corrupt-*.json"))
        self.assertEqual(len(backups), 1, f"应生成一个备份，实际 {backups}")
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{ broken prefs")
        self.assertFalse(path.exists())

        # 自愈：写入默认后能正常读回
        preferences.save_preferences({"visibility_mode": "always"})
        self.assertEqual(preferences.load_visibility_mode(), "always")

    def test_non_dict_preferences_backed_up(self):
        path = preferences.get_preferences_path()
        path.write_text("[1, 2]", encoding="utf-8")
        preferences.load_preferences()
        backups = list(path.parent.glob("preferences.corrupt-*.json"))
        self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)