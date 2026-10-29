"""悬浮窗的配色、字号、圆角、间距常量。

集中放在这里，换主题时只改这一个文件。
"""

from __future__ import annotations

import math
import zlib
from typing import Optional

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFont, QFontDatabase

# ---------------------------------------------------------------------------
# 卡片
# ---------------------------------------------------------------------------

# 卡片背景：奶油白半透明（235/255，能透出一点壁纸又不影响读数）。
# 换掉原来的深灰是因为深色卡片配橘猫显得「凶」，奶油白才和猫的暖橘是同一套语言。
CARD_BG = QColor(252, 248, 242, 235)
# 描边：深棕的低透明度。原来那层白色描边在浅底上完全看不见，
# 卡片会在浅色壁纸上糊掉 —— 换配色时必须一起换这条。
CARD_BORDER = QColor("#D8CFC4")  # 实色描边：低透明度在浅底上等于没有，改实色才看得见
CARD_BORDER_WIDTH = 1.0
CARD_RADIUS = 12.0

# ---------------------------------------------------------------------------
# 文字
# ---------------------------------------------------------------------------

TEXT_PRIMARY = QColor(60, 48, 40)    # 倒计时数值（主）—— 深棕，浅底上的高对比
TEXT_SECONDARY = QColor(120, 105, 96)  # 事件名（次要）
# 「已到点」高亮：原来的珊瑚粉在奶油白上对比度不够，换成偏红的陶土色
TEXT_ACCENT = QColor(216, 90, 48)

# 字号（pt）：名称小、时间大
FONT_SIZE_NAME = 12   # 9 太小，事件名在 150% 缩放下几乎读不出
FONT_SIZE_TIME = 16
FONT_SIZE_HINT = 9

# ---------------------------------------------------------------------------
# 间距与尺寸
# ---------------------------------------------------------------------------

PADDING_H = 16       # 卡片左右内边距
PADDING_V = 12       # 卡片上下内边距
# 行间距：7 太挤（12pt 名称与 16pt 时间贴在一起）；但 10 会被 28px 的毛线球
# 吃掉 —— 球占位比文字行高（约 20px）高 8px，两行之间只剩 2px 空隙，看起来挤。
# 取 14：给球的溢出留出余量，同时保持行与行之间有呼吸感。
ROW_SPACING = 14
# 列间距。6 在球 44px 时合适；球涨到 52px 后 6 显得贴，取 14。
# 这个值作用于所有相邻列 —— 时间列靠弹簧推到最右，不受影响。
COLUMN_SPACING = 14
MIN_WIDTH = 170      # 卡片最小宽度
MAX_NAME_WIDTH = 150 # 事件名最长显示宽度（超出省略号）
# 行高从 28 涨到 44（球变大），5 行会到 250px —— 太高了。
# 降到 4：约 200px，和屏幕右下角原来那块地方接近。超出显示「还有 N 项」。
MAX_ROWS = 4
SUFFIX_WIDTH = 44    # 「3/5」后缀固定宽度：计数变化（3→10）时不让整窗抖宽

# ---------------------------------------------------------------------------
# 字体
# ---------------------------------------------------------------------------

# 优先使用的中文字体（按顺序取第一个系统里存在的）。
# 不指定的话，某些环境下中文会走不到合适的字体回退。
_PREFERRED_FAMILIES = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "PingFang SC",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "SimHei",
)

_resolved_family: Optional[str] = None


def font_family() -> str:
    """返回一个可用的中文字体名；都不可用时返回空串（跟随系统默认）。

    结果只解析一次并缓存。注意必须在 QApplication 创建之后调用。
    """
    global _resolved_family
    if _resolved_family is None:
        try:
            available = set(QFontDatabase.families())
        except Exception:  # pragma: no cover - 极端环境下退回默认字体
            available = set()
        _resolved_family = next((f for f in _PREFERRED_FAMILIES if f in available), "")
    return _resolved_family


def make_font(point_size: int, bold: bool = False) -> QFont:
    """按主题字号造一个字体对象。"""
    font = QFont()
    family = font_family()
    if family:
        font.setFamily(family)
    font.setPointSize(point_size)
    font.setBold(bold)
    return font



# ---------------------------------------------------------------------------
# 毛线球（每个事件行左侧的圆形进度指示）
# ---------------------------------------------------------------------------

