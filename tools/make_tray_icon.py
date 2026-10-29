"""生成托盘图标 assets/tray.ico（猫头：圆头 + 两只三角耳 + 两只眼睛）。

为什么要用脚本生成而不是直接提交一张 .ico：
    图标是二进制资源，放进仓库既 review 不了也改不动。用脚本画出来，
    想调色、调形状、加尺寸都只是改几个常量，重跑一次就覆盖 ——
    代码与产物永远保持一致，也不会出现「图是谁导的、源文件在哪」的悬案。

尺寸策略：
    16/32/48/64/256 每个尺寸都**原生渲染**（内部 8 倍超采样后缩回），
    而不是渲染一张 256 再统一缩 —— 统一缩放会把 16px 下的描边糊成一片灰边。
    16px 只保留「圆头 + 两个耳朵 + 两个眼睛」，不放胡须、鼻子等细节：
    像素太少，多画一笔就是噪点。

依赖 Pillow，但**项目 .venv 里没有装**（程序运行时只读 .ico，不需要 Pillow）。
用一个带 Pillow 的解释器执行：

    "C:/Users/12507/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe" \
        tools/make_tray_icon.py

脚本幂等：重复执行只会覆盖 assets/tray.ico。
"""

from __future__ import annotations

import math
import struct
import sys
from collections.abc import Sequence
from pathlib import Path

# ---------------------------------------------------------------------------
# 配色与尺寸（与后续宠物形象保持同一暖橘色系）
# ---------------------------------------------------------------------------
FILL = (0xFA, 0xC7, 0x75, 0xFF)  # #FAC775 暖橘填充
STROKE = (0xBA, 0x75, 0x17, 0xFF)  # #BA7517 深橘描边（眼睛同色）
INNER_EAR = (0xFD, 0xE7, 0xD0, 0xFF)  # #FDE7D0 浅色内耳，只在 ≥24px 时画
NOSE = (0xD4, 0x53, 0x7E, 0xFF)  # #D4537E 粉色鼻子：唯一一笔跳出暖橘色系，用来「点题」

ICON_SIZES: tuple[int, ...] = (16, 32, 48, 64, 256)

# 64 是顺手多出来的一个尺寸：Inno Setup 的 SetupIconFile 文档建议图标至少含
# 16/32/48/64/256，installer.iss 直接用本文件当安装器图标，少一个 64 会让
# 安装向导在 125%/150% 缩放下挑不到合适的图。

# 每个尺寸内部按 8 倍画布渲染再缩回，等效抗锯齿（Pillow 没有原生 AA）
SUPERSAMPLE = 8

# 描边宽度占图标边长的比例。小尺寸下会被抬到至少 1px（见 _stroke_width）。
STROKE_RATIO = 0.065

# --- 形状几何，全部用 0..1 的单位坐标描述，与像素尺寸解耦 ---
# 头：横向略扁的椭圆，宽:高 = 1 : 0.92。
# 正圆会显得「像熊猫/像熊」，扁一点才有猫那种宽脸的感觉。
HEAD_CENTER = (0.5, 0.60)
HEAD_RX = 0.385
HEAD_RY = 0.354  # = HEAD_RX × 0.92

# 耳朵：**宽基三角**，这是「猫」和「兔子」的分水岭。
#   基部宽 = 2·RX·sin(19.5°) ≈ 0.256 ≈ 头宽的 1/3
#   高度   = 0.231 ≈ 头高的 1/3
#   两耳内根相距 0.067 ≈ 头宽的 8.7%（只留一条窄缝）
# 三个点这样定：内耳根在椭圆 85°、外耳根在 124°（都以椭圆参数角算，落在轮廓上），
# 顶点在两点连线中点再往「上偏外 12°」方向推一个耳高。
# 关键约束：内耳根几乎贴着椭圆顶点（y=0.247 vs 头顶 0.246），
# 所以两耳之间的缺口底就是头顶本身，不会像上一版那样切出一条深槽。
# 上一版耳朵窄而长、缺口直切头顶，视觉上就是兔耳 —— 那版已废弃。
LEFT_EAR = ((0.360, 0.046), (0.5336, 0.2473), (0.2847, 0.3065))  # 顶点 / 内耳根 / 外耳根
RIGHT_EAR = ((0.640, 0.046), (0.4664, 0.2473), (0.7153, 0.3065))

