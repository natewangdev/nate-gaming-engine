"""基于 nge.bot 的示例 bot。

演示规则引擎：每个 tick 截屏、模板匹配，并通过 HID 控制器执行操作。
请替换模板路径与业务逻辑；本文件是脚手架，不是开箱即用的脚本。

前置条件：
  - 已烧录固件，ESP32-S3 接在原生 USB 口。
  - pip install -r requirements.txt（需要 opencv 与截屏后端）
  - PNG 模板放在 ./templates/ 下

运行（请先关闭串口监视器）：
  python -m examples.bot_example
"""

from __future__ import annotations

import math
import time
from enum import Enum, IntEnum, auto
from pathlib import Path

import nge
from nge.bot import Bot, BotConfig
from nge.capture import ScreenCapture

# 资源根目录（模板等相对路径以此为基准）。
ASSET_ROOT = Path(r"C:\Users\Admin\Desktop\d4")
YOLO_MODEL = Path(r"D:/Project/ultralytics-8.3.163/runs/detect/d4/weights/best.onnx")

# 物品栏整体区域 (left, top, right, bottom)；3 行 × 11 列中心点，按行优先编号 0..32。
_INVENTORY_REGION = (1688, 962, 2496, 1285)
_INVENTORY_ROWS = 3
_INVENTORY_COLS = 11