# 为什么用 QPainter 画而不是用 PNG 素材：
#   1) 20~24px 下任何位图缩放都会糊，矢量在任何 DPR 下都清晰；
#   2) 颜色要按事件动态变化（六套配色），位图得准备好几份；
#   3) 素材要进 build.spec、要处理打包路径、要防缺失 —— 画出来全省了。
# 直径 24 -> 28：24px 下外圈环 + 球体 + 内部弧三者挤在一起，
# 实测缩到真实尺寸会糊成「蚊香 / 靶心」，认不出是球。
# 28 -> 44：球内要写事件名（最多 2 字），28px 下 10pt 字挤不下。
# 44px 球内可用宽度约 32px，两个字 + 左右各 4px 呼吸刚好。
# 44 -> 52：44px 下球内两个字仍显局促，52px 让球体和文字都有余量。
# 行高跟着变成 52，4 行卡片约 280px。
BALL_SIZE = 52            # 占位直径（含外圈弧）
BALL_RING_RADIUS = 26     # 进度弧半径
BALL_RING_WIDTH = 3       # 进度弧线宽
BALL_CORE_RADIUS = 19     # 球体半径，给文字留出 38px 宽度
BALL_LABEL_FONT = 10      # 球大了，字号跟着回到 10（8pt 在 38px 球体里偏小）
BALL_GAP = 8              # 球与文字的间距
# 折叠态：球绕猫散落。窗口要放大到 180x190 才装得下半径 70 的环，
# 但同时不能太大 —— 半径 82 才能填满 200 宽，那样球离猫太远，
# 看起来像「一圈卫星」而不是「散落在猫身上」。
# 球 28 -> 44 之后，半径 70 的环装不下 44px 的球（环外沿 70+22=92 > 90 半宽），
# 窗口必须放大。取 240x250：环 88 + 球半径 22 + 内边距，四周留白约 8px。
COLLAPSED_WIDTH = 240
COLLAPSED_HEIGHT = 250
COLLAPSED_BALL_RADIUS = 88.0
# 进度弧的底槽。所有球统一用这个中性灰褐，**不随球体颜色变**：
# 橘球的底槽做成浅橘、粉球做成浅粉会让整列看起来是六个不同的控件，
# 而浅色系的底槽在奶油白卡片上仍然会糊。
# 加深一档（原 #F1E9DF）之后，60% 的弧读起来是「填到 7 点」而不是「环缺了 40%」。
BALL_TRACK = QColor("#DCD2C5")
# 分段进度环的底槽。比 BALL_TRACK 更淡：#DCD2C5 在 3px 线宽下会和深色段争视觉，
# 而底槽只需要「提示这里有进度环」，不需要被看清。
BALL_TRACK_LIGHT = QColor("#EFE7DC")
# 一次性事件的虚线占位圆。E8DDD0 -> D3D1C7 -> B4B2A9，连续加深两次：
# 前两版在奶油白卡片上几乎看不见，用户会以为「这几个事件没有球」，
# 而不是「一次性事件用虚线占位」。
BALL_IDLE_STROKE = QColor("#B4B2A9")

# 配色表：(球体填充, 描边/进度弧)。
#
# 以暖色为主调 —— 这张卡片和橘猫是同一套语言，冷色球每出现一次都在破坏统一感。
# 但色相要拉开，否则六个事件同屏时整片橙黄一片糊。
# 色相跨度：黄 -> 橘 -> 珊瑚 -> 玫红 -> 绿 -> 暖灰 -> 紫。
#
# 为什么去掉原来的青和蓝：它们最冷，且在奶油白卡片上对比过硬；
# 新增珊瑚补足暖色区的色相跨度，新增暖灰给「常驻但不重要」的事件一个低调选项。
# 浅紫保留：紫配暖橘不突兀，是唯一的冷色，事件多时提供最后一级区分。
BALL_PALETTE: tuple[tuple[QColor, QColor], ...] = (
    (QColor("#FAC775"), QColor("#BA7517")),   # 1 橘   —— 主色，和猫同系
    (QColor("#F5C4B3"), QColor("#D85A30")),   # 2 珊瑚 —— 偏红，和橘拉开
    (QColor("#F4C0D1"), QColor("#D4537E")),   # 3 粉   —— 偏玫红
    (QColor("#C0DD97"), QColor("#3B6D11")),   # 4 黄绿 —— 暖调绿，和橘互补
    (QColor("#D8CFC4"), QColor("#8A7B6E")),   # 5 暖灰 —— 中性低调，不抢眼
    # 焦糖：和珊瑚同色相区（约 15-30 度），但明度明显低一档。
    # 明度差在 24px 小块面积上比色相差更好分辨 —— 浅紫虽然和橘互补，
    # 但它是这张卡片上唯一的冷色，每出现一次都在破坏「卡片和猫是一套」的统一感。
    (QColor("#D9A87E"), QColor("#96622F")),   # 6 焦糖 —— 暖色，明度最低
)




