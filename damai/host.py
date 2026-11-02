"""抢票宿主基类：把 device / config / probe 绑成选择器与导航层可用的对象。

源项目把这一切塞进一个 1165 行的 ``DamaiBot`` 神对象，各选择器再用
``set_bot()`` 反向引用宿主，形成双向委托——``PriceSelector`` 调
``bot._get_price_option_coordinates_by_config_index()``，而后者又定义在
``PriceSelector`` 里。移植时不照搬这套循环，改为：

- ``GrabHost`` 只提供**底层能力**（u2 device + primitives + probe + 配置）
- 各选择器/导航器**自包含**自己的业务流程，只从 host 取原语

这样每个模块都能用一个假 device 单独测，不需要先把整个编排器搭起来。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from core.logger import get_logger
from damai.primitives import ANDROID_UIAUTOMATOR, UIPrimitives
from damai.probe import PageProbe

logger = get_logger("damai.host")

#: 首启/中途弹出的干扰弹窗，按出现频率排序（越靠前命中率越高）
_STARTUP_POPUPS: tuple[tuple[str, str], ...] = (
    ("id", "android:id/ok"),
    ("id", "cn.damai:id/id_boot_action_agree"),
    ("id", "cn.damai:id/damai_theme_dialog_cancel_btn"),
    ("id", "cn.damai:id/damai_theme_dialog_close_layout"),
    ("id", "cn.damai:id/damai_theme_dialog_close_btn"),
    (ANDROID_UIAUTOMATOR, 'new UiSelector().text("Cancel")'),
    (ANDROID_UIAUTOMATOR, 'new UiSelector().text("下次再说")'),
    (ANDROID_UIAUTOMATOR, 'new UiSelector().text("我知道了")'),
    (ANDROID_UIAUTOMATOR, 'new UiSelector().text("知道了")'),
)

#: 轻量弹窗：只处理会挡住点击的最常见几种，用于热路径
_FAST_DIALOGS: tuple[tuple[str, str], ...] = (
    ("id", "cn.damai:id/damai_theme_dialog_cancel_btn"),
    ("id", "cn.damai:id/damai_theme_dialog_close_btn"),
    ("id", "cn.damai:id/damai_theme_dialog_close_layout"),
    (ANDROID_UIAUTOMATOR, 'new UiSelector().text("知道了")'),
    (ANDROID_UIAUTOMATOR, 'new UiSelector().text("我知道了")'),
)


class GrabHost(UIPrimitives):
    """选择器与导航层的共同宿主。

    Args:
        device: uiautomator2 device（或测试替身）。
        config: :class:`damai.config.GrabConfig`，至少要有 ``app_package``。
        probe: 可复用的 :class:`damai.probe.PageProbe`；不传则新建一个。
    """

    def __init__(self, device: Any, config: Any, probe: PageProbe | None = None) -> None:
        self.d = device
        self.config = config
        self.probe = probe if probe is not None else PageProbe(device, config)
        #: 热路径坐标缓存，键为业务语义名（如 ``attendee_checkboxes``）
        self._cached_hot_path_coords: dict[str, Any] = {}
        #: 分步耗时（导航层写入，出口报表读取）
        self.step_timings: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # 页面状态
    # ------------------------------------------------------------------ #
    def probe_current_page(self, fast: bool = False) -> dict[str, Any]:
        """当前页面状态（``PageProbe.probe`` 的别名，沿用源项目命名）。"""
        return self.probe.probe(fast=fast)

    def wait_for_page_state(
        self,
        expected_states: set[str],
        timeout: float = 5.0,
        poll_interval: float = 0.2,
    ) -> dict[str, Any]:
        """轮询等待进入 ``expected_states`` 之一，超时返回最后一次探测结果。

        注意：超时不抛异常。调用方必须自己检查返回的 ``state``——
        静默返回「没等到」比抛异常更安全，因为抢票链路里每种状态都有各自的
        下一步动作，抛出去反而会打断整条流程。
        """
        deadline = time.time() + max(0.0, timeout)
        last = self.probe_current_page(fast=True)
        while time.time() < deadline:
            if last["state"] in expected_states:
                return last
            time.sleep(poll_interval)
            last = self.probe_current_page(fast=True)
        return last

    # ------------------------------------------------------------------ #
    # 干扰处理
    # ------------------------------------------------------------------ #
    def dismiss_startup_popups(self) -> bool:
        """关掉首启的一次性系统/应用弹窗。返回是否关过至少一个。"""
        dismissed = False
        for by, value in _STARTUP_POPUPS:
            if not self._has_element(by, value):
                continue
            if self.ultra_fast_click(by, value):
                dismissed = True
                time.sleep(0.3)
        return dismissed

    def dismiss_fast_blocking_dialogs(self) -> bool:
        """热路径用的轻量弹窗清理，不做全量扫描。"""
        dismissed = False
        for by, value in _FAST_DIALOGS:
            if not self._has_element(by, value):
                continue
            if self.ultra_fast_click(by, value, timeout=0.15):
                dismissed = True
                time.sleep(0.05)
        return dismissed

    # ------------------------------------------------------------------ #
    # 返回键
    # ------------------------------------------------------------------ #
    def press_back(self) -> bool:
        """按返回键，失败返回 ``False``（不抛）。"""
        return self._press_keycode_safe(4, context="返回")

    # ------------------------------------------------------------------ #
    # 分步耗时
    # ------------------------------------------------------------------ #
    @contextmanager
    def timed_step(
        self, step_name: str, manual_baseline_seconds: float | None = None
    ) -> Iterator[None]:
        """记录单步耗时，并与「手动操作需要多久」的基线对比。

        基线对比是这个项目唯一有意义的性能指标——绝对秒数没有解释力，
        「比人手动点快多少」才是抢票场景真正关心的。
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            faster = (
                True
                if manual_baseline_seconds is None
                else elapsed <= manual_baseline_seconds
            )
            self.step_timings.append(
                {
                    "step": step_name,
                    "seconds": round(elapsed, 3),
                    "manual_baseline_seconds": manual_baseline_seconds,
                    "faster_than_manual": faster,
                }
            )
            if manual_baseline_seconds is None:
                logger.info("步骤耗时[%s] %.2fs", step_name, elapsed)
            else:
                relation = "快于手动基线" if faster else "慢于手动基线"
                log_fn = logger.info if faster else logger.warning
                log_fn(
                    "步骤耗时[%s] %.2fs（手动基线 %.2fs，%s）",
                    step_name,
                    elapsed,
                    manual_baseline_seconds,
                    relation,
                )
