"""重新生成 manager.png：隐藏底部那行本机配置路径。

路径标签是 main_window 里的局部变量（没存成成员），所以从布局里把它找出来隐藏。
为什么不直接截整个 QMainWindow：那个标签会显示本机临时路径，
放进 GitHub README 既暴露环境又难看。

两条硬要求（踩过坑）：
    1) 隔离 APPDATA（数据安全铁律）
    2) 加载中文字体（否则离屏渲染全是豆腐块）
"""
import os, sys, tempfile

os.environ["APPDATA"] = tempfile.mkdtemp(prefix="dpt_shots_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
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
w.resize(880, 320)
w.show()
app.processEvents()

# 隐藏「配置文件：...」那行（按文本开头识别，避免依赖局部变量）
hidden = 0
for label in w.centralWidget().findChildren(QLabel):
    if label.text().startswith("配置文件"):
        label.hide()
        hidden += 1
print("隐藏路径标签数:", hidden)
app.processEvents()

w.centralWidget().grab().save(os.path.join(OUT, "manager.png"))
path = os.path.join(OUT, "manager.png")
central = w.centralWidget()
print("[OK] manager.png", central.width(), "x", central.height(), os.path.getsize(path), "bytes")