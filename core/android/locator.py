"""定位符常量、设备层异常与显式等待轮询器。

设备层是 uiautomator2 直连，不再依赖任何 WebDriver 客户端库，所以这几个
基础概念自己定义一份：定位方式、异常类型、等待轮询。

这里的取值与原链路保持**字符串完全一致**（``"xpath"`` / ``"id"`` /
``"class name"`` / ``"accessibility id"``），所以 (by, value) 这条路由约定不变，
``to_by()`` 与 ``_to_xpath()`` 之间的接口不用改。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class By:
    """定位方式。取值与原链路的 ``By`` 一致，便于选择器语法保持原样。"""

    ID = "id"
    XPATH = "xpath"
    CLASS_NAME = "class name"
    #: Android 上等价于 content-desc（沿用旧叫法，以免调用点改名）
    ACCESSIBILITY_ID = "accessibility id"


class DeviceError(RuntimeError):
    """设备层异常基类。"""


class ElementTimeoutError(DeviceError, TimeoutError):
    """等待元素出现超时。

    继承 ``TimeoutError`` 而不是另造一个孤立类型：调用方原本 ``except
    TimeoutException`` 的写法可以原样保留，同时它也是 ``DeviceError``，
    在最外层能与其他设备故障一起兜住。
    """


class ElementStaleError(DeviceError):
    """元素句柄已失效（页面重绘、节点被回收）。"""


def poll_until(
    probe: Callable[[], T | None],
    *,
    timeout: float,
    poll: float = 0.3,
    message: str = "",
) -> T:
    """反复调用 ``probe`` 直到它返回真值。

    替代 ``WebDriverWait(driver, timeout).until(...)``：uiautomator2 是直连，
    没有「等待驱动就绪」这层语义，需要的只是「隔一会儿再看一眼」。

    Args:
        probe: 无参可调用对象，返回真值即成功（元素、非空列表、True 都行）。
        timeout: 总超时（秒）。
        poll: 两次探测的间隔（秒）。
        message: 超时时的报错文案（建议带上选择器与当前 activity）。

    Raises:
        ElementTimeoutError: 超时仍无真值。
    """
    deadline = time.monotonic() + max(timeout, 0)
    while True:
        try:
            result = probe()
        except ElementStaleError:
            # 探测期间页面重绘是常态，不该让整次等待失败
            result = None
        if result:
            return result
        if time.monotonic() >= deadline:
            raise ElementTimeoutError(message or f"{timeout}s 内条件未满足")
        time.sleep(poll)


__all__ = [
    "By",
    "DeviceError",
    "ElementStaleError",
    "ElementTimeoutError",
    "poll_until",
]
