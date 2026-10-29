"""生成 README 用截图。

两条硬要求（踩过坑）：
  1) 必须隔离 APPDATA —— 否则会读写用户真实配置（已发生过数据丢失事故）
  2) 必须加载中文字体 —— 否则离屏渲染的中文全是豆腐块
"""
import os, sys, tempfile

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="dpt_shots_")   # 数据安全铁律
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time as _t
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication

app = QApplication([])
app.setQuitOnLastWindowClosed(False)

# 离屏环境没有中文字体，不注册的话所有中文渲染成方块（实测过的坑）
QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")

from core.models import Event, EventMode, DisplayFormat
from core.storage import save_events
from ui.main_window import MainWindow
from ui.floating_window import FloatingWindow
from ui import theme

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "screenshots")
os.makedirs(OUT, exist_ok=True)

def mk(eid, name, mins, rc=2):
    return Event(id=eid, name=name, mode=EventMode.COUNTDOWN, anchor_ts=_t.time() + mins * 60,
                 display_format=DisplayFormat.SMART, enabled=True, show_on_desktop=True,
                 repeat_seconds=1800, repeat_count=rc, repeat_limit=5)

evs = [mk("e1", "喝水", 21, 2), mk("e2", "吃药", 33, 4),
       mk("e3", "站起来走走", 45, 0), mk("e4", "国考", 120, 3)]
save_events(evs)

w = MainWindow(evs)
f = FloatingWindow(w)
w.floating_window = f
w.events_changed.connect(f.rebuild)
f.show()
app.processEvents()

# 1) 展开态（卡片 + 猫）
p1 = os.path.join(OUT, "main-expanded.png")
f.grab().save(p1)
print("[OK] main-expanded.png", f.width(), "x", f.height(), os.path.getsize(p1), "bytes")

# 2) 折叠态（猫 + 球环绕）
f.set_collapsed(True, persist=False)
app.processEvents()
p2 = os.path.join(OUT, "main-collapsed.png")
f.grab().save(p2)
print("[OK] main-collapsed.png", f.width(), "x", f.height(), os.path.getsize(p2), "bytes")
f.set_collapsed(False, persist=False)
app.processEvents()

# 3) 管理窗口
w2 = MainWindow(evs)
w2.resize(880, 320)
w2.show()
app.processEvents()
p3 = os.path.join(OUT, "manager.png")
w2.grab().save(p3)
print("[OK] manager.png", w2.width(), "x", w2.height(), os.path.getsize(p3), "bytes")

# 4) 托盘菜单（含显示范围三档）
import ui.context_menu as CM
menu = CM.build_tray_menu(w2, None, w2)
menu.show()
app.processEvents()
p4 = os.path.join(OUT, "tray-menu.png")
menu.grab().save(p4)
print("[OK] tray-menu.png", menu.width(), "x", menu.height(), os.path.getsize(p4), "bytes")

print()
print("全部截图已存到:", OUT)