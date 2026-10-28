"""pytest 夹具：共享一个 Appium 会话；无设备时自动跳过而非报错。

失败截图已统一由 ``core.report.plugin`` 处理（它在 call 阶段失败时立刻拍，
此时 driver 还活着），这里不再重复实现。
"""

from __future__ import annotations

import sys
from contextlib import suppress
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.android.actions import AndroidDevice  # noqa: E402
from core.appiums.driver_factory import (  # noqa: E402
    AppiumServerError,
    DeviceNotFoundError,
    create_driver,
)
from core.config.config import DeviceConfig  # noqa: E402


@pytest.fixture(scope="session")
def config() -> DeviceConfig:
    return DeviceConfig.load()


@pytest.fixture(scope="session")
def driver(config: DeviceConfig):
    """整个会话共用一个 Appium session（省去反复建会话的 10s+ 开销）。"""
    try:
        drv = create_driver(config)
    except DeviceNotFoundError as exc:
        pytest.skip(f"无可用设备: {exc}")
    except AppiumServerError as exc:
        pytest.skip(f"Appium Server 不可用: {exc}")
    yield drv
    with suppress(Exception):
        drv.quit()


@pytest.fixture(scope="session")
def dev(driver) -> AndroidDevice:
    return AndroidDevice(driver)


@pytest.fixture(scope="session")
def screen_size(dev) -> tuple[int, int]:
    """屏幕尺寸，坐标兜底计算会用到。"""
    return dev.screen.width, dev.screen.height
