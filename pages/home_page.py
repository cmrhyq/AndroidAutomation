"""淘宝首页页面对象（含推荐页常见组件：频道栏、领券条、金币挂件）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core import get_logger
from pages.base_page import BasePage
from pages.components.bottom_tab import BottomTab
from pages.components.coin_widget import CoinWidget
from pages.components.coupon_bar import CouponBar
from pages.components.feed_channel import FeedChannelBar
from pages.components.popup import PopupCleaner
from pages.components.search_bar import SearchBar

if TYPE_CHECKING:
    from pathlib import Path

    from pages.cart_page import CartPage
    from pages.coin_page import CoinPage
    from pages.search_result_page import SearchResultPage


logger = get_logger(__name__)


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
        self.coin = CoinWidget(dev)
        self.channels = FeedChannelBar(dev)
        self.coupon = CouponBar(dev)

    # ------------------------------------------------------------------ #
    # 页面动作
    # ------------------------------------------------------------------ #
    def clean_popups(self, rounds: int = 3) -> int:
        """清掉首页各类浮层（开屏广告、更新提示、活动蒙层）。"""
        return self.popups.dismiss(rounds=rounds)

    def search(self, keyword: str) -> SearchResultPage:
        """首页搜索：点搜索入口 → 输入关键词 → 提交，返回搜索结果页。"""
        from pages.search_result_page import SearchResultPage

        logger.info("首页搜索: %r", keyword)
        self.search_bar.search_phrase(keyword)
        return SearchResultPage(self.dev).wait_ready()

    def open_cart(self) -> CartPage:
        """通过底部 tab 进入购物车。"""
        from pages.cart_page import CartPage

        self.tab.goto("cart")
        return CartPage(self.dev).wait_ready()

    def goto_mine(self) -> None:
        self.tab.goto("mine")

    # ------------------------------------------------------------------ #
    # 推荐页：频道切换 / 刷新
    # ------------------------------------------------------------------ #
    def current_channel(self) -> str:
        """当前选中的顶部频道名（默认「推荐」；识别不到返回空串）。"""
        return self.channels.current()

    def select_channel(self, channel: str = "推荐") -> None:
        """切换顶部频道（关注 / 推荐 / 闪购 / 双11 …）。"""
        self.channels.select(channel)

    def refresh_feed(self) -> None:
        """下拉刷新推荐流，并给内容重新加载留出随机等待时间。"""
        logger.info("下拉刷新推荐流")
        self.dev.swipe_direction("down", scale=0.55)
        self.dev.wait_long("下拉刷新后等推荐流加载")

    def feed_texts(self) -> list[str]:
        """推荐流里原生可读的文案（商品卡片主体是 Weex，通常拿不到，返回可能很少）。"""
        texts: list[str] = []
        try:
            for element in self.dev.driver.find_elements("xpath", "//*[@text or @content-desc]"):
                if not element.is_displayed():
                    continue
                value = (element.text or element.get_attribute("content-desc") or "").strip()
                if value:
                    texts.append(value)
        except Exception:  # noqa: BLE001 - 商品流节点不稳定，读取失败按「读不到」处理
            logger.warning("推荐流文案读取失败（可能整块是 Weex 容器）")
        return texts

    def capture_feed(self, tag: str = "feed") -> Path:
        """推荐流截图留证（商品流内容只能靠截图复核）。"""
        return self.screenshot(tag)

    # ------------------------------------------------------------------ #
    # 金币入口（活动位，可能不存在）
    # ------------------------------------------------------------------ #
    def has_coin_entry(self, timeout: float = 2.0) -> bool:
        """推荐页是否有「领淘金币」挂件（活动位，双 11/618 可能整块换皮肤）。"""
        return self.coin.available(timeout=timeout)

    def coin_entry_text(self) -> str:
        """金币挂件的完整 desc（例如「领淘金币」），不存在返回空串。"""
        return self.coin.entry_text()

    def open_coin_center(self, timeout: float = 5.0) -> CoinPage:
        """点击「领淘金币」进入金币中心；入口不存在时抛 PageNotReadyError。"""
        return self.coin.open(timeout=timeout)

    # ------------------------------------------------------------------ #
    # 领券条
    # ------------------------------------------------------------------ #
    def has_coupon_bar(self, timeout: float = 1.5) -> bool:
        return self.coupon.present(timeout=timeout)

    def claim_coupon(self) -> bool:
        """领取底部横条上的优惠券（有业务副作用）。"""
        return self.coupon.claim()
