"""全屏自动隐藏：平台检测 + 偏好持久化 + 主窗口集成。

覆盖四条不变量（核心依赖 PySide6 + offscreen，见底部运行说明）：
  A) should_hide 对全部 7 个 QUNS 状态给出正确判定，None（查询失败）按「宁可露出」返回 False；
  B) 偏好读写：默认开启、roundtrip、损坏 / 缺失回退、未知键忽略；
  C) 全屏轮询复用 1 秒节拍限流（2 秒一次），连续 10 次 check_notifications 查询 ≤ 5 次；
  D) 全屏隐藏期间到点事件：不弹通知（notification_requested 不发），
     但 fired_at 仍标记并落盘；未隐藏时照常弹通知。

运行方式（在项目根目录执行）：
    export APPDATA=$(mktemp -d)
    export QT_QPA_PLATFORM=offscreen
    .venv/Scripts/python.exe -m unittest tests.test_fullscreen -v
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

from PySide6.QtWidgets import QApplication

import platform_win.fullscreen as fullscreen
from core.models import DisplayFormat, Event, EventMode
from core.preferences import (
    get_preferences_path,
    VISIBILITY_ALWAYS,
    VISIBILITY_MODES,
    VISIBILITY_WINDOW,
    load_visibility_mode,
    load_preferences,
    save_preferences,
)
from core.storage import load_events
from ui.floating_window import FloatingWindow
from ui.main_window import MainWindow


def _make_due_event() -> Event:
    """一个已到点、未提醒的倒计时：anchor 在过去，collect_due_events 会选中它。"""
    return Event(
        name="到点事件",
        mode=EventMode.COUNTDOWN,
        anchor_ts=time.time() - 10,
        display_format=DisplayFormat.FULL,
        enabled=True,
        show_on_desktop=True,
    )


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


class ShouldHideTest(unittest.TestCase):
    """不变量 A：状态 -> 是否隐藏的纯函数判定。"""

    # 微软官方 QUERY_USER_NOTIFICATION_STATE 只有 1~7 七个取值，
    # 没有 0，也没有 SCREENSAVER（早期版本误把 6 当成屏保，官方 6 是免打扰）。
    OFFICIAL_STATES = (1, 2, 3, 4, 5, 6, 7)

    def test_constants_match_official_enum(self):
        """常量取值必须与微软官方一致 —— 写错会埋雷（以后按常量分流就会误判）。"""
        self.assertEqual(fullscreen.QUNS_NOT_PRESENT, 1)
        self.assertEqual(fullscreen.QUNS_BUSY, 2)
        self.assertEqual(fullscreen.QUNS_RUNNING_D3D_FULL_SCREEN, 3)
        self.assertEqual(fullscreen.QUNS_PRESENTATION_MODE, 4)
        self.assertEqual(fullscreen.QUNS_ACCEPTS_NOTIFICATIONS, 5)
        self.assertEqual(fullscreen.QUNS_QUIET_TIME, 6)
        self.assertEqual(fullscreen.QUNS_APP, 7)

    def test_should_hide_all_states(self):
        """除 5（正常桌面）外，其余 6 个官方取值一律隐藏。"""
        for state in self.OFFICIAL_STATES:
            self.assertEqual(
                fullscreen.should_hide(state),
                state != fullscreen.QUNS_ACCEPTS_NOTIFICATIONS,
                f"state={state} 判定不符预期",
            )

    def test_should_hide_none_false(self):
        """查询失败返回 None，按「宁可露出」处理，绝不隐藏。"""
        self.assertFalse(fullscreen.should_hide(None))


class ForegroundCoversTest(unittest.TestCase):
    """前台窗口覆盖判定中的「排除自身」逻辑。

    为什么必须用 mock 而不是真机测：
        offscreen 环境下 winId() 返回假句柄、SetForegroundWindow 不生效，
        真实前台窗口根本无法构造成「本进程的窗口」。
        测的是「PID 等于自己 -> False」这条判断，它不依赖窗口系统。

    这条最容易出错且最难被用户发现：漏了它，用户最大化管理窗口时猫会被藏起来，
    用户只会觉得「程序自己把宠物弄丢了」。
    """

    def test_own_window_never_counts_as_covering(self):
        """前台是本进程的窗口 -> 不算覆盖，返回 False。"""
        with mock.patch.object(fullscreen, "_foreground_hwnd", return_value=12345), \
             mock.patch.object(fullscreen, "_window_pid", return_value=os.getpid()):
            self.assertFalse(fullscreen.foreground_covers_screen())

    def test_other_process_fullscreen_window_counts(self):
        """别人进程的窗口占满屏 -> True（这是「仅桌面可见」档要隐藏的依据）。"""
        screen = fullscreen._RECT()
        screen.left, screen.top, screen.right, screen.bottom = 0, 0, 1920, 1080
        user32 = mock.MagicMock()
        user32.GetWindowRect.side_effect = lambda hwnd, ref: (
            setattr(ref._obj, "left", 0),
            setattr(ref._obj, "top", 0),
            setattr(ref._obj, "right", 1920),
            setattr(ref._obj, "bottom", 1080),
            True,
        )[-1]
        user32.IsWindowVisible.return_value = True
        user32.MonitorFromWindow.return_value = 99

        def fake_monitor_info(_mon, ref):
            ref._obj.rcMonitor = screen
            return True

        user32.GetMonitorInfoW.side_effect = fake_monitor_info

        with mock.patch.object(fullscreen, "_load_user32", return_value=user32), \
             mock.patch.object(fullscreen, "_foreground_hwnd", return_value=777), \
             mock.patch.object(fullscreen, "_window_pid", return_value=99999):
            self.assertTrue(fullscreen.foreground_covers_screen())

    def test_any_step_failure_returns_false(self):
        """拿不到前台窗口 -> False（宁可露出，不误藏）。"""
        with mock.patch.object(fullscreen, "_foreground_hwnd", return_value=None):
            self.assertFalse(fullscreen.foreground_covers_screen())

    def test_closed_window_returns_false(self):
        """窗口不可见（最小化时 rect 是 -32000）-> False。"""
        user32 = mock.MagicMock()
        user32.IsWindowVisible.return_value = False
        with mock.patch.object(fullscreen, "_load_user32", return_value=user32), \
             mock.patch.object(fullscreen, "_foreground_hwnd", return_value=888), \
             mock.patch.object(fullscreen, "_window_pid", return_value=99999):
            self.assertFalse(fullscreen.foreground_covers_screen())

    def test_desktop_window_never_counts_as_cover(self):
        """桌面窗口 Progman / WorkerW / Shell_TrayWnd 占满全屏也不算遮挡。

        这是「隐藏任务栏后点桌面，猫消失」的 bug 回归：点击桌面时前台正是
        Progman（或隐藏任务栏后的 WorkerW），它们天然铺满屏幕，绝不能据此藏宠物。
        """
        for cls in ("Progman", "WorkerW", "Shell_TrayWnd"):
            with self.subTest(cls=cls), \
                 mock.patch.object(fullscreen, "_foreground_hwnd", return_value=777), \
                 mock.patch.object(fullscreen, "_window_pid", return_value=99999), \
                 mock.patch.object(fullscreen, "_window_class", return_value=cls):
                # 不 mock GetWindowRect：类名命中后应直接 return False，不再走几何判定
                self.assertFalse(fullscreen.foreground_covers_screen(), cls)

    def test_desktop_class_check_happens_before_geometry(self):
        """类名命中桌面集合要在几何判定之前 —— 证明这是短路而不是靠几何巧合。

        反例：若类名检查在 GetWindowRect 之后，一个 getpid 相同但类名是 Progman
        的窗口会先被「本进程」分支拦住；这里故意让 pid 不同 + 铺满屏，仍应为 False。
        """
        screen = fullscreen._RECT()
        screen.left, screen.top, screen.right, screen.bottom = 0, 0, 1920, 1080
        user32 = mock.MagicMock()
        user32.GetWindowRect.side_effect = lambda hwnd, ref: (
            setattr(ref._obj, "left", 0),
            setattr(ref._obj, "top", 0),
            setattr(ref._obj, "right", 1920),
            setattr(ref._obj, "bottom", 1080),
            True,
        )[-1]
        user32.IsWindowVisible.return_value = True
        user32.MonitorFromWindow.return_value = 99

        def fake_monitor_info(_mon, ref):
            ref._obj.rcMonitor = screen
            return True

        user32.GetMonitorInfoW.side_effect = fake_monitor_info

        with mock.patch.object(fullscreen, "_load_user32", return_value=user32), \
             mock.patch.object(fullscreen, "_foreground_hwnd", return_value=777), \
             mock.patch.object(fullscreen, "_window_pid", return_value=99999), \
             mock.patch.object(fullscreen, "_window_class", return_value="WorkerW"):
            self.assertFalse(fullscreen.foreground_covers_screen())

    def test_none_class_falls_through_to_geometry(self):
        """类名查不到（None）不藏——继续走几何判定，别人窗口铺满屏仍算遮挡。"""
        screen = fullscreen._RECT()
        screen.left, screen.top, screen.right, screen.bottom = 0, 0, 1920, 1080
        user32 = mock.MagicMock()
        user32.GetWindowRect.side_effect = lambda hwnd, ref: (
            setattr(ref._obj, "left", 0),
            setattr(ref._obj, "top", 0),
            setattr(ref._obj, "right", 1920),
            setattr(ref._obj, "bottom", 1080),
            True,
        )[-1]
        user32.IsWindowVisible.return_value = True
        user32.MonitorFromWindow.return_value = 99

        def fake_monitor_info(_mon, ref):
            ref._obj.rcMonitor = screen
            return True

        user32.GetMonitorInfoW.side_effect = fake_monitor_info

        with mock.patch.object(fullscreen, "_load_user32", return_value=user32), \
             mock.patch.object(fullscreen, "_foreground_hwnd", return_value=777), \
             mock.patch.object(fullscreen, "_window_pid", return_value=99999), \
             mock.patch.object(fullscreen, "_window_class", return_value=None):
            self.assertTrue(fullscreen.foreground_covers_screen())


class PreferencesTest(unittest.TestCase):
    """不变量 B：偏好持久化与回退。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env_patch = _EnvPatch()
        self._env_patch.start(self._tmp.name)

    def tearDown(self):
        self._env_patch.restore()
        self._tmp.cleanup()

    def test_default_mode_is_window(self):
        """缺失文件 -> 默认「窗口可见」（与三档上线前的行为一致）。"""
        self.assertEqual(load_visibility_mode(), VISIBILITY_WINDOW)

    def test_roundtrip_all_modes(self):
        """三档都能存下去、读回来。"""
        for mode in VISIBILITY_MODES:
            save_preferences({"visibility_mode": mode})
            self.assertEqual(load_visibility_mode(), mode)

    def test_corrupt_file_falls_back_default(self):
        """损坏的 JSON -> 默认档，不抛异常。"""
        path = get_preferences_path()
        path.write_text("{ not valid json !!!", encoding="utf-8")
        self.assertEqual(load_visibility_mode(), VISIBILITY_WINDOW)

    def test_non_dict_falls_back_default(self):
        path = get_preferences_path()
        path.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(load_visibility_mode(), VISIBILITY_WINDOW)

    def test_unknown_keys_ignored(self):
        """未知键不落盘也不报错，已知键照常保存。"""
        save_preferences({"visibility_mode": VISIBILITY_ALWAYS, "junk": 123})
        prefs = load_preferences()
        self.assertEqual(prefs["visibility_mode"], VISIBILITY_ALWAYS)
        self.assertNotIn("junk", prefs)

    def test_invalid_mode_falls_back_default(self):
        path = get_preferences_path()
        path.write_text('{"visibility_mode": "nonsense"}', encoding="utf-8")
        self.assertEqual(load_visibility_mode(), VISIBILITY_WINDOW)

    # --- 旧键迁移：fullscreen_auto_hide -> visibility_mode ---

    def test_legacy_false_migrates_to_always(self):
        """旧版关掉自动隐藏 == 始终可见，升级后行为不变。"""
        path = get_preferences_path()
        path.write_text('{"fullscreen_auto_hide": false}', encoding="utf-8")
        self.assertEqual(load_visibility_mode(), VISIBILITY_ALWAYS)

    def test_legacy_true_migrates_to_window(self):
        path = get_preferences_path()
        path.write_text('{"fullscreen_auto_hide": true}', encoding="utf-8")
        self.assertEqual(load_visibility_mode(), VISIBILITY_WINDOW)

    def test_legacy_key_not_written_back(self):
        """旧键只在读时迁移，不应再被写出去。"""
        path = get_preferences_path()
        path.write_text('{"fullscreen_auto_hide": false}', encoding="utf-8")
        save_preferences({"visibility_mode": VISIBILITY_WINDOW})
        prefs = load_preferences()
        self.assertEqual(prefs["visibility_mode"], VISIBILITY_WINDOW)
        self.assertFalse(path.read_text(encoding="utf-8").count("fullscreen_auto_hide"))


