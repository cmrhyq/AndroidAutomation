"""时钟校时：判断「现在几点了」这件事，比看上去重要得多。

抢票要卡在开售那一秒点击，而机器时钟会漂：本机实测比手机快 **833ms**（PFGM00，
2026-11 实测）。不校时的话有两种结局 —— 早 833ms 点下去买到「未开售」的预约入口，
或者晚 833ms 眼看着票被抢光，事后还完全无法归因。

三个不同的量，不要混：

=====================  ==========================  ==========================
名字                   含义                        用在哪
=====================  ==========================  ==========================
``clock_offset_ms``    **本机墙钟**相对真实时间的    决定何时点击（决定性）
                       超前量（正数 = 本机偏快）
``device_offset_ms``   **设备时钟 - 本机时钟**       只用于 doctor 观测
``真实时间``           本机墙钟 - clock_offset_ms   与 sell_start_time 比较
=====================  ==========================  ==========================

注意真正决定何时点的是**本机时钟**而不是手机时钟：点击指令由本机发出，
手机只是执行器。所以 ``device_offset_ms`` 再大也不该参与开抢决策。
"""

from __future__ import annotations

import statistics
import time
from datetime import datetime, timedelta, timezone

from core.logger import get_logger, log_event

__all__ = [
    "TZ_SHANGHAI",
    "WAIT_WINDOW_AFTER_SALE_S",
    "device_clock_offset_ms",
    "parse_sale_time",
    "sleep_until",
    "true_now",
]

logger = get_logger(__name__)

#: 全国演出都用北京时间，与设备/本机所在时区无关
TZ_SHANGHAI = timezone(timedelta(hours=8))

#: 开售后最多再守这么久。超过就交给上层重试，不在这里死等 ——
#: 继续守下去只会让重试链路整体延后。
WAIT_WINDOW_AFTER_SALE_S = 8.0


# --------------------------------------------------------------------------- #
# 真实时间
# --------------------------------------------------------------------------- #
def true_now(clock_offset_ms: int = 0) -> datetime:
    """本机墙钟按 ``clock_offset_ms`` 校正后的真实时间（带时区）。

    ``clock_offset_ms`` 为正表示本机偏快，需要往回减。
    """
    return datetime.now(tz=TZ_SHANGHAI) - timedelta(milliseconds=int(clock_offset_ms or 0))


def parse_sale_time(raw: str | None) -> datetime | None:
    """把配置里的 ``sell_start_time`` 解析成带时区的时间；不可解析返回 ``None``。

    不带时区的写法（``2026-12-26 19:30:00``）一律按北京时间理解。
    """
    if not raw or not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw.strip())
    except ValueError:
        logger.warning("sell_start_time 无法解析: %r —— 按未配置处理", raw)
        return None
    return value.replace(tzinfo=TZ_SHANGHAI) if value.tzinfo is None else value


def sleep_until(target: datetime, *, max_sleep_s: float = 3600.0) -> None:
    """睡到 ``target``，但单次最多睡 ``max_sleep_s``。

    限制上限是为了让长等待仍能被 Ctrl-C 及时打断，也避免系统休眠导致
    一次性睡过开售时刻（醒来后会由调用方的轮询逻辑重新判断）。
    """
    remaining = (target - datetime.now(tz=TZ_SHANGHAI)).total_seconds()
    if remaining <= 0:
        return
    time.sleep(min(remaining, max_sleep_s))


# --------------------------------------------------------------------------- #
# 设备时钟观测（仅 doctor 用，不参与开抢决策）
# --------------------------------------------------------------------------- #
def _device_unix_ms(shell) -> int | None:
    """读设备当前 unix 毫秒时间。``shell`` 是 U2Driver 的 shell 通道。"""
    raw = shell("date", "+%s%3N").strip()
    if raw.isdigit():
        return int(raw)
    # 部分 ROM 的 toybox date 不支持 %3N，退化为秒级
    raw_seconds = shell("date", "+%s").strip()
    return int(raw_seconds) * 1000 if raw_seconds.isdigit() else None


def device_clock_offset_ms(shell, *, samples: int = 3) -> int | None:
    """设备时钟相对本机的偏差（毫秒，设备 - 本机）。

    Args:
        shell: 可调用对象，签名 ``shell(*args) -> str``，通常是
            :class:`core.android.u2_driver.U2Driver` 的 ``shell`` 方法。
        samples: 采样次数，取中位数抵消 adb 往返抖动。

    Returns:
        偏差毫秒数；测量失败返回 ``None``。

    单次测量的误差主要来自 adb 往返（几十到几百毫秒），所以默认 3 次取中位数。
    必须用 ``time.time()``（墙钟）而不是 ``time.monotonic()``（开机秒数）——
    后者与设备 unix 时间相减会算出 20 天量级的假偏差，这个坑踩过。
    """
    offsets: list[int] = []
    for _ in range(max(1, samples)):
        host_ms = int(time.time() * 1000)
        device_ms = _device_unix_ms(shell)
        if device_ms is not None:
            offsets.append(device_ms - host_ms)
        time.sleep(0.05)
    if not offsets:
        return None
    value = int(statistics.median(offsets))
    log_event(logger, "clock_probe", samples=len(offsets), device_offset_ms=value)
    return value
