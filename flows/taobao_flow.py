"""淘宝业务流：把页面对象编排成可复用的端到端链路。

分层职责
--------
- pages   ：单个页面能做什么（点这里、读那个）
- flows   ：跨页面的一段业务（启动→搜索→进详情→加购→回购物车校验）
- tests   ：只做「调用业务流 + 断言结果」

每一步都包在 ``core.report.step()`` 里 —— 这样报告里能看到「走通了 7 步、
第 5 步加购花了 4.2s」这种信息，而不是只看到一句断言失败。
没有挂报告插件时 ``step()`` 是透明 no-op，脚本单独调用也不会受影响。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from selenium.common.exceptions import WebDriverException

from core.android.actions import AndroidDevice
from core.logger import get_logger
from core.report import note, step
from data.constant.taobao import DEFAULT_KEYWORD, TAOBAO_PACKAGE
from pages.base_page import PageNotReadyError
from pages.cart_page import CartPage
from pages.detail_page import DetailPage
from pages.home_page import HomePage
from pages.search_result_page import SearchResultPage


class FlowError(RuntimeError):
    """业务流在某个步骤无法继续（前置条件不满足）。"""


@dataclass
class ProductSnapshot:
    """商品快照。

    数据来源优先取**搜索结果卡片**：淘宝详情页的标题/价格在 Weex 里，
    原生读不到；而结果卡片的 ``content-desc`` 恰好是
    ``标题 272.00元 5000+人付款`` 这种规整格式，反而更可靠。
    """

    title: str = ""
    price: str = ""
    #: 是否从结果卡片解析得到（False 表示回退到详情页且没读到）
    from_card: bool = False

    @property
    def short_title(self) -> str:
        """取标题前几个字做匹配（避免全标题因省略号/换行匹配不上）。"""
        return self.title[:8]


@dataclass
class CartFlowResult:
    """「搜索 → 加购 → 购物车校验」整条链路的结果。"""

    keyword: str = ""
    product: ProductSnapshot = field(default_factory=ProductSnapshot)
    #: 加购前 / 后的购物车角标（原生可读，是最可靠的加购判据）
    badge_before: int = 0
    badge_after: int = 0
    #: 是否成功加入购物车
    add_ok: bool = False
    #: 结果页识别到的商品卡片数量
    result_count: int = 0
    #: 是否成功进入购物车页
    cart_reached: bool = False
    #: 购物车截图存档路径（Weex 内容无法用选择器读，留证供复核）
    cart_screenshot: Path | None = None
    #: OCR 读到的件数（-1 表示无 OCR 环境，不可测）
    cart_item_count: int = -1
    #: OCR 读到的合计金额
    cart_total: str = ""

    @property
    def badge_increased(self) -> bool:
        return self.badge_after > self.badge_before


class TaobaoFlow:
    """淘宝端到端业务流。"""

    def __init__(self, dev: AndroidDevice, package: str = TAOBAO_PACKAGE) -> None:
        self.dev = dev
        self.package = package
        self.log = get_logger("flows.taobao")

    # ------------------------------------------------------------------ #
    # 单步
    # ------------------------------------------------------------------ #
    def start(self, fresh: bool = False, timeout: float = 40.0) -> HomePage:
        """启动 App（可选先杀进程）→ 回到首页 → 清浮层。"""
        with step("启动淘宝并等待首页就绪", screenshot_on_error=self.dev):
            if fresh:
                try:
                    self.dev.terminate_app(self.package)
                    time.sleep(1.0)
                except WebDriverException:
                    self.log.warning("结束应用失败（忽略）")
            self.log.info("启动淘宝 %s", self.package)
            self.dev.launch_app(self.package)

            home = HomePage(self.dev)
            # 淘宝会记住上次停留的 tab，launch_app 常常直接落在购物车/我的淘宝。
            # 首页判据已改成排他的（只认搜索栏），所以这里能如实发现「不在首页」，
            # 再点一下底部「首页」tab 纠正。
            try:
                home.wait_ready(timeout=min(timeout, 20.0))
            except PageNotReadyError:
                self.log.warning("未落在首页，点击底部「首页」tab 纠正")
                home.tab.goto("home")
                home.wait_ready(timeout=timeout)

            closed = home.clean_popups()
            if closed:
                home.wait_ready(timeout=20.0)
        return home

    def search(self, keyword: str = DEFAULT_KEYWORD) -> SearchResultPage:
        """从首页搜索关键词，返回结果页（已确认有结果）。"""
        with step(f"搜索关键词「{keyword}」", screenshot_on_error=self.dev):
            home = HomePage(self.dev)
            home.wait_ready()
            result = home.search(keyword)
            if not result.has_results(timeout=15.0):
                raise FlowError(
                    f"搜索 {keyword!r} 后未拿到结果"
                    f"（当前 activity={result.activity}，可能被风控/无网络/卡片选择器失效）"
                )
        note(keyword=keyword)
        return result

    def open_product(self, index: int = 0) -> DetailPage:
        """从结果页打开第 index 个商品详情。"""
        with step(f"打开第 {index + 1} 个商品详情", screenshot_on_error=self.dev):
            result = SearchResultPage(self.dev)
            result.wait_ready()
            detail = result.open_product(index)
        return detail

    def read_product(self, result: SearchResultPage | None = None, index: int = 0) -> ProductSnapshot:
        """从结果卡片解析商品标题与价格。"""
        with step("解析商品标题与价格"):
            page = result or SearchResultPage(self.dev)
            title, price = page.product_at(index)
            snap = ProductSnapshot(title=title, price=price, from_card=bool(title))
            if snap.title:
                self.log.info("商品快照(卡片): title=%r price=%r", snap.title[:24], snap.price)
            else:
                self.log.warning("结果卡片未解析出商品信息（desc 格式可能已变化）")
        note(title=snap.short_title, price=snap.price)
        return snap

    def add_to_cart(self) -> tuple[bool, int, int]:
        """在详情页加入购物车，返回 (是否成功, 加购前角标, 加购后角标)。"""
        with step("加入购物车", screenshot_on_error=self.dev):
            detail = DetailPage(self.dev)
            before = detail.cart_badge()
            ok = detail.add_to_cart()
            after = detail.cart_badge()
        note(角标=f"{before} → {after}")
        return ok, before, after

    def goto_cart(self) -> CartPage:
        """进入购物车（优先详情页顶部入口，失败退到底部 tab）。"""
        with step("进入购物车", screenshot_on_error=self.dev):
            cart: CartPage | None = None
            try:
                cart = DetailPage(self.dev).open_cart()
            except PageNotReadyError:
                self.log.warning("详情页内购物车入口不可用，改用底部 tab")
                cart = HomePage(self.dev).open_cart()
        return cart

    # ------------------------------------------------------------------ #
    # 组合链路
    # ------------------------------------------------------------------ #
    def search_and_add_to_cart(self, keyword: str = DEFAULT_KEYWORD) -> CartFlowResult:
        """完整链路：启动 → 搜索 → 解析商品 → 进详情 → 加购 → 购物车留证。"""
        result = CartFlowResult(keyword=keyword)

        self.start()
        result_page = self.search(keyword)
        result.result_count = result_page.result_count()
        result.product = self.read_product(result_page, index=0)

        self.open_product(0)
        result.add_ok, result.badge_before, result.badge_after = self.add_to_cart()

        cart = self.goto_cart()
        result.cart_reached = cart.is_here()
        result.cart_screenshot = cart.capture("flow_after_add")
        result.cart_item_count = cart.item_count_by_ocr()
        result.cart_total = cart.total_price_by_ocr()

        self.log.info(
            "链路结果: keyword=%r count=%d title=%r price=%r badge=%d->%d add_ok=%s cart=%s",
            keyword,
            result.result_count,
            result.product.short_title,
            result.product.price,
            result.badge_before,
            result.badge_after,
            result.add_ok,
            result.cart_reached,
        )
        return result

    def search_only(self, keyword: str = DEFAULT_KEYWORD) -> tuple[SearchResultPage, int]:
        """只跑「启动 → 搜索」，返回 (结果页, 卡片数)。"""
        self.start()
        page = self.search(keyword)
        return page, page.result_count()
