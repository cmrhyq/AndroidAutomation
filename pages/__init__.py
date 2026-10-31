"""页面对象层。

base_page.py 提供基类，components/ 放跨页复用组件，*_page.py 是具体页面；
页面方法返回下一个页面对象，断言留给 flows / tests。"""

from __future__ import annotations

from pages.base_page import BasePage, PageNotReadyError
from pages.cart_page import CartPage
from pages.coin_page import CoinPage
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
    "CoinPage",
]
