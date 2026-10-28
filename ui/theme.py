"""悬浮窗的配色、字号、圆角、间距常量。

集中放在这里，换主题时只改这一个文件。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtGui import QColor, QFont, QFontDatabase

# ---------------------------------------------------------------------------
# 卡片
# ---------------------------------------------------------------------------

# 卡片背景：深灰半透明（220/255 的不透明度，能透出一点桌面壁纸又不影响读数）
CARD_BG = QColor(32, 32, 36, 220)
# 一层很淡的描边，让卡片在浅色壁纸上也有轮廓
CARD_BORDER = QColor(255, 255, 255, 34)
CARD_BORDER_WIDTH = 1.0
CARD_RADIUS = 14.0

# ---------------------------------------------------------------------------
# 文字
# ---------------------------------------------------------------------------

TEXT_PRIMARY = QColor(255, 255, 255, 235)    # 倒计时数值（主）
TEXT_SECONDARY = QColor(255, 255, 255, 145)  # 事件名（次要）
TEXT_ACCENT = QColor(255, 138, 128)          # 「已到点」高亮

# 字号（pt）：名称小、时间大
FONT_SIZE_NAME = 9
FONT_SIZE_TIME = 14
FONT_SIZE_HINT = 9

# ---------------------------------------------------------------------------
# 间距与尺寸
# ---------------------------------------------------------------------------

PADDING_H = 16       # 卡片左右内边距
PADDING_V = 12       # 卡片上下内边距
ROW_SPACING = 7      # 行间距
COLUMN_SPACING = 26  # 名称与时间之间的最小间距（时间最长的那一行靠它撑开呼吸感）
MIN_WIDTH = 170      # 卡片最小宽度
MAX_NAME_WIDTH = 150 # 事件名最长显示宽度（超出省略号）

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
