"""独立验证：主窗口 hide() 后，悬浮窗是否仍在每秒刷新。

刻意不用工程师的脚本，自己写一遍。关键点：
  - 用 QTest.qWait 驱动真实事件循环，不用 time.sleep（sleep 期间 QTimer 不触发）
  - 用 spy 统计 refresh_times / refresh_texts 的**调用次数**来判定刷新，
    而不是比文本内容 —— 因为 refresh_times 只在文本变化时才写（改文本），
    文本恰好变不变取决于取样窗口是否跨过秒边界，必然偶发失败。
    调用次数与秒数进位完全无关。

判定规则（与计时逻辑严格对应，见 main_window.check_notifications）：
  - 主窗口**可见**时：refresh_times 调用次数 > 0（可见才刷表）
  - 主窗口**隐藏**时：refresh_times 调用次数 == 0（省 CPU，不刷表）
  - 悬浮窗：隐藏主窗口后仍在刷新（refresh_texts 调用次数 > 0）
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["APPDATA"] = os.path.join(os.environ["TEMP"], "dpt_lead_verify")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.models import DisplayFormat, Event, EventMode
from ui.floating_window import FloatingWindow
from ui.main_window import MainWindow


def make_event() -> Event:
    """造一个 90 秒后到点的事件，避免测试期间真的触发通知。"""
    import time as _t

    return Event(
        id="lead-verify",
        name="验证用",
        mode=EventMode.COUNTDOWN,
        anchor_ts=_t.time() + 90,
        display_format=DisplayFormat.FULL,
    )


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow([make_event()])
    floating = FloatingWindow(win)
    win.floating_window = floating
    win.events_changed.connect(floating.rebuild)

    # --- 装 spy：包装原方法，只计数调用次数，再转调原实现 ---
    win_refresh_times = win.refresh_times
    win_refresh_count = {"n": 0, "visible": 0, "hidden": 0}
    def spy_times(*a, **k):
        win_refresh_count["n"] += 1
        if win.isVisible():
            win_refresh_count["visible"] += 1
        else:
            win_refresh_count["hidden"] += 1
        return win_refresh_times(*a, **k)
    win.refresh_times = spy_times

    refresh_texts = floating.refresh_texts
    float_count = {"n": 0, "hidden": 0}
    def spy_texts(*a, **k):
        float_count["n"] += 1
        if not win.isVisible():
            float_count["hidden"] += 1
        return refresh_texts(*a, **k)
    floating.refresh_texts = spy_texts

    floating.show()
    win.show()
    QTest.qWait(300)
    win_visible_calls = win_refresh_count["n"]  # showEvent 里也刷过一次

    # 可见窗口再等 1.2s：每秒节拍至少再刷一次
    QTest.qWait(1200)
    win_visible_calls = win_refresh_count["n"]

    # --- 关键：隐藏主窗口 ---
    win.hide()
    QTest.qWait(200)
    hidden_floor = win_refresh_count["n"]     # 隐藏期开始的计数
    float_hidden_before = float_count["n"]
    float_count["hidden"] = 0                 # 重计「隐藏期间」的悬浮窗刷新

    QTest.qWait(2200)

    win_hidden_calls = win_refresh_count["n"] - hidden_floor
    float_hidden_calls = float_count["hidden"]

    # 结构断言：主窗口身上只应剩一个 QTimer
    timers = [n for n, v in vars(win).items() if isinstance(v, QTimer)]

    print(f"可见时 refresh_times 累计调用 : {win_visible_calls}")
    print(f"隐藏期间 refresh_times 调用   : {win_hidden_calls}")
    print(f"  -> 可见会刷 : {win_visible_calls > 0}")
    print(f"  -> 隐藏冻结 : {win_hidden_calls == 0}")
    print(f"隐藏期间 refresh_texts 调用   : {float_hidden_calls}")
    print(f"  -> 悬浮窗隐藏期间仍刷新 : {float_hidden_calls > 0}")
    print(f"主窗口 QTimer 字段 : {timers}")

    ok = (
        win_visible_calls > 0
        and win_hidden_calls == 0
        and float_hidden_calls > 0
        and timers == ["_notify_timer"]
    )
    print()
    print("[PASS] 不变量全部成立" if ok else "[FAIL] 不变量被破坏")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
