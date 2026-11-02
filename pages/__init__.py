"""页面对象层。

``base_page.py`` 提供通用页面基类；``base_damai.py`` 是大麦的页面基类
（在其上补页面状态判定、浮层清理、UI 树复用）；``damai/`` 是大麦具体页面，
``components/`` 放跨页复用组件。

页面方法返回下一个页面对象，断言留给 flows / tests。
"""

from __future__ import annotations

from pages.base_damai import DamaiBasePage
from pages.base_page import BasePage, PageNotReadyError

__all__ = [
    "BasePage",
    "PageNotReadyError",
    "DamaiBasePage",
]
