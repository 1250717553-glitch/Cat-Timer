"""宠物图像的加载、按 dpr 缩放缓存，以及素材缺失时的占位绘制。

三条硬要求（都是踩过或容易踩的坑）：
  1. 路径必须走 core.paths.first_existing —— 打包后资源在 sys._MEIPASS 下，
     用 __file__ 推路径在 PyInstaller 产物里会指到不存在的位置。
  2. 缓存 key 必须同时包含「目标尺寸」和「devicePixelRatio」。125%/150% 缩放
     下 dpr 是 1.25/1.5，只按尺寸做 key 会拿到按别的 dpr 缩好的位图 ——
     表现就是切到副屏（不同缩放比）后猫变模糊或突然变大。
  3. 素材缺失**不能崩**：回落成程序画的占位猫，并在控制台打印一行中文提示。
     占位路径是硬性要求，测试会在没有 body.png 的情况下跑整个窗口。

为什么不把 pixmap 塞进 QLabel 用样式表缩放：
    样式表缩放每帧都要重设 pixmap，慢且会糊；而且 QLabel 无法做旋转/镜像。
    这里只负责「给出正确尺寸的 QPixmap」，真正的变换在 paintEvent 里用
    QPainter + QTransform 做。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

from core.paths import first_existing

# 素材候选路径（相对资源基准目录）。按顺序取第一个存在的。
PET_CANDIDATES: tuple[str, ...] = ("assets/pet/body.png",)

# 占位猫的配色：与 assets/tray.ico 完全一致，保证「没有素材」时视觉上仍是同一个形象。
PLACEHOLDER_FILL = QColor("#FAC775")
PLACEHOLDER_STROKE = QColor("#BA7517")
PLACEHOLDER_INNER_EAR = QColor("#FDE7D0")
PLACEHOLDER_NOSE = QColor("#D4537E")

# ---------------------------------------------------------------------------
# 占位猫几何（单位坐标 0..1，与 tools/make_tray_icon.py 一一对应）
#
# 为什么要把这些数字抄一份而不是 import：
#    make_tray_icon.py 依赖 Pillow（只用于生成 .ico，不进运行时依赖），
#    ui 层不能为了几个常量去 import 一个运行时用不到的重依赖。
#    代价是两边要手动同步 —— 所以这里逐条标注了对应关系。
# ---------------------------------------------------------------------------

PLACEHOLDER_HEAD_CENTER = (0.5, 0.60)
PLACEHOLDER_HEAD_RX = 0.385  # 宽:高 = 1 : 0.92，扁一点才有猫的宽脸感
PLACEHOLDER_HEAD_RY = 0.354

# 宽基三角耳：基部 ≈ 头宽 1/3、高 ≈ 头高 1/3、两耳基部之间只留 8.7% 的窄缝。
# 窄而长的耳会变成兔子，宽基短耳才是猫。
PLACEHOLDER_LEFT_EAR = ((0.360, 0.046), (0.5336, 0.2473), (0.2847, 0.3065))
PLACEHOLDER_RIGHT_EAR = ((0.640, 0.046), (0.4664, 0.2473), (0.7153, 0.3065))
PLACEHOLDER_INNER_EAR_SCALE = 0.55

PLACEHOLDER_EYE_RADIUS = 0.075
PLACEHOLDER_EYE_CENTERS = ((0.345, 0.560), (0.655, 0.560))

PLACEHOLDER_NOSE_WIDTH = 0.062
PLACEHOLDER_NOSE_HEIGHT = 0.048
PLACEHOLDER_NOSE_CENTER_Y = 0.645

# 描边宽度占短边的比例
PLACEHOLDER_STROKE_RATIO = 0.045

# 模块级缓存。key = (逻辑宽, 逻辑高, dpr×100 的整数)
_cache: dict[tuple[int, int, int], QPixmap] = {}

# 原图只加载一次（1.1MB 的 PNG 解码不便宜）
_source: Optional[QPixmap] = None
_source_loaded = False
_missing_warned = False


def clear_cache() -> None:
    """清空缓存并忘掉已加载的原图（换素材 / 换屏幕后调试用）。"""
    global _source, _source_loaded, _missing_warned
    _cache.clear()
    _source = None
    _source_loaded = False
    _missing_warned = False


def pet_source_available() -> bool:
    """素材是否可用（不看缓存，直接探文件 + 解码）。"""
    path = first_existing(PET_CANDIDATES)
    if path is None:
        return False
    return not QPixmap(str(path)).isNull()


def load_pet_pixmap(width: int, height: int, dpr: float) -> QPixmap:
    """取一张「逻辑尺寸 width×height、按 dpr 预缩放」的宠物位图。

    返回的 pixmap 已经 `setDevicePixelRatio(dpr)`，调用方直接按**逻辑坐标**
    画即可（Qt 会自己映射到物理像素），不要再去手动除以 dpr。

    素材按 KeepAspectRatio 缩放 —— 猫是 1359×1454 的竖图，硬拉到方形会变形。

    :param width: 目标逻辑宽度（像素）
    :param height: 目标逻辑高度（像素）
    :param dpr: 设备像素比（QWidget.devicePixelRatioF()）
    """
    logical_w = max(1, int(round(width)))
    logical_h = max(1, int(round(height)))
    ratio = max(0.5, float(dpr))

    key = (logical_w, logical_h, int(round(ratio * 100)))
    cached = _cache.get(key)
    if cached is not None:
        return cached

    source = _load_source()
    if source is None:
        pixmap = _placeholder_pixmap(logical_w, logical_h, ratio)
    else:
        pixmap = source.scaled(
            int(round(logical_w * ratio)),
            int(round(logical_h * ratio)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(ratio)

    _cache[key] = pixmap
    return pixmap


# ---------------------------------------------------------------------------
# 原图加载
# ---------------------------------------------------------------------------


def _load_source() -> Optional[QPixmap]:
    """加载原图（只读一次盘）。失败返回 None 并打印中文提示。"""
    global _source, _source_loaded, _missing_warned

    if _source_loaded:
        return _source
    _source_loaded = True

    path = first_existing(PET_CANDIDATES)
    if path is None:
        _warn_once("未找到宠物素材（" + " / ".join(PET_CANDIDATES) + "），改用程序绘制的占位猫。")
        return None

    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        _warn_once(f"宠物素材无法解码：{path}，改用程序绘制的占位猫。")
        return None

    _source = pixmap
    return _source


def _warn_once(message: str) -> None:
    """同一进程只提示一次，避免 30fps 重绘把控制台刷爆。"""
    global _missing_warned
    if _missing_warned:
        return
    _missing_warned = True
    print(f"[pet] {message}")


# ---------------------------------------------------------------------------
# 占位猫（素材缺失时的降级路径）
# ---------------------------------------------------------------------------


def _placeholder_pixmap(width: int, height: int, dpr: float) -> QPixmap:
    """画一只占位猫：圆头 + 两只三角耳 + 两点眼，与托盘图标同款。"""
    pixmap = QPixmap(int(round(width * dpr)), int(round(height * dpr)))
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    # 之后全部用逻辑坐标作画，dpr 交给变换矩阵处理
    painter.scale(dpr, dpr)
    _draw_placeholder(painter, float(width), float(height))
    painter.end()

    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def _shrink_triangle(
    points: tuple[tuple[float, float], ...], factor: float
) -> list[tuple[float, float]]:
    """把三角形按重心等比缩小（内耳 = 耳朵缩一圈）。"""
    center_x = sum(p[0] for p in points) / len(points)
    center_y = sum(p[1] for p in points) / len(points)
    return [
        (center_x + (x - center_x) * factor, center_y + (y - center_y) * factor)
        for x, y in points
    ]


def _draw_placeholder(painter: QPainter, width: float, height: float) -> None:
    """占位猫：与 assets/tray.ico 同一套几何（宽基三角耳 + 扁圆头 + 小鼻子）。

    这些数字是从 tools/make_tray_icon.py 抄过来的，两边必须保持一致 ——
    素材缺失时用户看到的是同一个形象，而不是「另一只动物」。
    改任何一边都要同步另一边。
    """
    # 用短边做基准，把单位坐标映射到绘制区中心的正方形里
    span = min(width, height)
    offset_x = (width - span) / 2.0
    offset_y = (height - span) / 2.0

    def point(unit_x: float, unit_y: float) -> QPointF:
        return QPointF(offset_x + unit_x * span, offset_y + unit_y * span)

    stroke = max(1.0, span * PLACEHOLDER_STROKE_RATIO)
    pen = QPen(PLACEHOLDER_STROKE, stroke)
    # 尖角接头：圆角接头会把 1~2px 的耳尖磨成圆包，整只猫立刻像气球
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)

    # 耳朵先画，再让头盖住耳根 —— 否则会看到「贴片式」的接缝
    painter.setPen(pen)
    painter.setBrush(PLACEHOLDER_FILL)
    for ear in (PLACEHOLDER_LEFT_EAR, PLACEHOLDER_RIGHT_EAR):
        painter.drawPolygon(QPolygonF([point(x, y) for x, y in ear]))

    # 头：横向略扁的椭圆（宽:高 = 1 : 0.92）
    painter.drawEllipse(
        point(*PLACEHOLDER_HEAD_CENTER),
        PLACEHOLDER_HEAD_RX * span,
        PLACEHOLDER_HEAD_RY * span,
    )

    # 内耳（占位图尺寸固定 140×150，不存在 16px 那种糊成一团的问题，总是画）
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(PLACEHOLDER_INNER_EAR)
    for ear in (PLACEHOLDER_LEFT_EAR, PLACEHOLDER_RIGHT_EAR):
        inner = _shrink_triangle(ear, PLACEHOLDER_INNER_EAR_SCALE)
        painter.drawPolygon(QPolygonF([point(x, y) for x, y in inner]))

    # 眼睛
    painter.setBrush(PLACEHOLDER_STROKE)
    eye_radius = PLACEHOLDER_EYE_RADIUS * span
    for center in PLACEHOLDER_EYE_CENTERS:
        painter.drawEllipse(point(*center), eye_radius, eye_radius)

    # 鼻子：倒三角。只有两个眼睛点的脸，兔/狐/猫头鹰都长一个样，这一笔才「点题」。
    painter.setBrush(PLACEHOLDER_NOSE)
    half_width = PLACEHOLDER_NOSE_WIDTH / 2.0
    top_y = PLACEHOLDER_NOSE_CENTER_Y - PLACEHOLDER_NOSE_HEIGHT / 2.0
    bottom_y = PLACEHOLDER_NOSE_CENTER_Y + PLACEHOLDER_NOSE_HEIGHT / 2.0
    painter.drawPolygon(
        QPolygonF(
            [
                point(0.5 - half_width, top_y),
                point(0.5 + half_width, top_y),
                point(0.5, bottom_y),
            ]
        )
    )


def describe_state() -> str:
    """一句话说明当前用的是真素材还是占位图（排错时打印用）。"""
    path = first_existing(PET_CANDIDATES)
    if path is None:
        return "占位猫（未找到素材）"
    return f"素材 {path}"


__all__ = [
    "PET_CANDIDATES",
    "PLACEHOLDER_FILL",
    "PLACEHOLDER_STROKE",
    "clear_cache",
    "describe_state",
    "load_pet_pixmap",
    "pet_source_available",
]
