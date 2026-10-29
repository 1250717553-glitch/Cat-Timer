"""窗口摆放的纯逻辑 —— 不 import PySide6。

为什么放在 core 层：
    「窗口该往哪边翻」是一条判断规则，不是界面代码。抽成纯函数就能用普通
    单元测试覆盖边界（正好压线、屏幕宽度为 0、屏幕在副屏的负坐标区），
    不用起 QApplication，也不会因为屏幕分辨率不同而测不出来。

坐标系约定：
    全部是**屏幕全局坐标**（和 QScreen.availableGeometry() 一致）。
    副屏在主屏左侧时 x 可以是负数，函数按「左边界 + 宽度」算中线，
    所以负坐标天然成立，不需要特殊处理。
"""

from __future__ import annotations


def should_flip(window_center_x: float, screen_left: float, screen_width: float) -> bool:
    """窗口中心越过所在屏幕的中线时返回 True（猫在右、卡片向左展开）。

    为什么按「中线」而不是「窗口是否已经出界」：
        卡片贴着窗口的哪一侧，决定它往哪个方向展开。窗口已经在右半边时，
        卡片若还向右展开就更容易被屏幕右边缘裁掉。按中线翻一次最简单，
        也符合「卡片永远朝屏幕内侧展开」的直觉。

    正好压在中线时返回 False：
        这是「平局」的约定值。重点不是选哪边，而是**确定** —— 不能一会儿
        翻一会儿不翻，否则窗口停在中线附近时会来回跳。

    :param window_center_x: 窗口中心的屏幕横坐标
    :param screen_left: 所在屏幕可用区的左边界（副屏可能为负）
    :param screen_width: 所在屏幕可用区的宽度
    :return: True 表示应该翻转（猫在右、卡片向左）
    """
    if screen_width <= 0:
        # 屏幕信息还没就绪（或 API 返回异常值）时保持默认朝向，
        # 不要拿一个无效的宽度去算中线。
        return False
    return window_center_x > screen_left + screen_width / 2.0
