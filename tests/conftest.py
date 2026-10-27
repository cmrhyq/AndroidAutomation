"""pytest 夹具：共享一个 Appium 会话；无设备时自动跳过而非报错。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from androidauto.actions import AndroidDevice  # noqa: E402
from androidauto.config import DeviceConfig  # noqa: E402
from androidauto.driver_factory import (  # noqa: E402
    AppiumServerError,
    DeviceNotFoundError,
    create_driver,
)


@pytest.fixture(scope="session")
def config() -> DeviceConfig:
    return DeviceConfig.load()


@pytest.fixture(scope="session")
def driver(config: DeviceConfig):
    try:
        drv = create_driver(config)
    except DeviceNotFoundError as exc:
        pytest.skip(f"无可用设备: {exc}")
    except AppiumServerError as exc:
        pytest.skip(f"Appium Server 不可用: {exc}")
    yield drv
    drv.quit()


@pytest.fixture(scope="session")
def dev(driver) -> AndroidDevice:
    return AndroidDevice(driver)