class FullscreenAutoHideTest(unittest.TestCase):
    """不变量 C / D：主窗口集成（限流 + 隐藏期间抑制通知）。"""

    _app = None

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls._env_patch = _EnvPatch()
        app = QApplication.instance()
        if app is None:
            cls._app = QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls._env_patch.restore()
        cls._tmp.cleanup()

    def setUp(self):
        # 每个用例用**独立的 APPDATA 目录**：偏好经 preferences.json 落盘，
        # 共用同一个目录会让「上一条用例保存的 visibility_mode=always」
        # 泄漏到下一条用例（表现为「明明设的是全屏，却没隐藏」）。
        # 这是磁盘级的状态泄漏，比内存字段泄漏更隐蔽 —— 重置字段治不了它。
        self._case_tmp = tempfile.TemporaryDirectory()
        self._env_patch.start(self._case_tmp.name)
        self.main = MainWindow(events=[])
        self.floating = FloatingWindow(self.main)
        self.main.floating_window = self.floating
        self.floating.show()
        self.spied = []
        self.main.notification_requested.connect(self.spied.append)

        # 关键：全屏检查有 2 秒限流，其状态存在实例字段上。
        # 上一条用例跑完后 _last_fullscreen_check 已是「刚刚」，新用例的第一次
        # check_notifications 会被直接跳过、什么也不做 —— 表现为断言
        # 「悬浮窗应隐藏」离奇失败，而代码其实是对的。
        # 统一在这里清零，而不是让每个用例各记一次（已有用例漏过）。
        self.main._last_fullscreen_check = 0.0

    def tearDown(self):
        self.main.hide()
        self.floating.close()
        self.floating = None
        self.main = None
        self._case_tmp.cleanup()

    def test_throttle_limits_query_calls(self):
        """连续 10 次 check_notifications，系统状态查询 ≤ 5 次（2 秒限流）。"""
        with mock.patch.object(fullscreen, "query_state", return_value=5) as q:
            for _ in range(10):
                self.main.check_notifications()
        self.assertLessEqual(q.call_count, 5, f"查询调用次数应≤5，实际{q.call_count}")

    def test_fullscreen_due_event_suppressed_but_marked(self):
        """全屏隐藏期间到点：不弹通知，但 fired_at 标记且落盘。"""
        ev = _make_due_event()
        self.main.events = [ev]
        with mock.patch.object(fullscreen, "query_state", return_value=3) as q:
            self.main.check_notifications()  # 查询到全屏 -> 隐藏悬浮窗
            self.main.check_notifications()  # 限流内不重复查询，但仍处理到点事件

        # 悬浮窗已被自动隐藏
        self.assertFalse(self.floating.isVisible())
        self.assertTrue(self.main._last_fullscreen_hidden)
        # 到点事件已标记提醒（不重弹），且没有发系统通知
        self.assertIsNotNone(ev.fired_at)
        self.assertEqual(self.spied, [])
        # 已落盘：从磁盘读回仍是已提醒状态
        reloaded = [e for e in load_events() if e.id == ev.id][0]
        self.assertIsNotNone(reloaded.fired_at)

    def test_not_fullscreen_still_emits(self):
        """未进入全屏（状态 5）：到点事件照常弹通知。"""
        ev = _make_due_event()
        self.main.events = [ev]
        with mock.patch.object(fullscreen, "query_state", return_value=5):
            self.main.check_notifications()
        self.assertTrue(self.floating.isVisible())
        self.assertIsNotNone(ev.fired_at)
        self.assertEqual(self.spied, ["到点事件 已到点"])

    def test_check_notifications_cross_day_resets_and_persists(self):
        """追加指令 4：check_notifications 单出口落盘。昨天用满 5/5、但
        anchor 在未来的事件（未到点）今天经 check_notifications：仅发生
        跨天归零（不进到点分支），落盘后磁盘读回 count=0、日期=今天。
        —— 证明「重置本身」就触发唯一落盘，而非依赖到点分支先发生。"""
        import datetime
        today_ts = time.time()
        ev = _make_due_event()
        ev.anchor_ts = today_ts + 10 * 86400  # 远在未来：不到点
        ev.repeat_seconds = 1800
        ev.repeat_limit = 3
        ev.repeat_count = 3
        ev.repeat_count_date = (
            datetime.date.fromtimestamp(today_ts - 3 * 86400).isoformat()
        )
        self.main.events = [ev]
        with mock.patch.object(fullscreen, "query_state", return_value=5):
            self.main.check_notifications()
            self.main.check_notifications()  # 第二次同天：幂等不重复归零
        today = datetime.date.fromtimestamp(today_ts).isoformat()
        self.assertEqual(ev.repeat_count, 0, "cross-day 自检应归零")
        self.assertEqual(ev.repeat_count_date, today)
        reloaded = [e for e in load_events() if e.id == ev.id][0]
        self.assertEqual(reloaded.repeat_count, 0, "跨天归零必须落盘")
        self.assertEqual(reloaded.repeat_count_date, today)

    def test_tray_manual_restart_persists_via_restart_counting(self):
        """追加指令：托盘「重新开始」走 _restart_event -> _after_data_changed
        -> save_events，落盘后 count=0、fired_at=None、anchor=now+周期。"""
        import datetime
        today = datetime.date.fromtimestamp(time.time()).isoformat()
        ev = _make_due_event()
        ev.repeat_seconds = 1800
        ev.repeat_limit = 5
        ev.repeat_count = 4
        ev.repeat_count_date = today
        self.main.events = [ev]
        # 行号 0，_restart_event 校验 _is_repeating(0) 为 True
        self.main._restart_event(0)
        reloaded = [e for e in load_events() if e.id == ev.id][0]
        self.assertEqual(reloaded.repeat_count, 0)
        self.assertIsNone(reloaded.fired_at)
        self.assertGreater(reloaded.anchor_ts, time.time())

    def test_manual_show_skips_auto_hide(self):
        """用户手动显示悬浮窗后，本次全屏会话不再自动隐藏。"""
        with mock.patch.object(fullscreen, "query_state", return_value=3) as q:
            self.main.check_notifications()  # 进入全屏，自动隐藏一次
            self.main.set_floating_visible(True)   # 用户手动恢复
            self.main._last_fullscreen_check = 0.0  # 重置限流以便再次查询
            self.main.check_notifications()
        self.assertTrue(self.floating.isVisible())
        self.assertTrue(self.main._fullscreen_manual_override)
        self.assertGreaterEqual(q.call_count, 2)

    def test_switch_to_always_restores_floating(self):
        """切到「始终可见」：正被全屏隐藏的悬浮窗立即恢复。

        这条取代了旧的「关闭全屏自动隐藏开关」用例 —— 那个布尔开关已删除，
        它的语义并入「始终可见」档（两个入口做同一件事会让用户困惑）。
        """
        # mock 必须覆盖到 set_visibility_mode 内部那次「重新判定」——
        # 它在换档后立刻调 _should_hide_now()，若此时 mock 已退出，
        # 查到的就是真实桌面状态（5），判定为「不该藏」，本次用例失去意义。
        with mock.patch.object(fullscreen, "query_state", return_value=3):
            self.main.check_notifications()  # 进入全屏，隐藏悬浮窗
            self.assertFalse(self.floating.isVisible())
            self.main.set_visibility_mode(VISIBILITY_ALWAYS)  # 切档 -> 立即恢复
            self.assertTrue(self.floating.isVisible())
            self.assertFalse(self.main._last_fullscreen_hidden)

    def test_switch_to_always_persists(self):
        """换档要落盘，重启后仍是「始终可见」。"""
        self.main.set_visibility_mode(VISIBILITY_ALWAYS)
        self.assertEqual(load_visibility_mode(), VISIBILITY_ALWAYS)


if __name__ == "__main__":
    unittest.main(verbosity=2)