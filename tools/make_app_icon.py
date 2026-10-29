"""从 "App .png" 生成多尺寸应用图标 assets/app.ico。

为什么不用 tools/make_tray_icon.py 那套 Pillow 流程：
    那个脚本依赖 Pillow，而项目 .venv 刻意不装 Pillow（运行时只读 .ico）。
    本脚本全程用 Qt（已在 .venv 里）做缩放与 PNG 编码，不引入任何新依赖。

尺寸策略（两套裁法）：
    48/64/256：整张原图直接缩 —— 大图标看得清，保留完整的圆角底座。
    16/32    ：先用**同一个**「猫头裁剪框」放大到占画布 88% 再缩。
        为什么小尺寸要单独裁：16px 下整张图缩下来，猫头只剩 ~11px，
        黑描边被压成 1px 灰线，整只猫糊成一团。放大到 88% 后描边有 2px，
        轮廓才立得住。
        为什么**保留奶油底座**而不是紧裁到猫头边缘：底座在深色任务栏上提供
        背景对比。紧裁到猫头后，橘猫直接压在深色任务栏上，1px 黑描边撑不住，
        反而更糊。
        16 和 32 必须用同一个裁剪框，否则这两档切换会「跳」一下。

用法（在项目根目录）：
    .venv\\Scripts\\python.exe tools/make_app_icon.py
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, Qt
from PySide6.QtGui import QGuiApplication, QImage

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE = PROJECT_ROOT / "App .png"
TARGET = PROJECT_ROOT / "assets" / "app.ico"
SIZES: tuple[int, ...] = (16, 32, 48, 64, 256)

# 小尺寸（16/32）走「猫头放大」这条路径
SMALL_SIZES = frozenset({16, 32})
# 猫头（含耳朵，按**黑色描边**量）在小尺寸画布里占的比例。
# 实测原图猫头描边只占画布 73% × 66%，16px 下只剩 11px 宽 —— 这就是要放大的原因。
SMALL_HEAD_RATIO = 0.88
# 判定「这是猫头描边」的阈值：三通道都小于它才算深色线条。
# 不用「与底色不同」来判定，因为底座本身是带渐变和投影的浅色，
# 那样量出来会把胡须和投影都算进去（实测包围盒会涨到 91%，等于没裁）。
OUTLINE_MAX_CHANNEL = 110
# 找包围盒时先把图缩到这么小再扫：1657² 逐像素读 QColor 太慢，
# 缩到 256 只有 6.5 万次，精度 ±7 源像素，对裁剪完全够用。
PROBE_SIZE = 256


def head_bounds(image: QImage) -> QRect:
    """找出猫头（含耳朵）的包围盒 —— 以**黑色描边**为准。

    为什么用描边而不是「与底色不同」：
        底座是带渐变和投影的浅色，按色差判定会把胡须、投影一起算进去，
        量出来占画布 91%，按 88% 裁等于没裁。描边是深色线条，边界干净，
        量出来 73% × 66%，与肉眼看到的「猫头占七成」一致。
    """
    probe = image.scaled(
        PROBE_SIZE,
        PROBE_SIZE,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )

    min_x, min_y = PROBE_SIZE, PROBE_SIZE
    max_x, max_y = -1, -1
    for y in range(PROBE_SIZE):
        for x in range(PROBE_SIZE):
            color = probe.pixelColor(x, y)
            if color.alpha() < 32:
                continue
            if max(color.red(), color.green(), color.blue()) >= OUTLINE_MAX_CHANNEL:
                continue
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)

    if max_x < min_x or max_y < min_y:
        # 找不到描边（异常素材）：退回整图，至少不会崩
        return QRect(0, 0, image.width(), image.height())

    scale = image.width() / float(PROBE_SIZE)
    return QRect(
        int(min_x * scale),
        int(min_y * scale),
        int((max_x - min_x + 1) * scale),
        int((max_y - min_y + 1) * scale),
    )


def head_crop(image: QImage, ratio: float) -> QImage:
    """以猫头为中心裁一个正方形，使猫头描边占该正方形的 ratio。

    裁剪框**只由猫头包围盒决定**，与目标尺寸无关 —— 所以 16 和 32 拿到的是
    同一个框，两档切换不会跳。
    代价：胡须会超出裁剪框被切掉一点。16/32px 下胡须本来就是亚像素线条，
    切掉比让它糊成噪点更好看。
    """
    bounds = head_bounds(image)
    side = int(round(max(bounds.width(), bounds.height()) / ratio))
    side = min(side, image.width(), image.height())

    left = bounds.center().x() - side // 2
    top = bounds.center().y() - side // 2
    # 夹回图内，保证 copy 不越界
    left = max(0, min(left, image.width() - side))
    top = max(0, min(top, image.height() - side))
    return image.copy(left, top, side, side)


def load_square(source: Path) -> QImage:
    """读入原图并居中裁成正方形（图标必须是方的，否则会被拉伸变形）。"""
    image = QImage(str(source))
    if image.isNull():
        raise SystemExit(f"[错误] 无法读取图片：{source}")

    if image.hasAlphaChannel() is False:
        image = image.convertToFormat(QImage.Format.Format_ARGB32)

    side = min(image.width(), image.height())
    left = (image.width() - side) // 2
    top = (image.height() - side) // 2
    return image.copy(left, top, side, side)


def render_frame(square: QImage, size: int) -> QImage:
    """把正方形原图缩到 size×size。

    先放大到 4 倍再缩回：Qt 的 SmoothTransformation 是单次双线性，
    直接 1657 -> 16 会丢掉细节；4 倍超采样后缩回，16px 下轮廓更干净。
    """
    work = square.scaled(size * 4, size * 4, Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    return work.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)


def png_bytes(image: QImage) -> bytes:
    """把 QImage 编码成 PNG 字节流（ICO 自 Vista 起允许每帧用 PNG 压缩）。"""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise SystemExit("[错误] PNG 编码失败")
    data = bytes(buffer.data())
    buffer.close()
    return data


def write_ico(path: Path, frames: list[tuple[int, bytes]]) -> None:
    """拼多尺寸 ICO：ICONDIR + N × ICONDIRENTRY + 数据区。"""
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)

    entries = b""
    payload = b""
    for size, data in frames:
        dimension = 0 if size >= 256 else size  # 256 只能写 0，ICO 的历史约定
        entries += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32,
                               len(data), offset)
        offset += len(data)
        payload += data

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + payload)


def main() -> int:
    if not SOURCE.is_file():
        print(f"[错误] 找不到源图：{SOURCE}")
        return 1

    app = QGuiApplication([])  # QImage 缩放不严格要求 QApplication，但保证插件就绪
    square = load_square(SOURCE)
    print(f"源图: {square.width()}x{square.height()}")

    # 16/32 共用这一个裁剪框（同一个框 = 两档切换不跳）
    small_square = head_crop(square, SMALL_HEAD_RATIO)
    print(
        f"小尺寸裁剪框: {small_square.width()}x{small_square.height()} "
        f"(猫头占 {SMALL_HEAD_RATIO:.0%})"
    )

    frames: list[tuple[int, bytes]] = []
    for size in SIZES:
        base = small_square if size in SMALL_SIZES else square
        frame = render_frame(base, size)
        frames.append((size, png_bytes(frame)))
        print(f"  {size}x{size} -> {len(frames[-1][1])} bytes"
              f"{' (猫头放大)' if size in SMALL_SIZES else ''}")

    write_ico(TARGET, frames)
    print(f"已生成: {TARGET} ({TARGET.stat().st_size} bytes, {len(frames)} 帧)")
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
