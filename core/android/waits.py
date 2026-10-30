"""系统级随机等待（human-like pacing）—— 全项目的等待节奏统一收口在这里。

为什么不用裸 ``time.sleep``
--------------------------
1. **固定间隔是机器指纹**：每个动作后面都紧跟一个恒定的 0.3s / 1.0s，
   是风控最容易识别的特征之一。区间随机化能显著降低这层特征。
2. **魔法数字散落各处**：``sleep(0.4)`` ``sleep(1.2)`` ``sleep(2.0)`` 散在
   页面对象、组件、业务流里，想整体调快或调慢必须全文搜索改一遍。
3. **调试与回归需求冲突**：调选择器时要慢下来观察，跑回归时要快。
   用 ``AA_WAIT_SCALE`` 一个环境变量就能整体缩放（0 = 完全关掉）。

三档粒度
--------
+--------------+----------------+------------------------------------------+
| 方法         | 区间（秒）     | 典型场景                                 |
+==============+================+==========================================+
| ``wait_short``  | 0.20 ~ 0.55 | 轮询间隙、点击/按键后的即时反馈          |
| ``wait_medium`` | 0.90 ~ 1.90 | 页面跳转、tab 切换、输入提交、浮层弹出   |
| ``wait_long``   | 2.20 ~ 4.20 | 冷启动、Weex/themis 重页面渲染、恢复前台 |
+--------------+----------------+------------------------------------------+

选择原则：**先按「这一步设备要干多少活」选档位，不要按「以前这里写了多少秒」照搬。**
例如「点一下搜索按钮」→ short/medium；「冷启动 App 等首屏」→ long。

用法::

    from core.android.waits import wait_short, wait_medium, wait_long

    wait_short("点击后确认态刷新")
    wait_medium("搜索结果页加载")
    wait_long("淘宝冷启动首屏")

业务代码里更推荐走封装层的同名方法（``dev.wait_short()``），这样等待会被
打印进该页面对象的日志流，排查时能直接看到「时间花在哪一段」。
"""

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
