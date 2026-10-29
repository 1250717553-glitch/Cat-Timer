"""core/paths.py 单元测试（纯逻辑，不需要 PySide6）。

打包后资源找不到是「换台电脑就打不开」级别的问题，所以这里的路径解析必须有测试兜住。
用 mock 伪造 sys.frozen / sys._MEIPASS 来模拟打包后的运行环境。

运行方式：
    python -m unittest tests.test_paths -v
"""


from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import sys
import unittest
from pathlib import Path
from unittest import mock

from core import paths

# 开发期项目根目录（core/paths.py 的上上级）
EXPECTED_DEV_ROOT = Path(paths.__file__).resolve().parent.parent


class TestDevMode(unittest.TestCase):
    """开发期（未打包）行为。"""

    def test_is_frozen_false(self):
        self.assertFalse(paths.is_frozen())

    def test_base_dir_is_project_root(self):
        self.assertEqual(paths.base_dir(), EXPECTED_DEV_ROOT)
        self.assertTrue((paths.base_dir() / "main.py").is_file())

    def test_app_dir_is_project_root(self):
        self.assertEqual(paths.app_dir(), EXPECTED_DEV_ROOT)

    def test_resource_path(self):
        self.assertEqual(paths.resource_path("assets/tray.png"), EXPECTED_DEV_ROOT / "assets" / "tray.png")


class TestFrozenMode(unittest.TestCase):
    """打包后（PyInstaller）行为。"""

    def test_base_dir_uses_meipass(self):
        """打包后基准目录必须是 sys._MEIPASS，不是 __file__ 推出来的路径。"""
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", r"C:\bundle\_internal", create=True):
            self.assertTrue(paths.is_frozen())
            self.assertEqual(paths.base_dir(), Path(r"C:\bundle\_internal"))
            self.assertEqual(
                paths.resource_path("assets/tray.ico"),
                Path(r"C:\bundle\_internal") / "assets" / "tray.ico",
            )

    def test_app_dir_is_exe_folder_not_internal(self):
        """程序目录应是 exe 所在目录，而不是 _internal。"""
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", r"C:\bundle\_internal", create=True), \
                mock.patch.object(sys, "executable", r"C:\bundle\app.exe"):
            self.assertEqual(paths.app_dir(), Path(r"C:\bundle"))
            # 资源目录与程序目录是两个不同的地方，别搞混
            self.assertNotEqual(paths.base_dir(), paths.app_dir())

    def test_base_dir_falls_back_to_exe_dir_without_meipass(self):
        """极端兜底：frozen 但没有 _MEIPASS 时退回 exe 所在目录。"""
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", r"C:\bundle\app.exe"):
            with mock.patch.object(paths, "is_frozen", return_value=True):
                # 删除 _MEIPASS（若存在）
                had = hasattr(sys, "_MEIPASS")
                saved = getattr(sys, "_MEIPASS", None)
                if had:
                    del sys._MEIPASS
                try:
                    self.assertEqual(paths.base_dir(), Path(r"C:\bundle"))
                finally:
                    if had:
                        sys._MEIPASS = saved


class TestFirstExisting(unittest.TestCase):
    """first_existing：按顺序取第一个存在的文件。"""

    def test_returns_none_when_nothing_exists(self):
        self.assertIsNone(
            paths.first_existing(("assets/__no_such_file__.png", "assets/__nope__.ico"))
        )

    def test_returns_first_existing(self):
        """第一个存在的是 main.py 时，应返回 main.py 而不是后面的。"""
        found = paths.first_existing(("main.py", "core/timer.py"))
        self.assertIsNotNone(found)
        self.assertEqual(found, EXPECTED_DEV_ROOT / "main.py")

    def test_skips_missing_and_takes_later_one(self):
        found = paths.first_existing(("__missing__.txt", "core/timer.py"))
        self.assertEqual(found, EXPECTED_DEV_ROOT / "core" / "timer.py")

    def test_empty_tuple(self):
        self.assertIsNone(paths.first_existing(()))


class TestDescribe(unittest.TestCase):
    """describe 用于排错打印，至少要能正常产出字符串。"""

    def test_dev_describe(self):
        text = paths.describe()
        self.assertIn("开发期", text)
        self.assertIn(str(EXPECTED_DEV_ROOT), text)

    def test_frozen_describe(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", r"C:\bundle\_internal", create=True):
            text = paths.describe()
            self.assertIn("打包后", text)
            self.assertIn("_internal", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
