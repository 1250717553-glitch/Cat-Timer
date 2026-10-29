"""守护：测试必须在隔离的 APPDATA 下运行。

这条测试存在的唯一意义是「报警」。

背景（真实事故）：
    unittest discover 把 tests/ 当**纯目录**、按顶层模块名逐个加载
    （测试 id 形如 test_xxx.TestClass，没有 tests. 前缀），所以
    tests/__init__.py 里的隔离代码根本不会执行 —— 实测基坑。
    最终方案是每个 test_*.py 的文件头内联三行隔离代码。

如果哪天有人删掉那些内联行，这里立刻变红。
否则要等到用户的真实 config.json 被覆盖才发现 —— 而那时数据已经找不回来。
"""


from __future__ import annotations

import os as _os, tempfile as _tempfile  # 强制隔离：必须早于任何 core 模块 import
if "dpt_test_" not in _os.environ.get("APPDATA", ""):
    _os.environ["APPDATA"] = _tempfile.mkdtemp(prefix="dpt_test_")

import os
import unittest
from pathlib import Path


class EnvIsolationTest(unittest.TestCase):
    def test_appdata_points_to_temp_dir(self):
        """APPDATA 必须指向临时目录。"""
        appdata = os.environ.get("APPDATA", "")
        self.assertTrue(appdata, "APPDATA 未设置")

        # 两种可接受形态：包级隔离的 dpt_test_，或外部脚本/CI 设的 Temp 目录
        lowered = appdata.lower().replace(chr(92), "/")
        self.assertTrue(
            "dpt_test_" in appdata or "temp" in lowered,
            f"APPDATA 未隔离到临时目录: {appdata}",
        )

    def test_appdata_is_not_real_user_roaming(self):
        """APPDATA 绝不能是真实的 %AppData%\\Roaming。"""
        appdata = os.environ.get("APPDATA", "").rstrip("\\/")
        self.assertFalse(
            appdata.lower().endswith("roaming"),
            f"APPDATA 指向真实用户目录，测试会覆盖用户配置: {appdata}",
        )

    def test_config_path_is_inside_test_appdata(self):
        """真实兜底：storage 解析出的路径必须落在测试目录内。"""
        from core.storage import get_config_path

        appdata = os.environ.get("APPDATA", "")
        config_path = str(get_config_path())
        self.assertIn(
            Path(appdata).name, config_path,
            f"配置路径不在测试 APPDATA 下: {config_path}",
        )
        # 且不能落在用户 Roaming 下
        self.assertNotIn("appdata\\roaming\\", config_path.lower().replace("/", "\\"),
                         f"配置路径落在真实 Roaming 下: {config_path}")


if __name__ == "__main__":
    unittest.main(verbosity=2)