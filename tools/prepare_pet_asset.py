"""把 AI 生成的 body.png 处理成可直接用的宠物素材。

原图有两个必须处理的问题：
  1. **背景是纯白，不是透明** —— 直接用会在桌面上出现一块白方块。
  2. **右下角有「即梦AI」水印** —— 分享出去不合适。

抠图策略：从四个角做 flood fill（容差 30），而不是「全局白色转透明」。
原因：猫的胸毛、胡须本身就是白色，全局转透明会在肚子上开洞；
从边缘扩散则会被猫的黑色描边挡住，只吃掉外部背景。
水印是浅灰（约 235）叠在白底（254）上，色差 19 < 容差 30，会被一并吞掉。

代价：伸出描边之外的**白色胡须会被吃掉**。150–200px 的实际显示尺寸下
几乎不可见，可以接受；若素材日后换成官方透明底图，本脚本会自动跳过抠图。

用法（必须用带 Pillow 的解释器）：
    "C:/Users/12507/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe" tools/prepare_pet_asset.py
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:
    print("[错误] 当前解释器没有 Pillow。请用：")
    print('  "C:/Users/12507/.workbuddy-ai/binaries/python/envs/default/Scripts/python.exe" tools/prepare_pet_asset.py')
    sys.exit(2)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# 源图放在 assets/pet/source/ 下：它是 2048×2048 的中间产物，不属于运行时资源，
# 留在项目根会让人误以为程序要读它（程序只读 assets/pet/body.png）。
SRC = PROJECT_ROOT / "assets" / "pet" / "source" / "body.png"
OUT_DIR = PROJECT_ROOT / "assets" / "pet"
OUT_BODY = OUT_DIR / "body.png"
PREVIEW = PROJECT_ROOT / "assets" / "pet" / "_preview_checker.png"

# flood fill 的标记色。选品红是因为原图里绝对不存在这个颜色，
# 用它做「已抠掉」的标记不会和素材本身的任何像素混淆。
MAGIC = (255, 0, 255)
# 容差：白底 254 与水印浅灰 235 差 19，取 30 能把水印一起吞掉，
# 又不至于吃到猫身上的浅奶油色（(253,224,194)，与白底差 60）。
THRESH = 30
# 内容裁剪后额外留的透明边距（占长边比例），给呼吸/缩放动画留余量，
# 避免放大时图像被窗口边缘切掉。
PADDING_RATIO = 0.04

# 水印切除线（占高度比例）。实测原图：猫本体最低到 y=1740（0.85H），
# 「即梦AI」水印在 y=1860..1980（0.91H..0.97H），中间有 120px 空隙。
# 取 0.87 落在空隙正中，两边各留约 40px 余量。
# 如果日后换了没有水印的素材，这条线仍然安全 —— 它只切掉图像最底部的空白带。
WATERMARK_CUT_RATIO = 0.87


def has_alpha(im: Image.Image) -> bool:
    """原图是否已经带可用的透明通道（已经是透明底就跳过抠图）。"""
    if im.mode not in ("RGBA", "LA"):
        return False
    alpha = im.convert("RGBA").getchannel("A")
    low, high = alpha.getextrema()
    return low < 250  # 存在真正透明的像素


def cut_watermark(im: Image.Image) -> Image.Image:
    """切掉图片底部的水印带。

    为什么用「切」而不是「擦」：水印是半透明浅灰，用修补算法擦除会留下
    一圈灰雾，反而更明显。实测水印与猫本体之间有 120px 纯背景空隙，
    直接从空隙处截断最干净。
    """
    width, height = im.size
    keep = int(height * WATERMARK_CUT_RATIO)
    if keep >= height:
        return im
    print(f"[去水印] 截掉底部 {height - keep}px（水印带）")
    return im.crop((0, 0, width, keep))


def cut_background(im: Image.Image) -> Image.Image:
    """从四角 flood fill 抠掉外部背景，返回 RGBA。"""
    rgb = im.convert("RGB")
    width, height = rgb.size
    work = rgb.copy()

    corners = [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]
    for point in corners:
        try:
            ImageDraw.floodfill(work, point, MAGIC, thresh=THRESH)
        except Exception as exc:  # 单点失败不影响其它角
            print(f"[警告] 从 {point} 抠图失败：{exc}")

    rgba = rgb.convert("RGBA")
    # 用 numpy 批量置透明（4M 像素用 Python 循环要十几秒）
    try:
        import numpy as np

        marked = np.array(work)
        mask = (
            (marked[:, :, 0] == MAGIC[0])
            & (marked[:, :, 1] == MAGIC[1])
            & (marked[:, :, 2] == MAGIC[2])
        )
        arr = np.array(rgba)
        arr[:, :, 3] = np.where(mask, 0, 255)
        result = Image.fromarray(arr, "RGBA")
        print(f"[抠图] numpy 批量处理，标记为背景的像素 {int(mask.sum())}")
    except ImportError:
        print("[抠图] 未装 numpy，回退逐像素处理（较慢）")
        px = rgba.load()
        marked = work.load()
        for y in range(height):
            for x in range(width):
                if marked[x, y] == MAGIC:
                    r, g, b, _ = px[x, y]
                    px[x, y] = (r, g, b, 0)
        result = rgba

    return result


def trim_and_pad(im: Image.Image) -> Image.Image:
    """裁到内容外接框，再补一圈透明边距。"""
    bbox = im.getbbox()  # 基于 alpha 通道，透明部分不计入
    if bbox is None:
        raise RuntimeError("抠图后整张图都是透明的 —— 容差或标记色有问题")
    trimmed = im.crop(bbox)

    long_side = max(trimmed.size)
    pad = int(long_side * PADDING_RATIO)
    canvas = Image.new("RGBA", (trimmed.width + pad * 2, trimmed.height + pad * 2), (0, 0, 0, 0))
    canvas.paste(trimmed, (pad, pad))
    return canvas


def make_preview(im: Image.Image, size: int = 420) -> Image.Image:
    """把透明图叠在棋盘格上，方便肉眼判断抠图边缘是否干净。"""
    board = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    draw = ImageDraw.Draw(board)
    cell = 20
    for y in range(0, size, cell):
        for x in range(0, size, cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle([x, y, x + cell - 1, y + cell - 1], fill=(226, 226, 226, 255))

    thumb = im.copy()
    thumb.thumbnail((size - 40, size - 40), Image.LANCZOS)
    board.paste(thumb, ((size - thumb.width) // 2, (size - thumb.height) // 2), thumb)
    return board.convert("RGB")


def main() -> int:
    if not SRC.is_file():
        print(f"[错误] 找不到源图：{SRC}")
        return 1

    im = Image.open(SRC)
    print(f"[读取] {SRC.name} {im.size} mode={im.mode}")

    if has_alpha(im):
        print("[跳过] 原图已带透明通道，直接裁剪与缩放")
        processed = im.convert("RGBA")
    else:
        print("[处理] 原图为不透明 RGB，开始抠背景（含去除右下角水印）")
        processed = cut_background(cut_watermark(im))

    processed = trim_and_pad(processed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    processed.save(OUT_BODY)
    print(f"[输出] {OUT_BODY.relative_to(PROJECT_ROOT)} {processed.size}")

    make_preview(processed).save(PREVIEW)
    print(f"[预览] {PREVIEW.relative_to(PROJECT_ROOT)}")

    # 顺便报一下猫的几何比例，供界面层算「卡片该贴在哪」
    width, height = processed.size
    print(f"[比例] 宽高比 {width / height:.3f}（1.0 = 正方形）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
