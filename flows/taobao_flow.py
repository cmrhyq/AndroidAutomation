"""淘宝业务流：把页面对象编排成可复用的端到端链路。

分层：pages 描述单页能力，flows 编排跨页业务，tests 只做调用与断言。
每个步骤包在 core.report.step() 里，报告能看到耗时分布；未挂插件时为 no-op。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from selenium.common.exceptions import WebDriverException

from core.android.actions import AndroidDevice
from core.logger import get_logger
from core.report import note, step
from data.constant.taobao import DEFAULT_KEYWORD, TAOBAO_PACKAGE
from pages.base_page import PageNotReadyError
from pages.cart_page import CartPage
from pages.coin_page import CoinPage
from pages.detail_page import DetailPage
from pages.home_page import HomePage
from pages.search_result_page import SearchResultPage


logger = get_logger(__name__)


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


@dataclass
class CoinFlowResult:
    """「推荐页领淘金币 → 金币中心签到」链路的结果。

    金币入口是**活动位**（双 11/618 期间推荐页会整块换皮肤），入口消失属于
    正常业务现象而不是 bug，所以这里用 ``entry_found`` 如实表达，而不是抛异常。
    """

    #: 推荐页是否找到「领淘金币」挂件
    entry_found: bool = False
    #: 挂件上的完整 desc（例如「领淘金币」）
    entry_text: str = ""
    #: 是否进入了金币中心页
    page_reached: bool = False
    #: 签到前 / 后的金币余额（-1 表示页面是 Weex/H5 容器，原生读不到）
    balance_before: int = -1
    balance_after: int = -1
    #: 金币页是否有原生可读文案（False = themis/Weex 渲染层，只能截图留证）
    content_readable: bool = False
    #: 本次是否真的点了签到（已签到过 / 无按钮都是 False）
    signed: bool = False
    #: 读到的任务名列表
    task_names: list[str] = field(default_factory=list)
    #: 金币中心截图（Weex 页面只能靠截图留证）
    screenshot: Path | None = None
    #: 入口不存在时的现场截图
    home_screenshot: Path | None = None
    #: 降级说明（入口不存在 / 页面读不到余额等）
    message: str = ""

    @property
    def balance_gained(self) -> int:
        """本次净增金币数（任一端读不到时返回 0，不代表没变化）。"""
        if self.balance_before < 0 or self.balance_after < 0:
            return 0
        return self.balance_after - self.balance_before

    @property
    def balance_readable(self) -> bool:
        return self.balance_before >= 0 or self.balance_after >= 0


class TaobaoFlow:
    """淘宝端到端业务流。"""

    #: 淘宝主框架 Activity（首页 / 购物车 / 我的淘宝都在这个容器内）。
    #: 只有在这里，元素查找才是「原生页面」的量级。
    MAIN_ACTIVITY_HINT = "welcome.Welcome"

    #: 这些页面的内容由 Weex / themis 渲染，``find_elements`` 与 ``page_source``
    #: 会长时间挂起（实测详情页曾卡死 9 分钟）。一旦落在这里，必须先用返回键退出，
    #: 否则后续任何 probe 都可能把整条用例拖死。
    HEAVY_ACTIVITY_HINTS: tuple[str, ...] = (
        "TTDetailActivity",
        "TMSActivity",
        "themis",
        "DetailActivity",
    )

    def __init__(self, dev: AndroidDevice, package: str = TAOBAO_PACKAGE) -> None:
        self.dev = dev
        self.package = package

    # ------------------------------------------------------------------ #
    # 前置纠偏
    # ------------------------------------------------------------------ #
    def escape_heavy_page(self, max_backs: int = 3) -> bool:
        """若落在 Weex/H5 重渲染页面，按返回键退回主框架。

        Returns:
            是否发生了返回动作。
        """
        escaped = False
        for _ in range(max_backs):
            activity = self.dev.current_activity or ""
            if not any(hint in activity for hint in self.HEAVY_ACTIVITY_HINTS):
                return escaped
            logger.warning("当前在重渲染页面（%s），元素查找会挂起，先按返回键退出", activity)
            self.dev.press("back")
            self.dev.wait_medium("返回后等页面切换")
            escaped = True
        return escaped

    # ------------------------------------------------------------------ #
    # 单步
    # ------------------------------------------------------------------ #
    def start(self, fresh: bool = False, timeout: float = 40.0) -> HomePage:
        """启动 App（可选先杀进程）→ 回到首页 → 清浮层。"""
        with step("启动淘宝并等待首页就绪", screenshot_on_error=self.dev):
            if fresh:
                try:
                    self.dev.terminate_app(self.package)
                    self.dev.wait_medium("应用进程结束后等系统回收")
                except WebDriverException:
                    logger.warning("结束应用失败（忽略）")
            logger.info("启动淘宝 %s", self.package)
            self.dev.launch_app(self.package)

            home = HomePage(self.dev)
            # 淘宝会「恢复上次页面」：上一次停在详情页 / 金币页时，launch_app 会直接
            # 回到那里。这类页面是 Weex/themis 渲染，随便一个 probe 都可能挂住，
            # 所以先退出来再谈「是否在首页」。
            self.escape_heavy_page()

            # 淘宝也会记住上次停留的 tab，launch_app 常常直接落在购物车/我的淘宝。
            # 首页判据已改成排他的（只认搜索栏），所以这里能如实发现「不在首页」，
            # 再点一下底部「首页」tab 纠正。
            try:
                home.wait_ready(timeout=min(timeout, 20.0))
            except PageNotReadyError:
                logger.warning("未落在首页，点击底部「首页」tab 纠正")
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
                logger.info("商品快照(卡片): title=%r price=%r", snap.title[:24], snap.price)
            else:
                logger.warning("结果卡片未解析出商品信息（desc 格式可能已变化）")
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
                logger.warning("详情页内购物车入口不可用，改用底部 tab")
                cart = HomePage(self.dev).open_cart()
        return cart

    # ------------------------------------------------------------------ #
    # 推荐页
    # ------------------------------------------------------------------ #
    def switch_channel(self, channel: str = "推荐") -> HomePage:
        """切换推荐页顶部频道（关注 / 推荐 / 闪购 / 双11 …）。"""
        with step(f"切换到「{channel}」频道", screenshot_on_error=self.dev):
            home = HomePage(self.dev)
            home.wait_ready()
            home.select_channel(channel)
        note(频道=channel, 当前=home.current_channel())
        return home

    def _ensure_recommend_channel(self, home: HomePage) -> None:
        """确保停在「推荐」频道。

        金币入口、领券条这类推荐页专属的运营位只在「推荐」频道渲染，
        从其它频道（例如上次退出时停在「关注」）直接找入口会一直落空。
        """
        current = home.current_channel()
        if current == "推荐":
            return
        logger.info("当前频道=%r，先切到「推荐」（金币/领券位只在此频道投放）", current or "未知")
        try:
            home.select_channel("推荐")
        except PageNotReadyError as exc:
            logger.warning("切换到「推荐」频道失败，继续在当前频道探测: %s", exc)

    def probe_coin_entry(self) -> tuple[bool, str, Path | None]:
        """只探测金币入口是否投放，**不进页**（适合做 AB 投放监控）。

        Returns:
            (是否存在, 入口文案, 不存在时的截图路径)
        """
        with step("探测推荐页金币入口"):
            home = HomePage(self.dev)
            home.wait_ready()
            self._ensure_recommend_channel(home)
            found = home.has_coin_entry(timeout=3.0)
            text = home.coin_entry_text() if found else ""
            shot = None if found else home.screenshot("no_coin_entry")
        if not found:
            logger.warning("金币入口未投放，截图=%s", shot)
        note(金币入口=text or "未投放")
        return found, text, shot

    # ------------------------------------------------------------------ #
    # 淘金币
    # ------------------------------------------------------------------ #
    def open_coin_center(self) -> CoinPage:
        """从推荐页点「领淘金币」进入金币中心（入口不存在时抛 PageNotReadyError）。"""
        with step("打开淘金币中心", screenshot_on_error=self.dev):
            home = HomePage(self.dev)
            home.wait_ready()
            coin = home.open_coin_center()
        return coin

    def claim_coins(self, do_first_task: bool = False, strict: bool = False) -> CoinFlowResult:
        """推荐页 → 领淘金币 → 金币中心签到（可选顺手做一个任务）。

        Args:
            do_first_task: 是否点第一个任务的「去完成」。
            strict: 入口不存在时是否抛 FlowError。默认 False —— 入口是活动位，双 11/618
                换皮肤后会消失，此时应返回 entry_found=False 让用例 skip。

        Returns:
            CoinFlowResult：入口是否找到、是否进页、签到前后余额、截图留证。
        """
        result = CoinFlowResult()

        with step("检查推荐页金币入口"):
            home = HomePage(self.dev)
            home.wait_ready()
            self._ensure_recommend_channel(home)
            result.entry_found = home.has_coin_entry(timeout=3.0)
            result.entry_text = home.coin.entry_text()

        if not result.entry_found:
            result.message = (
                "推荐页未找到「领淘金币」入口。该挂件是活动位（双 11/618 期间推荐页"
                "会整块换皮肤），当前可能未投放或未命中 AB 实验分组。"
            )
            result.home_screenshot = home.screenshot("no_coin_entry")
            if strict:
                raise FlowError(f"{result.message} 现场截图={result.home_screenshot}")
            logger.warning("%s 现场截图=%s", result.message, result.home_screenshot)
            note(金币入口="未投放")
            return result

        with step("进入金币中心并读取初始状态", screenshot_on_error=self.dev):
            coin = home.open_coin_center()
            result.page_reached = coin.wait_ready_soft(timeout=12.0)
            result.screenshot = coin.capture("center_before")
            # 一次遍历取余额 + 签到态 + 任务名（金币页元素上千，逐项读会慢几十秒）
            snap_before = coin.snapshot()
            result.balance_before = snap_before.balance
            result.task_names = snap_before.tasks
            result.content_readable = snap_before.readable
            if not snap_before.readable:
                logger.info("金币页无原生文案（渲染层页面），余额与任务列表不可读，以截图留证")

        with step("签到领金币", screenshot_on_error=self.dev):
            result.signed = coin.sign_in()
            result.balance_after = coin.snapshot().balance
            if not result.signed:
                logger.info("未产生签到动作（已签到过或按钮未命中）")
        coin.capture("center_after")

        if do_first_task:
            with step("执行首个金币任务", screenshot_on_error=self.dev):
                if not coin.do_task(0):
                    logger.warning("首个任务按钮未命中，跳过")

        logger.info(
            "金币链路: entry=%r reached=%s readable=%s signed=%s balance=%s->%s tasks=%d",
            result.entry_text,
            result.page_reached,
            result.content_readable,
            result.signed,
            result.balance_before,
            result.balance_after,
            len(result.task_names),
        )
        note(
            金币入口=result.entry_text or "未投放",
            进入金币中心=str(result.page_reached),
            签到=str(result.signed),
            余额=f"{result.balance_before} → {result.balance_after}",
        )
        return result

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

        logger.info(
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
