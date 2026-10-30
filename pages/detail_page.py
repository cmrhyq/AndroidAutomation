"""淘宝商品详情页页面对象。

真机实测结构（PFGM00 / 淘宝 10.x / 1080x2400）
------------------------------------------------
**1. 加购按钮点不动是个坑**

    desc="加入购物车"  FrameLayout [397,2220][727,2400]  clickable=false

``clickable`` 是 false，用 WebDriver 的 ``element.click()`` 会静默失败。
必须按坐标点击 —— 所以走 ``tap_center``。

**2. SKU 规格浮层是 Weex，没有任何原生节点**

点底部「加入购物车」后会弹出「颜色分类 / 数量」浮层，把浮层打开时的
UI 层级导出来看，``text=加入购物车``、``desc=加入购物车`` 全部返回 0 个。
也就是说浮层内部完全拿不到选择器，只能按坐标点它底部那颗橙色按钮
（实测相对坐标约为 (0.5, 0.939)）。

**3. 浮层是否打开，有个免费判据**

浮层一旦弹出，底部原生栏（含 ``desc=加入购物车``）就查不到了。
于是 ``浮层打开 ⇔ desc=加入购物车 消失``，不用额外探测。

**4. 加购成功判据用购物车角标**

「加购成功」Toast 也在 Weex 里读不到，但顶部购物车图标右上角的数量角标
是**原生 TextView**（实测 ``text="1"`` @ rect x=913,y=132），可靠且无时序问题。

**5. 详情页 page_source 会卡**

详情页是 Weex + 视频，``driver.page_source`` 可能长时间不返回，
因此本页**不要**调用 ``dump()``，需要探测一律走 ``find_elements``。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from selenium.common.exceptions import WebDriverException

from pages.base_page import BasePage

if TYPE_CHECKING:
    from pages.cart_page import CartPage


class DetailPage(BasePage):
    """商品详情页（含底部购买栏与 SKU 选择浮层）。"""

    name = "detail"

    #: 底部购买栏是两个原生按钮：加入购物车（左）/ 领券购买（右）
    ready_selectors: tuple[str, ...] = (
        "desc=加入购物车",
        "desc~=领券购买",
        "desc~=立即购买",
    )

    ADD_CART_SELECTORS: tuple[str, ...] = (
        "desc=加入购物车",
        "desc~=加入购物车",
        "text=加入购物车",
    )

    #: 顶部工具栏的购物车入口（原生可点）
    OPEN_CART_SELECTORS: tuple[str, ...] = (
        "desc=购物车，按钮",
        "desc~=购物车",
    )

    #: 顶部标题栏返回按钮
    BACK_SELECTORS: tuple[str, ...] = ("desc=返回，按钮", "desc~=返回")

    #: SKU 浮层底部「加入购物车」按钮的相对坐标（Weex，无原生节点）
    SKU_CONFIRM_POINT: tuple[float, float] = (0.5, 0.939)

    #: 角标搜索范围：距购物车图标中心的最大偏移（px）
    _BADGE_RANGE = 110

    # ------------------------------------------------------------------ #
    # 状态判定
    # ------------------------------------------------------------------ #
    def sku_sheet_open(self) -> bool:
        """SKU 浮层是否打开：底部原生加购按钮消失即认为浮层已弹出。"""
        return not self.probe(*self.ADD_CART_SELECTORS)

    def cart_badge(self) -> int:
        """读取购物车角标数量（读不到返回 0）。

        角标是原生 TextView，贴在购物车图标右上角。这里用「数字文本 + 位置邻近」
        双重条件定位，避免误取页面上其它数字（价格、销量都含非纯数字字符）。
        """
        cart = self.rect_of(self.OPEN_CART_SELECTORS[0])
        if cart is None:
            return 0
        cx = cart["x"] + cart["width"] / 2
        cy = cart["y"] + cart["height"] / 2
        try:
            elements = self.dev.driver.find_elements("xpath", "//*[@text]")
        except WebDriverException:
            return 0
        for el in elements:
            try:
                text = (el.get_attribute("text") or "").strip()
            except WebDriverException:
                continue
            if not text.isdigit() or len(text) > 3:
                continue
            try:
                rect = el.rect
            except WebDriverException:
                continue
            ex = rect["x"] + rect["width"] / 2
            ey = rect["y"] + rect["height"] / 2
            if abs(ex - cx) <= self._BADGE_RANGE and abs(ey - cy) <= self._BADGE_RANGE:
                return int(text)
        return 0

    # ------------------------------------------------------------------ #
    # 加购
    # ------------------------------------------------------------------ #
    def add_to_cart(self, timeout: float = 15.0) -> bool:
        """加入购物车，返回是否成功（以购物车角标增长为准）。

        流程：点底部加购 → 等 SKU 浮层 → 坐标点浮层确认 → 校验角标。
        """
        baseline = self.cart_badge()
        self.log.info("加购前角标 = %d", baseline)

        self.tap_center(*self.ADD_CART_SELECTORS, timeout=timeout)
        # 等浮层弹出（原生加购按钮消失）
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if self.sku_sheet_open():
                self.log.info("SKU 浮层已弹出")
                break
            self.dev.wait_short("等 SKU 浮层弹出")
        else:
            self.log.warning("未检测到 SKU 浮层，可能该商品直接加购成功")

        if self.sku_sheet_open():
            # 浮层内是 Weex，只能按坐标点底部确认按钮
            self.tap_relative(*self.SKU_CONFIRM_POINT, reason="SKU 浮层确认按钮（Weex 无原生节点）")
            # 等浮层收起
            settle = time.monotonic() + 6.0
            while time.monotonic() < settle:
                if not self.sku_sheet_open():
                    break
                self.dev.wait_short("等 SKU 浮层收起")

        # 角标会有一小段延迟才刷新，轮询等它涨上来
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            current = self.cart_badge()
            if current > baseline:
                self.log.info("加购成功：角标 %d -> %d", baseline, current)
                return True
            self.dev.wait_short("等购物车角标刷新")

        self.log.warning("加购后角标未增长（baseline=%d, 当前=%d）", baseline, self.cart_badge())
        return False

    def open_cart(self, timeout: float = 10.0) -> CartPage:
        """从详情页顶部工具栏进购物车。"""
        from pages.cart_page import CartPage

        self.tap_center(*self.OPEN_CART_SELECTORS, timeout=timeout)
        return CartPage(self.dev).wait_ready(timeout=timeout)

    def go_back(self) -> None:
        """点标题栏返回（比 press('back') 稳，不会误触搜索框）。"""
        selector = self.exists_any(*self.BACK_SELECTORS, timeout=3.0)
        if selector:
            self.tap_center(selector)
        else:
            self.back()

    # ------------------------------------------------------------------ #
    # 覆写：详情页 page_source 极慢，禁止导出
    # ------------------------------------------------------------------ #
    def dump(self, tag: str = "ui"):  # type: ignore[override]
        """详情页禁用 UI 导出 —— Weex + 视频会让 page_source 长时间阻塞。"""
        self.log.warning("详情页 page_source 可能长时间阻塞，已跳过 dump（tag=%s）", tag)
        return None
