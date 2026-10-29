"""用 Qt（QImage）把根目录 body.png 裁好写回 assets/pet/body.png。

为什么不用 tools/prepare_pet_asset.py：
    那个脚本依赖 Pillow，而项目 .venv 刻意不装 Pillow（运行时只读素材）。
    与 tools/make_app_icon.py 同一思路 —— 全程用 Qt（已在 .venv 里），
    不引入任何新依赖，也不需要外部解释器。

本次处理的素材是「已带透明通道」的版本（根目录 body.png，1895×1662，
alpha 有抗锯齿过渡）。它没有水印、也没有白色背景，所以不做抠图，
只做两件事：

  1. trim：按 alpha 非零内容的最小包围盒（bbox）裁剪。
        —— 关键：按内容裁，而不是按画布比例裁。根目录那份四边留白
           （左 382 / 右 261 / 上 186 / 下 128），画布比例 1.1402 是
           留白造成的假象，猫本体 bbox 比例实际是 0.9288（高>宽），
           与旧素材 1359x1454（比例 0.935）几乎一致。
  2. pad：在裁剪结果四周补 4% 的透明边距，和 prepare_pet_asset.py 的
        PADDING_RATIO（0.04）对齐，给呼吸/缩放动画留一点余量。

用法（在项目根目录）：
    .venv/Scripts/python.exe tools/prepare_pet_qt.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QImage, QPainter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "body.png"
OUT = PROJECT_ROOT / "assets" / "pet" / "body.png"
PADDING_RATIO = 0.04  # 与 prepare_pet_asset.py 的 PADDING_RATIO 一致


def alpha_bbox(image: QImage) -> QRect:
    """按 alpha>0 的内容像素求最小包围盒（用 constBits 快速扫描）。"""
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = rgba.width(), rgba.height()
    mv = rgba.constBits()
    # RGBA8888：每像素 4 字节，alpha 在第 4 字节（索引 3、7、11…）
    w4 = w * 4
    min_x, min_y = w, h
    max_x, max_y = -1, -1
    for y in range(h):
        row_start = y * w4
        # 先整行快速判断有没有内容（any 的步进切片由 C 层迭代，很快）
        if not any(mv[row_start + 3 : row_start + w4 : 4]):
            continue
        for x in range(w):
            if mv[row_start + x * 4 + 3] != 0:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
        if y < min_y:
            min_y = y
        max_y = y
    if max_x < min_x or max_y < min_y:
        raise RuntimeError("整张图 alpha 全为 0，无可裁剪内容")
    return QRect(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def trim_and_pad(image: QImage) -> QImage:
    """裁到 alpha bbox，再补一圈 4% 的透明边距。"""
    bbox = alpha_bbox(image)
    trimmed = image.copy(bbox)
    long_side = max(trimmed.width(), trimmed.height())
    pad = int(long_side * PADDING_RATIO)
    canvas = QImage(
        trimmed.width() + pad * 2,
        trimmed.height() + pad * 2,
        QImage.Format.Format_ARGB32,
    )
    canvas.fill(0)  # 全透明
    painter = QPainter(canvas)
    painter.drawImage(pad, pad, trimmed)
    painter.end()
    return canvas


def count_semi_transparent(image: QImage) -> int:
    """统计 alpha 介于 1..254 的半透明像素数（证明抗锯齿边缘还在）。"""
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = rgba.width(), rgba.height()
    mv = rgba.constBits()
    w4 = w * 4
    count = 0
    for y in range(h):
        row_start = y * w4
        for a in mv[row_start + 3 : row_start + w4 : 4]:
            if 0 < a < 255:
                count += 1
    return count


def main() -> int:
    if not SRC.is_file():
        print(f"[错误] 找不到源图：{SRC}")
        return 1

    app = QGuiApplication([])
    src = QImage(str(SRC))
    if src.isNull():
        print(f"[错误] 无法读取图片：{SRC}")
        return 1
    src = src.convertToFormat(QImage.Format.Format_ARGB32)
    print(f"[读取] {SRC.name} {src.width()}x{src.height()}")

    bbox = alpha_bbox(src)
    print(f"[裁剪] alpha bbox x={bbox.x()}..{bbox.x()+bbox.width()-1} "
          f"y={bbox.y()}..{bbox.y()+bbox.height()-1} "
          f"({bbox.width()}x{bbox.height()})")

    done = trim_and_pad(src)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ok = done.save(str(OUT), "PNG")
    if not ok:
        print("[错误] 写 PNG 失败")
        return 1

    semi = count_semi_transparent(done)
    print(f"[输出] {OUT.relative_to(PROJECT_ROOT)} {done.width()}x{done.height()}")
    print(f"[半透明] 像素数 {semi}（>0 说明抗锯齿边缘保留）")
    print(f"[比例] 宽高比 {done.width() / done.height():.3f}（高>宽时 <1）")
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