# 折叠态球环绕的扰动幅度（度 / 像素）。
# 原来的 ±12 度 / ±10px 太规整，四个球正好落在上下左右，看起来像十字靶。
# 加大到 ±25 度 / ±18px 才有「散落」的感觉 —— 但边界必须靠测试校验，不靠手算。
# 实测取值：角度扰动是主要约束。
#   角度±25 半径±18 -> 最坏间距 29.6（重叠）
#   角度±18 半径±12 -> 42.8（仍不够）
#   角度±11 半径±18 -> 58.6（安全，且散落感明显）
# 不能只看「4 个球」——n=6/8 时相邻球更容易挤，必须 2~8 全扫。
ANGLE_JITTER = 11.0
RADIUS_JITTER = 18.0


def collapsed_ball_positions(
    event_ids: list[str], center: QPointF, base_radius: float = 70.0
) -> list[QPointF]:
    """折叠态下球绕猫散落的位置。

    两件事同时要满足：
        1) **不重叠** —— 重叠的球会遮住进度弧，那是信息，不能丢；
        2) **看起来不规整** —— 严格等分角度会像「一圈卫星」，不像「散落」。

    所以角度按 360/n 等分保证不撞，再叠加哈希扰动打散规律感：
    角度 ±ANGLE_JITTER、半径 ±RADIUS_JITTER，让球忽远忽近、不再排成十字。

    安全边界：相邻两球最小角间隔 = (360/n) - 2*ANGLE_JITTER。
    n=8 时是 45 - 50 = -5（**会反向越过**），但半径也不同，实际距离仍需校验 ——
    所以「不重叠」必须靠 test_positions_do_not_overlap 遍历 2~8 球实测，
    手算只覆盖个例，挡不住边界情况。

    代价：增删一个事件时所有球都会重排（角度是相对当前球数算的）。
    这是为「不重叠」付的账，值得 —— 球重叠是无法接受的，位置跳动只是观感问题。

    :param event_ids: 事件 id 列表（顺序即绘制顺序）
    :param center: 环绕中心（折叠态窗口的中心）
    :param base_radius: 基础半径；窗口 240x250 下取 88 能贴着猫的轮廓
    """
    n = len(event_ids)
    if n == 0:
        return []

    angle_span = int(ANGLE_JITTER * 2) + 1
    radius_span = int(RADIUS_JITTER * 2) + 1

    result: list[QPointF] = []
    for index, event_id in enumerate(event_ids):
        h = zlib.crc32(event_id.encode("utf-8"))
        angle = 360.0 * index / n + (h % angle_span - ANGLE_JITTER)
        radius = base_radius + ((h >> 8) % radius_span - RADIUS_JITTER)
        rad = math.radians(angle - 90)  # -90：从正上方（12 点）开始，符合视觉习惯
        result.append(
            QPointF(
                center.x() + radius * math.cos(rad),
                center.y() + radius * math.sin(rad),
            )
        )
    return result




def ball_label(event) -> str:
    """球内显示的事件名，最多 2 个字。

    为什么截到 2 个字：44px 球内可用宽度约 32px，10pt 字号下最多容 3 个汉字；
    取 2 个能保证左右各留约 4px 呼吸，不贴球体边缘。

    空名字返回问号：留空会让球里一片空白，用户会以为是渲染失败。
    """
    name = (event.name or "").strip()
    if not name:
        return "?"
    return name[:2]


def ball_colors(event_id: str) -> tuple[QColor, QColor]:
    """按事件 id 稳定分配毛线球配色。

    必须用 zlib.crc32，不能用内置 hash()：Python 3 对字符串 hash 加了随机盐
    （PYTHONHASHSEED），每次进程启动结果都不同 —— 用户会发现「重启后所有球的
    颜色全变了」，而且查不出原因。crc32 是跨进程稳定的。
    """
    index = zlib.crc32(event_id.encode("utf-8")) % len(BALL_PALETTE)
    return BALL_PALETTE[index]


