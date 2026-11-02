"""pytest 公共夹具。

抢票链路走 uiautomator2 直连，没有「会话级设备夹具」这种东西：
设备相关的验证统一放 ``scripts/damai_grab.py doctor``，不塞进单测。

失败截图由 ``core.report.plugin`` 在 call 阶段失败时处理（它按用例签名里有没有
``dev`` 决定要不要拍），这里不再重复实现。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.config.config import DeviceConfig  # noqa: E402


@pytest.fixture(scope="session")
def config() -> DeviceConfig:
    return DeviceConfig.load()