def _inventory_slot_centers(
    region: tuple[int, int, int, int],
    rows: int,
    cols: int,
) -> list[tuple[int, int]]:
    l, t, r, b = region
    w, h = r - l, b - t
    slots: list[tuple[int, int]] = []
    for row in range(rows):
        for col in range(cols):
            sl = l + (col * w) // cols
            sr = l + ((col + 1) * w) // cols
            st = t + (row * h) // rows
            sb = t + ((row + 1) * h) // rows
            slots.append(((sl + sr) // 2, (st + sb) // 2))
    return slots


_INVENTORY_SLOTS: list[tuple[int, int]] = _inventory_slot_centers(
    _INVENTORY_REGION, _INVENTORY_ROWS, _INVENTORY_COLS
)


class YoloLabel(IntEnum):
    """YOLO 检测 class id（与模型训练时 class id 一致）。"""

    ITEM = 0  # item 物品
    BOSS_BLOOD = 1  # bossblood boss血条
    AOLUSI = 2  # aolusi 奥鲁斯
    YELLOWDOOR_BOSS_BOX = 3  # yellowdoor-boss-box 黄门boss箱子
    NEST_SMITH = 4  # nest-smith 铁匠
    NEST_MYSTIC = 5  # nest-mystic 秘法师
    NEST_STASH = 6  # nest-stash 储物箱
    KIOVASHA_SMITH = 7  # kiovasha-smith 基奥瓦沙铁匠
    KIOVASHA_MYSTIC = 8  # kiovasha-mystic 基奥瓦沙秘法师
    KIOVASHA_STASH = 9  # kiovasha-stash 基奥瓦沙储物箱



class WalkDirection(Enum):
    """八方向行走（屏幕坐标：y 轴向下为正）。"""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"
    UP_LEFT = "up_left"
    UP_RIGHT = "up_right"
    DOWN_LEFT = "down_left"
    DOWN_RIGHT = "down_right"


_WALK_ANGLES: dict[WalkDirection, float] = {
    WalkDirection.UP: 0.0,
    WalkDirection.UP_RIGHT: 45.0,
    WalkDirection.RIGHT: 90.0,
    WalkDirection.DOWN_RIGHT: 135.0,
    WalkDirection.DOWN: 180.0,
    WalkDirection.DOWN_LEFT: -135.0,
    WalkDirection.LEFT: -90.0,
    WalkDirection.UP_LEFT: -45.0,
}


def _resolve_direction(direction: WalkDirection | str) -> WalkDirection:
    if isinstance(direction, WalkDirection):
        return direction
    return WalkDirection(direction.strip().lower().replace("-", "_"))


def _direction_vector(direction: float | WalkDirection | str) -> tuple[float, float]:
    """单位方向向量。角度：0° 向上，顺时针为正（90° 向右，-90° 向左）。"""
    if isinstance(direction, (int, float)):
        angle = float(direction)
    elif isinstance(direction, WalkDirection):
        angle = _WALK_ANGLES[direction]
    else:
        angle = _WALK_ANGLES[_resolve_direction(direction)]
    rad = math.radians(angle)
    return math.sin(rad), -math.cos(rad)


def _walk_target(
    center_x: float,
    center_y: float,
    direction: float | WalkDirection | str,
    distance: float,
) -> tuple[float, float]:
    dx, dy = _direction_vector(direction)
    return (
        center_x + dx * distance,
        center_y + dy * distance,
    )


_SKILL_KEYS = ("5", "3", "4", "1")

# 打怪时依次移动的屏幕坐标（四角 + 前三点再扫一轮）。
_FIGHT_POSITIONS = [
    (568, 287),
    (568, 1091),
    (1976, 287),
    (1976, 1091),
    (568, 287),
    (568, 1091),
    (1976, 287),
    (1976, 1091),
    (568, 287),
    (568, 1091),
    (1976, 287),
    (1976, 1091),
    (568, 287),
    (568, 1091),
    (1976, 287),
    (1976, 1091),
    (568, 287),
]


def _press_skill_rotation(ctx) -> None:
    for key in _SKILL_KEYS:
        ctx.controller.press(key)
        nge.random_delay(0, 20)


def walk(
    ctx,
    direction: float | WalkDirection | str,
    distance: float,
    *,
    spread: float = 0.0,
    button: str = "L",
    hold: float | None = None,
    duration: float | None = None,
    center: tuple[float, float] | None = None,
) -> None:
    """点击屏幕中心偏移处行走（点地移动类游戏专用，非 nge 通用 API）。

    ``direction`` 可为角度（度）：0 向上，90 向右，-90 向左，180 向下；
    也兼容 ``WalkDirection`` 枚举或名称字符串。
    """
    if center is None:
        w, h = ctx.controller.screen_size
        cx, cy = w / 2.0, h / 2.0
    else:
        cx, cy = center
    tx, ty = _walk_target(cx, cy, direction, distance)
    ctx.controller.click(
        tx, ty, button=button, hold=hold, duration=duration, spread=spread
    )


class BotState(Enum):
    """Bot 高层阶段（存于 ``ctx.state["state"]``）。"""

    NOT_TEAMED = auto()  # 未组队
    RUN_MAP = auto()  # 跑图
    FIGHT = auto()  # 打怪
    LOOT = auto()  # 捡物品
    CHECK_INVENTORY = auto()  # 检查背包
    HANDLE_GEAR = auto()  # 处理装备


BOT_STATE_LABEL: dict[BotState, str] = {
    BotState.NOT_TEAMED: "未组队",
    BotState.RUN_MAP: "跑图",
    BotState.FIGHT: "打怪",
    BotState.LOOT: "捡物品",
    BotState.CHECK_INVENTORY: "检查背包",
    BotState.HANDLE_GEAR: "处理装备",
}


def bot_state(ctx) -> BotState:
    return ctx.state["state"]


def set_bot_state(ctx, state: BotState) -> None:
    if ctx.state.get("state") != state:
        ctx.state["state"] = state
        nge.logger.info(f"[state] -> {BOT_STATE_LABEL[state]}")


def build_bot() -> Bot:
    controller = nge.connect()  # 或 nge.connect(port="COM7")

    # 截取整块主屏；传入 region=(l, t, r, b) 可限定区域。
    capture = ScreenCapture()

    config = BotConfig(
        tick_hz=4.0,  # 约每秒4次评估规则
        tick_jitter=0.35,  # 随机化 tick 间隔 ±35%
        one_action_per_tick=True,
        session_max_seconds=0,  # 0 = 直到 Ctrl+C 才停止
        break_every=3600.0,  # 约每小时休息一次（拟人）
        break_duration=(5.0, 15.0),
    )

    bot = Bot(
        controller,
        capture,
        config=config,
        asset_root=ASSET_ROOT,
        yolo_model=YOLO_MODEL,
    )

    # 共享状态：ctx.state["state"] 使用 BotState（切换见 set_bot_state）。
    bot.ctx.state.update(
        {
            "state": BotState.HANDLE_GEAR,
            "loot_count": 0,
        }
    )
    # --- 规则：priority 越大越先评估 ---

    @bot.rule(name="跑图", priority=100, cooldown=1.0)
    def runmap(ctx) -> bool:
        if bot_state(ctx) != BotState.RUN_MAP:
            return False

        nge.logger.info("当前状态：跑图")
        # 人物在屏幕中心，向正上方点击 220px 处行走（落点再随机散布 10px）。
        walk(ctx, 45, distance=800, spread=10)
        nge.random_delay(1000, 1500)
        walk(ctx, 80, distance=1000, spread=10)
        nge.random_delay(500, 1500)
        walk(ctx, 90, distance=900, spread=10)
        nge.random_delay(500, 1000)
        walk(ctx, 110, distance=700, spread=10)
        nge.random_delay(500, 1000)
        walk(ctx, 40, distance=800, spread=10)
        nge.random_delay(500, 1500)
        walk(ctx, 60, distance=800, spread=10)
        nge.random_delay(1000, 1500)
        walk(ctx, 60, distance=1000, spread=10)
        nge.random_delay(1000, 1500)
        walk(ctx, 45, distance=1000, spread=10)
        nge.random_delay(1000, 1500)
        walk(ctx, 10, distance=800, spread=10)
        nge.random_delay(1000, 1500)
        walk(ctx, 60, distance=800, spread=10)
        nge.random_delay(1000, 1500)
        walk(ctx, 45, distance=800, spread=10)
        nge.random_delay(1000, 1500)
        walk(ctx, -45, distance=800, spread=10)
        nge.random_delay(1000, 1500)
        walk(ctx, 0, distance=400, spread=10)
        nge.random_delay(200, 500)
        walk(ctx, 0, distance=420, spread=10)
        nge.random_delay(200, 500)
        walk(ctx, 0, distance=400, spread=10)
        nge.random_delay(0, 200)
        walk(ctx, 0, distance=100, spread=10)
        nge.random_delay(200, 500)
        walk(ctx, -90, distance=800, spread=10)
        nge.random_delay(1000, 1500)
        walk(ctx, -90, distance=780, spread=10)
        nge.random_delay(500, 1000)
        walk(ctx, -90, distance=400, spread=10)
        nge.random_delay(500, 1000)
        walk(ctx, 2, distance=600, spread=10)
        nge.random_delay(500, 1000)
        walk(ctx, 0, distance=580, spread=10)
        nge.random_delay(500, 1000)
        walk(ctx, 0, distance=560, spread=10)
        nge.random_delay(500, 1000)
        walk(ctx, 0, distance=400, spread=10)
        nge.logger.info("跑图完成")
        # 检测boss血条
        set_bot_state(ctx, BotState.NOT_TEAMED)
        # d = ctx.find_object(YoloLabel.BOSS_BLOOD.value, conf=0.5)
        # if d is not None:
        #     nge.logger.info("boss血条存在")
        #     set_bot_state(ctx, BotState.FIGHT)
        # else:
        #     ctx.controller.press("t")
        #     nge.logger.info("未检测到BOSS，传送中...")
        #     nge.random_delay(10000, 12000)
        #     set_bot_state(ctx, BotState.NOT_TEAMED)
        #     nge.logger.info("当前状态：未组队")
        return True

    @bot.rule(name="打怪", priority=40, cooldown=1.0)
    def kill(ctx) -> bool:
        if bot_state(ctx) != BotState.FIGHT:
            return False
        nge.logger.info("当前状态：打怪")
        ctx.controller.mouse_down("R")
        for px, py in _FIGHT_POSITIONS:
            ctx.controller.move_to(px, py)
            _press_skill_rotation(ctx)
            nge.random_delay(500, 1000)

        ctx.controller.mouse_up("R")
        walk(ctx, 135, distance=1000, spread=10)
        set_bot_state(ctx, BotState.LOOT)

        return True

    @bot.rule(name="捡物品", priority=30, cooldown=0.2)
    def loot(ctx) -> bool:
        if bot_state(ctx) != BotState.LOOT:
            return False
        nge.logger.info("当前状态：捡物品")
        items = ctx.detect(conf=0.5, classes=[YoloLabel.ITEM])
        if not items:
            return True
        item = items[0]
        ctx.controller.click(item.x, item.y, spread=10)
        ctx.state["loot_count"] = ctx.state.get("loot_count", 0) + 1
        count = ctx.state["loot_count"]
        nge.logger.info(
            f"拾取物品 ({item.x}, {item.y}) conf={item.conf:.2f} count={count}"
        )
        if count > 30:
            set_bot_state(ctx, BotState.CHECK_INVENTORY)
        return True

    @bot.rule(name="检查背包", priority=20, cooldown=1.0)
    def check_inventory(ctx) -> bool:
        if bot_state(ctx) != BotState.CHECK_INVENTORY:
            return False
        nge.logger.info("当前状态：检查背包")
        m = ctx.find_in_region("images/装备排序.png", (1605, 880, 1707, 959), threshold=0.7)
        
        # 判断背包是否已经打开，如果未打开则打开背包
        if m is None:
            ctx.controller.press("i")
            nge.random_delay(1000, 1500)
            ctx.refresh_frame()
            m = ctx.find_in_region("images/装备排序.png", (1605, 880, 1707, 959), threshold=0.7)
            if m is not None:
                nge.logger.info("背包已打开")
            else:
                nge.logger.error("背包打开失败")
                return False

        # 判断装备和护身符最后一个栏位是否有装备
        n = ctx.find_in_region("images/空装备格子.png", (2417, 1170, 2507, 1290), threshold=0.7)
        if n is None:
            nge.logger.info("装备已满")
            
            set_bot_state(ctx, BotState.HANDLE_GEAR)
        else:
            nge.logger.info("装备未满")
            # 检查护身符最后一个栏位是否有装备
            ctx.controller.click(2280, 925, spread=10)
            nge.random_delay(500, 1000)
            ctx.refresh_frame()
            n = ctx.find_in_region("images/空装备格子.png", (2417, 1170, 2507, 1290), threshold=0.7)
            if n is None:
                nge.logger.info("护身符已满")
                set_bot_state(ctx, BotState.HANDLE_GEAR)
            else:
                nge.logger.info("护身符未满")

        # 关闭背包
        ctx.controller.press("i")
        ctx.state["loot_count"] = 0
        return True

    @bot.rule(name="处理装备", priority=10, cooldown=30.0)
    def handle_gear(ctx) -> bool:
        if bot_state(ctx) != BotState.HANDLE_GEAR:
            return False
        nge.logger.info("当前状态：处理装备")

        # 点击回城 (收藏基奥瓦沙)
        ctx.controller.press("t")
        nge.random_delay(6000, 7000)
        walk(ctx, 160, distance=800, spread=10)
        nge.random_delay(200, 500)
        walk(ctx, 160, distance=600, spread=10)
        nge.random_delay(200, 500)
        walk(ctx, 100, distance=600, spread=10)
        nge.random_delay(200, 500)
        walk(ctx, 45, distance=600, spread=10)
        nge.random_delay(1000, 1500)
        ctx.refresh_frame()
        items = ctx.detect(conf=0.5, classes=[YoloLabel.KIOVASHA_STASH])
        if items:
            item = items[0]
            ctx.controller.click(item.x, item.y, spread=30)
            nge.logger.info(f"点击储物箱 ({item.x}, {item.y}) conf={item.conf:.2f}")
            nge.random_delay(500, 1000)
            # 查找 "储物箱-宝箱"
            ctx.refresh_frame()
            m = ctx.find_in_region("images/储物箱-宝箱.png", (63, 166, 872, 270), threshold=0.7)
            if m is None:
                nge.logger.error("未检测到储物箱-宝箱")
                set_bot_state(ctx, BotState.HANDLE_GEAR)
                return False
            else:
                ctx.controller.click(m.x, m.y, spread=20)
                nge.random_delay(200, 500)

            # 存放装备
            for slot in _INVENTORY_SLOTS:
                ctx.controller.click(slot[0], slot[1],button="R", spread=10)
                nge.random_delay(50, 100)

            # 按ESC关闭储物箱
            ctx.controller.press("esc")
            nge.random_delay(200, 500)

            # 移动到秘术师
            walk(ctx, -135, distance=400, spread=10)
            nge.random_delay(200, 500)
            walk(ctx, -135, distance=400, spread=10)
            nge.random_delay(200, 500)
            walk(ctx, 180, distance=400, spread=10)
            nge.random_delay(200, 500)
            walk(ctx, 180, distance=400, spread=10)
            nge.random_delay(500, 1000)

            # 检测秘术师
            ctx.refresh_frame()
            items = ctx.detect(conf=0.5, classes=[YoloLabel.KIOVASHA_MYSTIC])
            if items:
                item = items[0]
                ctx.controller.click(item.x, item.y, spread=30)
                nge.logger.info(f"点击秘术师 ({item.x}, {item.y}) conf={item.conf:.2f}")
                nge.random_delay(500, 1000)
                # 点击分解神符按钮
                ctx.controller.click(695, 216, spread=10)
                nge.random_delay(200, 500)
                # 点击所有神符
                ctx.controller.click(513, 945, spread=10)
                nge.random_delay(200, 500)
                # 点击接受
                ctx.controller.click(1181, 857, spread=10)
                nge.random_delay(50, 200)
                # 按ESC关闭
                ctx.controller.press("esc")
                set_bot_state(ctx, BotState.HANDLE_GEAR)
            else:
                nge.logger.error("未检测到秘术师")
                set_bot_state(ctx, BotState.HANDLE_GEAR)
                return False
        else:
            nge.logger.error("未检测到储物箱")
            set_bot_state(ctx, BotState.HANDLE_GEAR)
            return False
  
        return True

    @bot.rule(name="advance", priority=0, cooldown=0.0)
    def advance(ctx) -> bool:
        # 无其他规则匹配时的兜底行为。
        nge.logger.info("当前状态：兜底行为")
        return True

    return bot


def main() -> None:
    bot = build_bot()
    nge.logger.info("Bot 将在3秒后运行")
    time.sleep(3)
    bot.run()


if __name__ == "__main__":
    main()