def distinct_ball_indices(event_ids: list[str]) -> dict[str, int]:
    """给一批事件分配互不相同的配色下标（同一屏内颜色不撞）。

    纯 crc32 分配会撞色（事件多了必然重复）。这里在保留「按 id 稳定」的前提下
    做一次冲突消解：按出现顺序逐个取下一个空位；只有事件数超过调色板容量
    （6 个以上）才允许重复。

    为什么仍然以 crc32 为起点、而不是直接按顺序 0,1,2...：
        用户删掉中间某个事件后，剩下的球颜色不该整排挪位。以 id 哈希为基准，
        增删只影响真正冲突的那几个。
    """
    used: set[int] = set()
    result: dict[str, int] = {}
    for event_id in event_ids:
        base = zlib.crc32(event_id.encode("utf-8")) % len(BALL_PALETTE)
        index = base
        if len(used) < len(BALL_PALETTE):
            for offset in range(len(BALL_PALETTE)):
                candidate = (base + offset) % len(BALL_PALETTE)
                if candidate not in used:
                    index = candidate
                    break
        used.add(index)
        result[event_id] = index
    return result
# ---------------------------------------------------------------------------
# 管理窗口（浅色 QSS）
# ---------------------------------------------------------------------------

# 这几个色只在管理窗口的样式表里用，但同样集中在这里 —— 改配色只改本文件。
# 全部从上面的主色派生：底色 = 卡片色，分隔线 = 更深的暖灰，强调 = 陶土色。
PANEL_BG = CARD_BG.name()                 # #fcf8f2 面板底色
PANEL_ALT_BG = "#F2E8DC"                  # 表格交替行：比底色略深
PANEL_LINE = "#E8DDD0"                    # 网格线 / 边框
PANEL_HEADER_BG = "#F1E9DF"               # 表头 / 按钮底色
PANEL_SELECTED_BG = "#F0E4D8"             # 选中行
PANEL_BUTTON_BORDER = "#DCCFC0"           # 按钮 / 滚动条滑块
PANEL_HEADER_TEXT = "#5F4E42"             # 表头文字
PANEL_DISABLED_TEXT = "#B3A69A"           # 禁用态文字


def main_window_qss() -> str:
    """管理窗口的浅色样式表。

    为什么要显式写死一套浅色，而不是跟随系统主题：
        Windows 深色模式下 QTableWidget 会变成一片黑，和奶油白的悬浮卡片
        完全是两套视觉；而且表格文字在深色下还会被系统 palette 改成浅色，
        与我们的浅底打架。写死之后「不管系统主题是什么，管理窗口都是浅色」。

    颜色全部取自本文件的常量，main_window.py 里不允许再出现裸色值。
    """
    return f"""
    QMainWindow, QWidget {{
        background-color: {PANEL_BG};
        color: {TEXT_PRIMARY.name()};
    }}
    QTableWidget {{
        background-color: {PANEL_BG};
        alternate-background-color: {PANEL_ALT_BG};
        gridline-color: {PANEL_LINE};
        color: {TEXT_PRIMARY.name()};
        border: 1px solid {PANEL_LINE};
        border-radius: 8px;
        selection-background-color: {PANEL_SELECTED_BG};
        selection-color: {TEXT_PRIMARY.name()};
        outline: none;
    }}
    QTableWidget::item {{
        padding: 4px 6px;
    }}
    QTableWidget::item:alternate {{
        background-color: {PANEL_ALT_BG};
    }}
    /* 选中行必须显式指定文字色：不写的话系统会把文字刷成白色，浅底上看不见 */
    QTableWidget::item:selected {{
        background-color: {PANEL_SELECTED_BG};
        color: {TEXT_PRIMARY.name()};
    }}
    QHeaderView::section {{
        background-color: {PANEL_HEADER_BG};
        color: {PANEL_HEADER_TEXT};
        border: none;
        border-bottom: 1px solid {PANEL_LINE};
        padding: 6px;
        font-weight: 600;
    }}
    QTableCornerButton::section {{
        background-color: {PANEL_HEADER_BG};
        border: none;
    }}
    QPushButton {{
        background-color: {PANEL_HEADER_BG};
        color: {TEXT_PRIMARY.name()};
        border: 1px solid {PANEL_BUTTON_BORDER};
        border-radius: 6px;
        padding: 6px 14px;
    }}
    QPushButton:hover {{
        background-color: {PANEL_SELECTED_BG};
    }}
    QPushButton:pressed {{
        background-color: #DFD0BE;
    }}
    QPushButton:disabled {{
        background-color: {PANEL_ALT_BG};
        color: {PANEL_DISABLED_TEXT};
        border-color: {PANEL_LINE};
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {PANEL_BUTTON_BORDER};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: #CBBBA8;
    }}
    /* 去掉上下箭头按钮：它们在小窗口里既丑又没用 */
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
        background: none;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    QToolTip {{
        background-color: {PANEL_HEADER_BG};
        color: {TEXT_PRIMARY.name()};
        border: 1px solid {PANEL_BUTTON_BORDER};
        padding: 4px 6px;
    }}
    """
