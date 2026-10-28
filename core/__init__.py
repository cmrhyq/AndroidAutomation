"""Appium 控制 Android 设备的轻量封装。

典型用法：

    from core import AndroidDevice, session

    with session() as driver:
        dev = AndroidDevice(driver)
        dev.press("home")
        dev.launch_app("com.android.settings")
        dev.tap("text~=显示")
        dev.screenshot("settings")
"""
from core.android.actions import AndroidDevice, Screen, to_by
from core.appiums.driver_factory import create_driver, ensure_server, resolve_udid, session, AppiumServerError, \
    DeviceNotFoundError
from core.config.config import DeviceConfig
from core.logger import get_logger, setup_logging

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
