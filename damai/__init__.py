"""大麦抢票引擎（uiautomator2 直连）。

与仓库的 Appium 栈**并存但不互相 import**：

- ``core/`` / ``pages/`` / ``flows/`` —— Appium 会话驱动的常规 UI 自动化
- ``damai/`` —— uiautomator2 直连的抢票引擎，按毫秒级节奏控制点击

为什么另起一套驱动：Appium 每条命令要走一次 HTTP 往返（本地 50~100ms），
而开售瞬间需要 20~30ms 连点、单次 XML dump 复用给多个坐标提取、
``input tap`` 批量 shell。这些在 Appium 协议下做不到。

依赖 ``uiautomator2`` 是**可选**的（``pip install -e ".[grab]"``）。
本包任何模块的顶层都不 import 它，只在 :mod:`damai.device` 内懒加载，
因此未安装时 ``import damai`` 依然可用、``pytest -m unit`` 依然全绿。

入口：``python -m damai <子命令>``，详见 :mod:`damai.cli`。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
