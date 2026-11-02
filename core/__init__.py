"""Android 设备自动化底座。

对外入口是 :class:`core.android.actions.AndroidDevice` 与
:func:`core.android.u2_driver.connect`：前者是设备能力门面（与驱动无关），
后者是 uiautomator2 后端的具体实现。
"""

from __future__ import annotations

from core.android.actions import AndroidDevice, Screen, to_by
from core.config.config import DeviceConfig
from core.logger import get_logger, setup_logging

__version__ = "0.1.0"

__all__ = [
    "AndroidDevice",
    "Screen",
    "to_by",
    "DeviceConfig",
    "get_logger",
    "setup_logging",
    "__version__",
]
