"""系统级随机等待：全项目的等待节奏统一收口在这里。

三档区间：wait_short 0.20~0.55s / wait_medium 0.90~1.90s / wait_long 2.20~4.20s，
区间内随机取值；AA_WAIT_SCALE 可整体缩放（0 = 跳过，1 = 正常，2 = 放慢）。"""

from __future__ import annotations

import random
import time
from typing import Final

from core.config.config import get
from core.logger import get_logger

logger = get_logger(__name__)

#: 等待倍率环境变量。1 = 正常；0 = 全部跳过；1.5~2 = 放慢观察
SCALE_ENV: Final[str] = "AA_WAIT_SCALE"

#: 短等待区间（秒）
SHORT_RANGE: Final[tuple[float, float]] = (0.20, 0.55)
#: 中等等待区间（秒）
MEDIUM_RANGE: Final[tuple[float, float]] = (0.90, 1.90)
#: 长等待区间（秒）
LONG_RANGE: Final[tuple[float, float]] = (2.20, 4.20)


def scale() -> float:
    """当前等待倍率（每次读取环境变量，便于运行时切换）。非法值按 1.0 处理。"""
    raw = (get(SCALE_ENV, "1") or "1").strip()
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r 不是数字，按 1.0 处理", SCALE_ENV, raw)
        return 1.0
    return max(value, 0.0)


def _wait(bounds: tuple[float, float], reason: str = "") -> float:
    """在 ``bounds`` 区间内随机睡眠，返回实际睡眠秒数（被倍率缩放后）。"""
    factor = scale()
    if factor <= 0:
        logger.debug("跳过等待（%s=0）%s", SCALE_ENV, f"：{reason}" if reason else "")
        return 0.0
    low, high = bounds[0] * factor, bounds[1] * factor
    duration = random.uniform(low, high)
    logger.debug("等待 %.2fs%s", duration, f"（{reason}）" if reason else "")
    time.sleep(duration)
    return round(duration, 3)


def wait_short(reason: str = "") -> float:
    """短随机等待（0.20~0.55s）：轮询间隙、点击后的即时状态刷新。"""
    return _wait(SHORT_RANGE, reason)


def wait_medium(reason: str = "") -> float:
    """中等随机等待（0.90~1.90s）：页面跳转、tab 切换、输入提交。"""
    return _wait(MEDIUM_RANGE, reason)


def wait_long(reason: str = "") -> float:
    """长随机等待（2.20~4.20s）：冷启动、重渲染页面、从后台恢复前台。"""
    return _wait(LONG_RANGE, reason)
