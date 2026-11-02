"""大麦抢票页面对象层。

与 ``pages/base_page.py`` 共用同一套基类，
区别只在驱动后端：大麦链路走 uiautomator2 直连（``core.android.u2_driver``），
因为开售瞬间需要几十毫秒级的连点，走协议栈的每命令一次往返撑不住。

页面方法返回下一个页面对象，断言留给 ``flows/damai_flow.py``。
"""

from __future__ import annotations

from pages.base_damai import DamaiBasePage
from pages.damai.confirm_page import DamaiConfirmPage
from pages.damai.detail_page import (
    DamaiDetailPage,
    SessionNotFoundError,
    WrongEventError,
)
from pages.damai.home_page import DamaiHomePage
from pages.damai.search_page import (
    DamaiSearchPage,
    SearchAmbiguousError,
    SearchEmptyError,
)
from pages.damai.sku_page import DamaiSkuPage

__all__ = [
    "DamaiBasePage",
    "DamaiConfirmPage",
    "DamaiDetailPage",
    "DamaiHomePage",
    "DamaiSearchPage",
    "DamaiSkuPage",
    "SearchAmbiguousError",
    "SearchEmptyError",
    "SessionNotFoundError",
    "WrongEventError",
]