# 内耳：把耳朵三角形按重心缩到 55%，整块都落在椭圆轮廓之外，不需要额外裁剪。
INNER_EAR_SCALE = 0.55
# 16px 下内耳只有 1~2 个像素，画上去就是一团脏点，所以小尺寸直接省略。
INNER_EAR_MIN_SIZE = 24

# 眼睛：两个实心圆。间距不能太小 —— 16px 下圆心距减去两个半径不足 2px 时，
# 两只眼睛会糊成一条横线，看起来像戴了眼罩。
EYE_RADIUS = 0.075
EYE_CENTERS = ((0.345, 0.560), (0.655, 0.560))

# 鼻子：倒三角，宽约头宽 8%，中心落在两眼连线正下方 12% 头高处。
# 这一笔是 16px 下区分「猫」和「兔子」的关键 —— 只有两点眼睛的脸，
# 兔子、狐狸、猫头鹰都长这样。
NOSE_WIDTH = 0.062
NOSE_HEIGHT = 0.048
NOSE_CENTER_Y = 0.645  # = 眼线 0.560 + 12% × 头高 0.708

# 缺少 Pillow 时的提示（用中文说清楚该怎么做，而不是丢一个 ImportError）
_MISSING_PILLOW_HINT = """\
[错误] 当前 Python 解释器没有安装 Pillow，无法生成图标。

请用带 Pillow 的解释器执行本脚本，例如：
    "C:/Users/12507/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe" tools/make_tray_icon.py

或者给任意解释器装上 Pillow（注意：不要装进项目 .venv，
程序运行时并不需要 Pillow）：
    <某个python.exe> -m pip install pillow

如果你确认上面这个解释器就是目标环境，也可以直接：
    "C:/Users/12507/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe" -m pip install pillow
"""


def _project_root() -> Path:
    """项目根目录（本文件位于 <项目根>/tools/make_tray_icon.py）。"""
    return Path(__file__).resolve().parent.parent


def _stroke_width(size: int) -> int:
    """描边宽度（像素，作用于该尺寸的画布）。

    小尺寸按比例算出来会小于 1px，直接画就消失了，所以至少抬到 1px；
    16px 的图标描边 1px 已经占掉 1/16，再粗就糊成一团。
    """
    return max(1, round(size * STROKE_RATIO))


def _scaled_point(
    point: tuple[float, float], origin: float, span: float
) -> tuple[float, float]:
    """单位坐标 -> 画布坐标。

    :param origin: 绘制区左上角（像素）
    :param span: 绘制区边长（像素）
    """
    return (origin + point[0] * span, origin + point[1] * span)


def _head_box(
    origin: float, span: float, grow: float = 0.0
) -> tuple[float, float, float, float]:
    """头部的椭圆外接矩形；grow > 0 时向外扩，用来画外描边。"""
    cx, cy = _scaled_point(HEAD_CENTER, origin, span)
    rx = HEAD_RX * span + grow
    ry = HEAD_RY * span + grow
    return (cx - rx, cy - ry, cx + rx, cy + ry)


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


