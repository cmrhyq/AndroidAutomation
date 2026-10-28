"""淘宝首页页面对象。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pages.base_page import BasePage
from pages.components.bottom_tab import BottomTab
from pages.components.popup import PopupCleaner
from pages.components.search_bar import SearchBar

if TYPE_CHECKING:
    from pages.cart_page import CartPage
    from pages.search_result_page import SearchResultPage


class HomePage(BasePage):
    """淘宝首页（底部 tab 框架的第一屏）。"""

    name = "home"

    #: 首页判定必须**排他**：只能用首页独有的元素。
    #: 特别注意不要放 `desc=首页` —— 那是底部导航常驻 tab，购物车页、我的淘宝页
    #: 同样存在，会导致「明明在购物车却认为在首页」的假阳性（实测踩过）。
    ready_selectors: tuple[str, ...] = (
        "desc=搜索栏",
        "text~=搜索宝贝",
        "text~=搜索天猫",
        "id=com.taobao.taobao:id/search_bar",
    )

    def __init__(self, dev) -> None:
        super().__init__(dev)
        self.search_bar = SearchBar(dev)
        self.tab = BottomTab(dev)
        self.popups = PopupCleaner(dev)

    # ------------------------------------------------------------------ #
    # 页面动作
    # ------------------------------------------------------------------ #
    def clean_popups(self, rounds: int = 3) -> int:
        """清掉首页各类浮层（开屏广告、更新提示、活动蒙层）。"""
        return self.popups.dismiss(rounds=rounds)

    def search(self, keyword: str) -> SearchResultPage:
        """首页搜索：点搜索入口 → 输入关键词 → 提交，返回搜索结果页。"""
        from pages.search_result_page import SearchResultPage

        self.log.info("首页搜索: %r", keyword)
        self.search_bar.search_phrase(keyword)
        return SearchResultPage(self.dev).wait_ready()

    def open_cart(self) -> CartPage:
        """通过底部 tab 进入购物车。"""
        from pages.cart_page import CartPage

        self.tab.goto("cart")
        return CartPage(self.dev).wait_ready()

    def goto_mine(self) -> None:
        self.tab.goto("mine")
