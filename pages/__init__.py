"""页面对象层。

约定
----
- ``pages/base_page.py``    —— 页面基类（多候选选择器轮询、显式等待、滚动、留证）
- ``pages/components/``     —— 可复用组件（浮层清理、底部导航、搜索栏），跨页面复用
- ``pages/*_page.py``       —— 具体页面对象，只描述「这个页面有什么、能做什么」

页面方法返回下一个页面对象，方便链式编排；具体断言放在 flows / tests 层。
"""

from __future__ import annotations

from pages.base_page import BasePage, PageNotReadyError
from pages.cart_page import CartPage
from pages.detail_page import DetailPage
from pages.home_page import HomePage
from pages.search_result_page import SearchResultPage

__all__ = [
    "BasePage",
    "PageNotReadyError",
    "HomePage",
    "SearchResultPage",
    "DetailPage",
    "CartPage",
]
