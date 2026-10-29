"""宠物动画的姿势计算 —— 纯逻辑，不 import PySide6。

为什么单独成文件、且不碰 Qt：
    呼吸 / 点击 / 歪头的曲线本质是「数学」，不是「界面」。放在 core 层就能用
    普通单元测试覆盖（tests/test_pet_animator.py 不需要 QApplication、不需要
    离屏平台插件），也符合项目既有分层「core 不依赖 GUI」。

为什么全是纯函数、不带状态：
    状态（动画起点、上一次鼠标位置）由调用方持有（见 ui/floating_window.py 的
    节拍器）。纯函数意味着同一个 elapsed 永远算出同一个姿势 —— 丢帧、暂停、
    窗口隐藏后再显示，都不会让动画「漂移」到奇怪的位置。

坐标系约定（贯穿整个文件）：
    偏移量的单位是**逻辑像素**，x 向右为正、y **向下**为正（与 Qt 屏幕坐标一致）。
    所以「向上浮」是负的 offset_y。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 常量：所有魔数都在这里，附上取值理由
# ---------------------------------------------------------------------------

# 呼吸周期。成年人静息呼吸 12~20 次/分钟，即 3.0~5.0s 一次。
# 取 3.2s 偏快一点：桌宠要让人感觉「活着」，太慢会以为卡住了。
BREATH_PERIOD_S = 3.2

# 呼吸时的缩放幅度。132px 高的猫，3% 约等于 ±4px 的起伏，刚好能看出来；
# 再大就变成「喘气」，5% 以上在视觉上会显得整只猫在抖。
BREATH_SCALE_AMPLITUDE = 0.03

# 呼吸时的上下浮动（逻辑像素）。只缩放不位移，看起来像原地抽搐；
# 配合 1.5px 的上下移动才像胸腔起伏。取 1.5 是因为再大就会和点击动画的
# 位移混淆，用户分不清「它在呼吸」还是「它在动」。
BREATH_OFFSET_Y_PX = 1.5

# 点击反馈总时长。短于 0.2s 人眼几乎捕捉不到；长于 0.5s 会觉得「卡了一下」。
CLICK_DURATION_S = 0.35

# 点击反馈的峰值缩放。12% 足够明显，又不至于让猫糊掉。
CLICK_SCALE_PEAK = 1.12

# 点击反馈里「放大」占的时间比例。前 55% 放大、后 45% 回落 ——
# 上升慢回落快，比对称曲线更像「被戳了一下」。
CLICK_RISE_RATIO = 0.55

# 回落段的衰减指数。越大回弹越轻。取 2.0 时最低点约为 1.0 - 1.3%，
# 也就是「轻微回弹」而不是「弹簧」。
CLICK_REBOUND_DAMPING = 2.0

# 歪头的最大角度。2.5° 是很克制的量：再多就不像「跟着你看」而像「脖子歪了」。
HEAD_TURN_MAX_DEG = 2.5

# 鼠标横向离猫中心多远时达到最大歪头角度。
# 取 160px ≈ 一个卡片宽度：鼠标扫过卡片范围就能走完整行程，手感跟得上。
HEAD_TURN_RANGE_PX = 160.0

# 歪头时附带的最大横向位移。给 2px 是为了让「转头」有一点重心移动的感觉，
# 纯旋转看起来像贴纸在原地转。
HEAD_TURN_MAX_OFFSET_PX = 2.0


@dataclass
class PetPose:
    """某一帧宠物的姿势。默认值即「静止」。

    :param scale: 缩放倍数，1.0 = 原始大小
    :param offset_x: 相对基准位置的横向偏移（逻辑像素，右为正）
    :param offset_y: 纵向偏移（逻辑像素，**下为正**）
    :param rotation: 绕头部中心的旋转角（度，顺时针为正）
    :param flipped: 是否左右镜像
    """

    scale: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    rotation: float = 0.0
    flipped: bool = False

    def is_static(self) -> bool:
        """是否与静止姿势完全一致（用来跳过不必要的重绘/断言「已回落」）。"""
        return (
            self.scale == 1.0
            and self.offset_x == 0.0
            and self.offset_y == 0.0
            and self.rotation == 0.0
        )


def breathing_pose(elapsed: float) -> PetPose:
    """呼吸：一个周期内「吸气 -> 呼气」，t=0 处于吸满状态。

    曲线用余弦而不是正弦，是为了让 t=0 就落在极值上 —— 窗口刚显示时猫立刻
    处在「吸满」的一帧，而不是从中间值慢慢爬上去（后者会显得启动卡顿）。

    缩放映射到 1.00~1.03（而不是 0.97~1.03）：不让猫缩到原始尺寸以下，
    避免缩小重采样把 1px 描边糊掉。

    :param elapsed: 从动画开始算起的秒数（可以很大，内部取模）
    """
    phase = (elapsed / BREATH_PERIOD_S) % 1.0
    wave = math.cos(2.0 * math.pi * phase)  # +1 吸满 / -1 呼尽

    # 把 -1..+1 的余弦映射到 0..1，再乘幅度：结果恒在 [1.0, 1+幅度]
    inflation = (1.0 + wave) / 2.0
    return PetPose(
        scale=1.0 + BREATH_SCALE_AMPLITUDE * inflation,
        # 吸满时略微上浮（屏幕 y 向下为正，所以取负）
        offset_y=-BREATH_OFFSET_Y_PX * wave,
    )


def click_bounce_pose(elapsed: float) -> PetPose:
    """点击反馈：0.35s 内 1.0 -> 1.12 -> 1.0，末尾带一次轻微回弹。

    分两段构造，而不是用一条阻尼正弦：
        上升段（前 55%）：sin 的 0->π/2，末端斜率为 0，与回落段平滑衔接；
        回落段（后 45%）：cos(1.5π·u) 从 1 降到 0，同时乘 (1-u)^2 的衰减，
                         中途会穿过 1.0 一次形成「回弹」，末端精确回到 1.0。
    阻尼正弦虽然更「物理」，但参数一改就要重新调曲线；两段式每段的端点
    都是解析可控的，改幅度不会牵连回弹位置。

    :param elapsed: 从点击瞬间算起的秒数
    :return: 超时或未开始（elapsed < 0）时返回静止姿势
    """
    if elapsed < 0.0 or elapsed >= CLICK_DURATION_S:
        return PetPose()

    progress = elapsed / CLICK_DURATION_S
    peak_delta = CLICK_SCALE_PEAK - 1.0

    if progress <= CLICK_RISE_RATIO:
        rise = progress / CLICK_RISE_RATIO
        scale = 1.0 + peak_delta * math.sin(0.5 * math.pi * rise)
    else:
        fall = (progress - CLICK_RISE_RATIO) / (1.0 - CLICK_RISE_RATIO)
        damping = (1.0 - fall) ** CLICK_REBOUND_DAMPING
        scale = 1.0 + peak_delta * math.cos(1.5 * math.pi * fall) * damping

    return PetPose(scale=scale)


def head_turn_pose(
    mouse_x: float,
    pet_center_x: float,
    max_deg: float = HEAD_TURN_MAX_DEG,
) -> PetPose:
    """歪头跟随：按鼠标相对猫中心的横向偏移映射成旋转角 + 横向位移。

    映射是线性的并在 HEAD_TURN_RANGE_PX 处饱和（clamp 到 ±1），而不是
    无限放大：鼠标跑到屏幕另一头时，猫不该把头转成 90°。

    :param mouse_x: 鼠标的横坐标（与 pet_center_x 同一坐标系，一般用全局坐标）
    :param pet_center_x: 猫中心的横坐标
    :param max_deg: 最大旋转角（度）
    :return: 鼠标正好在猫中心时返回静止姿势
    """
    delta = mouse_x - pet_center_x
    if delta == 0.0:
        return PetPose()

    ratio = max(-1.0, min(1.0, delta / HEAD_TURN_RANGE_PX))
    return PetPose(
        rotation=max_deg * ratio,
        offset_x=HEAD_TURN_MAX_OFFSET_PX * ratio,
    )


def blend(
    breath: PetPose,
    click: PetPose,
    head: PetPose,
    *,
    click_active: bool,
    flipped: bool = False,
) -> PetPose:
    """把三路动画合成一个最终姿势。

    叠加规则（为什么不是简单相加）：
        缩放/纵向位移：点击期间**点击动画独占**。如果让呼吸的 1.03 叠在点击的
            1.12 上会变成 1.15，且呼吸还在自己摆动，看起来像抽筋。
            「呼吸的相位继续推进但幅度置零」就是这么实现的 —— 呼吸的 elapsed
            一直在走，只是这一帧不采用它的幅度，点击结束后立刻接回当前相位，
            不会出现相位跳变。
        横向位移/旋转：来自歪头跟随，与缩放无关，任何时候都直接采用。

    :param breath: 呼吸姿势
    :param click: 点击姿势
    :param head: 歪头姿势
    :param click_active: 点击动画是否正在进行
    :param flipped: 是否镜像
    """
    if click_active:
        scale = click.scale
        offset_y = click.offset_y
    else:
        scale = breath.scale
        offset_y = breath.offset_y

    return PetPose(
        scale=scale,
        offset_x=head.offset_x,
        offset_y=offset_y,
        rotation=head.rotation,
        flipped=flipped,
    )
