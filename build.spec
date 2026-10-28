# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 桌面宠物计时器（免安装 onedir 版）。

用法（在项目根目录执行）：
    .venv\\Scripts\\python.exe -m PyInstaller build.spec --noconfirm
或直接双击 build.bat。

产物：dist/桌面宠物计时器/  ← 整个目录拷到别的电脑即可运行，无需装 Python。

【为什么必须 onedir 而不是 onefile】
onefile 每次启动都要把整个 PySide6 解压到临时目录，启动要好几秒；
而且「自解压并释放可执行文件」这个行为极易被杀软判为木马误报。
onedir 只是把文件平铺在一个目录里，启动快、误报少。
"""

from pathlib import Path

# PyInstaller 执行本文件时会注入 SPECPATH（本文件所在目录）
PROJECT_ROOT = Path(SPECPATH).resolve()
APP_NAME = "桌面宠物计时器"

# ---------------------------------------------------------------------------
# 数据文件：assets/（托盘图标等）打进包，运行时用 core.paths.resource_path 取
# ---------------------------------------------------------------------------
datas = []
assets_dir = PROJECT_ROOT / "assets"
if assets_dir.is_dir():
    datas.append((str(assets_dir), "assets"))
    print(f"[build.spec] 已包含资源目录：{assets_dir}")
else:
    print(f"[build.spec] 提示：未找到 {assets_dir}，跳过。")
    print("[build.spec]       托盘图标将退回系统标准图标（程序仍可正常运行）。")

# ---------------------------------------------------------------------------
# 程序图标（可选）：assets/ 下有 .ico 就用
# ---------------------------------------------------------------------------
icon_file = None
for name in ("app.ico", "tray.ico", "icon.ico"):
    candidate = assets_dir / name
    if candidate.is_file():
        icon_file = str(candidate)
        print(f"[build.spec] 使用图标：{candidate}")
        break

# ---------------------------------------------------------------------------
# 排除用不到的大模块，显著减小体积
# 本程序只用到 QtCore / QtGui / QtWidgets，其余 Qt 模块全部不需要
# ---------------------------------------------------------------------------
excludes = [
    # --- Qt 里的重量级模块 ---
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtWebView",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.QtQml",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtUiTools",
    "PySide6.QtTest", "PySide6.QtSql", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtStateMachine", "PySide6.QtTextToSpeech",
    "PySide6.QtNetworkAuth", "PySide6.QtPrintSupport", "PySide6.QtDBus",
    "PySide6.QtNetwork", "PySide6.QtSvg", "PySide6.QtSvgWidgets",
    # --- 用不到的 Python 标准库/工具 ---
    "tkinter", "unittest", "pydoc", "doctest", "pdb", "lib2to3",
    "distutils", "setuptools", "pip", "ensurepip", "venv",
    "sqlite3", "xmlrpc", "test",
    # --- 其他常见的体积/误报来源 ---
    "numpy", "matplotlib", "PIL", "pandas", "scipy",
]

# ---------------------------------------------------------------------------
# 二次裁剪：PyInstaller 的 PySide6 hook 会把一批「用不到但体积很大」的
# Qt 运行时文件一起拷进来（excludes 只能挡 Python 绑定，挡不住 DLL 和插件）。
# 这里按目标路径再过滤一遍。
# ---------------------------------------------------------------------------
DROP_PREFIXES = (
    "PySide6/Qt6Quick",            # Qt Quick（QML 运行时）
    "PySide6/Qt6Qml",              # QML 引擎
    "PySide6/Qt6VirtualKeyboard",  # 虚拟键盘
    "PySide6/Qt6Pdf",              # PDF
    "PySide6/Qt6Svg",              # SVG（本项目图标用 PNG/ICO）
    "PySide6/Qt6Network",          # 网络（本项目不联网）
)
DROP_NAMES = (
    "libcrypto-3.dll",             # 只被 Qt6Network 用（本包里没有 _ssl.pyd）
    "libssl-3.dll",
)
DROP_DIR_PREFIXES = (
    "PySide6/translations",        # Qt 自带 99 个语言包，本项目界面文案是硬编码中文
    "PySide6/plugins/tls",         # 网络 TLS 后端
    "PySide6/plugins/networkinformation",
)
DROP_EXACT = (
    "PySide6/plugins/iconengines/qsvgicon.dll",
    "PySide6/plugins/imageformats/qsvg.dll",
    "PySide6/plugins/imageformats/qpdf.dll",
    "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
)

# 注意：opengl32sw.dll（约 20MB，Qt 的软件 OpenGL 回退）**刻意保留**。
# 本项目只用 QPainter 光栅绘制，正常情况下用不到它；但在没有显卡驱动的
# 虚拟机 / 远程桌面上，Qt 会回退到它。删掉能再省 20MB，但可能让程序在
# 那类机器上起不来 —— 宁可大一点也不要「换台电脑就打不开」。
# 若确认部署环境都有正常显卡驱动，把 "opengl32sw.dll" 加进 DROP_NAMES 即可。


def _should_keep(entry) -> bool:
    """TOC 条目形如 (目标路径, 源路径, 类型)，按目标路径判断是否保留。"""
    dest = str(entry[0]).replace("\\", "/")
    if any(dest.startswith(prefix) for prefix in DROP_PREFIXES):
        return False
    if any(dest.endswith(name) for name in DROP_NAMES):
        return False
    if any(dest == name for name in DROP_EXACT):
        return False
    if any(dest.startswith(folder + "/") for folder in DROP_DIR_PREFIXES):
        return False
    return True

a = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# 应用二次裁剪（必须在 COLLECT 之前）
_binaries_before = len(a.binaries)
_datas_before = len(a.datas)
a.binaries = [entry for entry in a.binaries if _should_keep(entry)]
a.datas = [entry for entry in a.datas if _should_keep(entry)]
print(
    f"[build.spec] 二次裁剪：binaries {_binaries_before} -> {len(a.binaries)}，"
    f"datas {_datas_before} -> {len(a.datas)}"
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # onedir：可执行文件与依赖分开放
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # 不用 UPX：压缩后更容易被杀软误报
    console=False,           # 【必须】GUI 程序，不要黑色控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_NAME,           # -> dist/桌面宠物计时器/
)