def _triangle_incenter(
    points: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    """三角形内心（按对边长度加权）。"""
    (ax, ay), (bx, by), (cx, cy) = points
    a = math.hypot(bx - cx, by - cy)  # 与 A 相对的边长
    b = math.hypot(ax - cx, ay - cy)
    c = math.hypot(ax - bx, ay - by)
    total = a + b + c
    if total == 0:
        return (ax, ay)
    return ((a * ax + b * bx + c * cx) / total, (a * ay + b * by + c * cy) / total)


def _grow_triangle(
    points: Sequence[tuple[float, float]], amount: float
) -> list[tuple[float, float]]:
    """把三角形三条边同时向外平移 amount，得到描边用的放大三角。

    为什么用「以内切心缩放」而不是「画粗线」：
        粗线（draw.line + joint="curve"）会把顶点磨成圆角 —— 耳尖在 48px 下
        本来就只有 2~3px，一磨就变成圆包，整只猫立刻像「气球上顶了两个包」。
        三角形有一个很好的性质：三条边等距外扩，等价于以内切心为中心缩放
        (r + amount) / r，其中 r 是内切圆半径。这样得到的是精确的等距外扩，
        而且顶点保持尖锐。
    """
    (ax, ay), (bx, by), (cx, cy) = points
    area = abs((bx - ax) * (cy - ay) - (cx - ax) * (by - ay)) / 2.0
    perimeter = (
        math.hypot(bx - cx, by - cy)
        + math.hypot(ax - cx, ay - cy)
        + math.hypot(ax - bx, ay - by)
    )
    if area <= 0 or perimeter <= 0:
        return list(points)

    inradius = 2.0 * area / perimeter
    center_x, center_y = _triangle_incenter(points)
    scale = (inradius + amount) / inradius
    return [
        (center_x + (x - center_x) * scale, center_y + (y - center_y) * scale)
        for x, y in points
    ]


def _draw_ear_outline(draw, points, color, width: float) -> None:
    """画耳朵的外描边：一条精确外扩 width 的实心三角。

    外扩部分随后会被第 2 遍的填充盖掉内侧，只剩外圈 width 宽的一圈描边。
    """
    draw.polygon(_grow_triangle(points, width), fill=color)


def render_cat(size: int) -> "Image.Image":  # noqa: F821 (Pillow 延迟导入)
    """渲染单个尺寸的猫头图标。

    绘制顺序（每一层的存在理由）：
        第 1 遍：用描边色画放大版（头 + 耳朵），得到一圈略粗的实心剪影；
        第 2 遍：用填充色画原尺寸（耳朵 + 头），把内部接缝全部盖掉，
                 只剩最外圈 lw 宽的描边。头放在耳朵之后画，耳根就被头盖住了，
                 不会出现「贴片式」的接缝。
        第 3 遍：内耳（仅 ≥24px）、眼睛、鼻子 —— 都是细节，最后画在最上层。

    如果反过来「先画头再画耳朵、各自描边」，头部的椭圆轮廓会在耳根处横切一刀，
    看起来像「耳朵贴在一个球上」而不是一个整体。

    :param size: 目标边长（像素）
    """
    from PIL import Image, ImageDraw

    canvas = size * SUPERSAMPLE
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    width = _stroke_width(size) * SUPERSAMPLE

    # 安全内边距：描边是**向外**扩的，如果按 0..1 铺满画布，耳朵尖会被画布
    # 边缘切平（16px 下尤其明显，尖角直接变方）。这里把绘制区按描边宽度整体
    # 内缩一圈，保证任何尺寸下最外沿都留得住。
    origin = float(width)
    span = float(canvas - 2 * width)

    ear_units = (LEFT_EAR, RIGHT_EAR)
    ears = [[_scaled_point(p, origin, span) for p in tri] for tri in ear_units]
    eyes = [_scaled_point(center, origin, span) for center in EYE_CENTERS]

    # 第 1 遍：放大版剪影，全部用描边色
    draw.ellipse(_head_box(origin, span, grow=width), fill=STROKE)
    for points in ears:
        _draw_ear_outline(draw, points, STROKE, width)

    # 第 2 遍：原尺寸填充，盖掉内部接缝（先耳朵后头，让头压住耳根）
    for points in ears:
        draw.polygon(points, fill=FILL)
    draw.ellipse(_head_box(origin, span), fill=FILL)

    # 第 3 遍：细节。内耳在 16px 下只有 1~2px，画了就是脏点，所以按尺寸省略。
    if size >= INNER_EAR_MIN_SIZE:
        for tri in ear_units:
            inner = [
                _scaled_point(p, origin, span)
                for p in _shrink_triangle(tri, INNER_EAR_SCALE)
            ]
            draw.polygon(inner, fill=INNER_EAR)

    # 眼睛最后画（在最上层，不会被第 2 遍覆盖）
    eye_r = EYE_RADIUS * span
    for cx, cy in eyes:
        draw.ellipse((cx - eye_r, cy - eye_r, cx + eye_r, cy + eye_r), fill=STROKE)

    # 鼻子：倒三角。眼睛只有两个点时，兔/狐/猫头鹰都长一个样，这一笔才是「猫」。
    nose_top = _scaled_point((0.5, NOSE_CENTER_Y - NOSE_HEIGHT / 2.0), origin, span)
    nose_bottom = _scaled_point((0.5, NOSE_CENTER_Y + NOSE_HEIGHT / 2.0), origin, span)
    nose_half = NOSE_WIDTH / 2.0 * span
    draw.polygon(
        [
            (nose_top[0] - nose_half, nose_top[1]),
            (nose_top[0] + nose_half, nose_top[1]),
            (nose_bottom[0], nose_bottom[1]),
        ],
        fill=NOSE,
    )

    # 缩回目标尺寸：超采样画布 -> 真尺寸，等效抗锯齿
    resample = getattr(Image, "Resampling", Image).LANCZOS
    return image.resize((size, size), resample)


def _png_bytes(image: "Image.Image") -> bytes:  # noqa: F821 (Pillow 延迟导入)
    """把一张图编码成 PNG 字节流（ICO 允许每帧用 PNG 压缩，Vista 起支持）。"""
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def write_ico(path: Path, images: list["Image.Image"]) -> None:  # noqa: F821
    """把多张不同尺寸的图拼成一个多尺寸 .ico 文件。

    为什么不直接用 Pillow 的 `image.save(..., sizes=[...])`：
    它只会拿一张图反复降采样，16px 那一帧是「256 缩下来的糊图」，
    而不是为 16px 单独设计过的形状。这里每帧都是原生渲染的，必须自己拼。

    ICO 文件结构（ICONDIR + N × ICONDIRENTRY + 数据区）：
        0..5   ICONDIR：保留 0 / 类型 1(图标) / 帧数
        6..21  ICONDIRENTRY（每帧 16 字节）
        之后   各帧数据依次排列

    :param path: 输出路径
    :param images: 各尺寸图像，顺序即写入顺序
    """
    frames = [(image.width, _png_bytes(image)) for image in images]

    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)  # 数据区起点
    entries = b""
    payload = b""

    for size, data in frames:
        # 宽/高各占 1 字节，所以 256 只能写 0（这是 ICO 格式的历史遗留约定）
        dimension = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII",
            dimension,  # 宽
            dimension,  # 高
            0,  # 调色板颜色数（真彩图为 0）
            0,  # 保留
            1,  # 颜色平面数
            32,  # 每像素位数
            len(data),  # 该帧数据长度
            offset,  # 该帧数据偏移
        )
        offset += len(data)
        payload += data

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + payload)


def build_tray_icon(output: Path | None = None) -> Path:
    """生成 assets/tray.ico 并返回其路径（幂等，重复调用覆盖）。

    :param output: 输出路径；默认 <项目根>/assets/tray.ico
    """
    target = _project_root() / "assets" / "tray.ico" if output is None else output
    images = [render_cat(size) for size in ICON_SIZES]
    write_ico(target, images)
    return target


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    try:
        import PIL  # noqa: F401  # 仅用于探测依赖是否就绪
    except ImportError:
        sys.stderr.write(_MISSING_PILLOW_HINT)
        return 2

    args = sys.argv[1:] if argv is None else argv
    output = Path(args[0]) if args else None

    target = build_tray_icon(output)
    print(f"[OK] 已生成托盘图标：{target}")
    print(f"     尺寸：{', '.join(f'{s}x{s}' for s in ICON_SIZES)}")
    print(f"     大小：{target.stat().st_size} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
