"""androidauto - Appium 控制 Android 设备的轻量封装。

典型用法：

    from androidauto import AndroidDevice, session

    with session() as driver:
        dev = AndroidDevice(driver)
        dev.press("home")
        dev.launch_app("com.android.settings")
        dev.tap("text~=显示")
        dev.screenshot("settings")
"""

from .actions import AndroidDevice, Screen, to_by
from .config import DeviceConfig
from .driver_factory import (
    AppiumServerError,
    DeviceNotFoundError,
    create_driver,
    ensure_server,
    resolve_udid,
    session,
)
from .logger import get_logger, setup_logging

__version__ = "0.1.0"

__all__ = [
    "AndroidDevice",
    "Screen",
    "to_by",
    "DeviceConfig",
    "create_driver",
    "ensure_server",
    "resolve_udid",
    "session",
    "AppiumServerError",
    "DeviceNotFoundError",
    "get_logger",
    "setup_logging",
    "__version__",
]
