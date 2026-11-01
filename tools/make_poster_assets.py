"""生成海报用的高分辨率截图（原生高分屏渲染）。

为什么不能直接用 docs/screenshots/ 那几张：
    最宽的 manager 只有 880px。A4 海报是 2481x3509，放进去等于位图放大
    2.8 倍，文字和边缘全糊 —— 海报 SOP 第 4.4 条专门写了：
    「高分辨率原生渲染，不要位图拉伸」。

做法：启动前设 QT_SCALE_FACTOR=3，让 Qt 按 3 倍 DPR 真正重新布局和光栅化，
    grab() 出来的就是 3 倍物理像素（而不是把小图放大）。
    这一步必须在 QApplication 创建之前完成，否则不生效。

两条铁律照旧：
    1) 隔离 APPDATA
    2) 加载中文字体（QT_SCALE_FACTOR 不影响字体可用性，但离屏环境仍缺字体）
"""
import os, sys, tempfile

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="dpt_poster_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 必须在 QApplication 之前设置，Qt 才会按高 DPI 渲染
os.environ["QT_SCALE_FACTOR"] = "3"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time as _t
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QLabel

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")

from core.models import Event, EventMode, DisplayFormat
from core.storage import save_events
from ui.main_window import MainWindow
from ui.floating_window import FloatingWindow

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "poster-assets")
os.makedirs(OUT, exist_ok=True)


def save(widget, name):
    path = os.path.join(OUT, name)
    pix = widget.grab()
    pix.save(path)
    print("[OK] %-24s %d x %d  %d KB" % (name, pix.width(), pix.height(), os.path.getsize(path) // 1024))
    return pix


def mk(eid, name, mins, rc=2):
    return Event(id=eid, name=name, mode=EventMode.COUNTDOWN, anchor_ts=_t.time() + mins * 60,
                 display_format=DisplayFormat.SMART, enabled=True, show_on_desktop=True,
                 repeat_seconds=1800, repeat_count=rc, repeat_limit=5)


evs = [mk("e1", "喝水", 21, 2), mk("e2", "吃药", 33, 4),
       mk("e3", "站起来走走", 45, 0), mk("e4", "国考", 120, 3)]
save_events(evs)

print("实际 DPR:", app.devicePixelRatio())

w = MainWindow(evs)
f = FloatingWindow(w)
w.floating_window = f
f.show()
app.processEvents()
save(f, "poster-main.png")

f.set_collapsed(True, persist=False)
app.processEvents()
save(f, "poster-collapsed.png")
f.set_collapsed(False, persist=False)

w2 = MainWindow(evs)
w2.resize(880, 300)
w2.show()
for label in w2.centralWidget().findChildren(QLabel):
    if label.text().startswith("配置文件"):
        label.hide()
app.processEvents()
save(w2.centralWidget(), "poster-manager.png")

print()
print("输出:", OUT)