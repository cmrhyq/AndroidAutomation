"""推荐页「领淘金币」挂件组件。

为什么单独做成组件而不是塞进 ``HomePage``
------------------------------------------
1. 它是**活动位**，双 11 / 618 期间推荐页会整块换皮肤，入口可能不存在。
   独立组件才能把「不存在」当成正常分支处理，而不是污染首页主流程。
2. 同款挂件在「我的淘宝」等页面也会出现，做成组件后可复用。

定位要点（真机实测）
--------------------
``content-desc="领淘金币"`` 在层级里出现 3 次（外层容器 / 可点层 / 内部文案层），
没有 ``resource-id``。直接 ``desc=领淘金币`` 大概率命中不可点击的外层，
所以点击一律走 ``tap_center``（按命中元素中心坐标点），不依赖 ``clickable`` 属性。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from data.constant.taobao import COIN_ENTRY_DESCS
from pages.base_page import BasePage, PageNotReadyError

if TYPE_CHECKING:
    from pages.coin_page import CoinPage


class CoinWidget(BasePage):
    """推荐页金币挂件（含金币数 / 倒计时，点进去是金币中心）。"""

    name = "coin_widget"

    #: 入口候选：desc 精确匹配优先，再退到 contains。
    #: `desc~=淘金币` 会同时命中「领淘金币」「淘金币小镇」等变体，作为兜底。
    ENTRY_SELECTORS: tuple[str, ...] = (
        *tuple(f"desc={text}" for text in COIN_ENTRY_DESCS),
        "desc~=淘金币",
        "desc~=领金币",
        "text~=领淘金币",
    )

    #: 挂件上的数字（金币余额或倒计时）
    AMOUNT_PATTERN: re.Pattern[str] = re.compile(r"([\d][\d,]{1,})")

    # ------------------------------------------------------------------ #
    # 探测
    # ------------------------------------------------------------------ #
    def available(self, timeout: float = 2.0) -> bool:
        """入口当前是否存在（活动位下线 / 换皮肤时为 False，不抛异常）。"""
        return bool(self.exists_any(*self.ENTRY_SELECTORS, timeout=timeout))

    def entry_text(self) -> str:
        """入口的完整 desc（例如「领淘金币」），不存在返回空串。

        注意：这类挂件**只有 content-desc 没有 text**，必须走 ``text_or_desc``，
        否则会读到空串。
        """
        selector = self.exists_any(*self.ENTRY_SELECTORS, timeout=2.0)
        if not selector:
            return ""
        return self.text_or_desc(selector, timeout=2.0)

    def amount(self) -> int:
        """挂件上展示的金币数（读不到返回 -1）。

        真实挂件文案形如「1600／明日再来」，也可能只有倒计时「00:56」，
        所以这里只做「能解析出数字就返回」的宽松读取，不做强断言。
        """
        text = self.entry_text()
        if not text:
            return -1
        match = self.AMOUNT_PATTERN.search(text.replace(",", ""))
        return int(match.group(1)) if match else -1

    def visible_in_viewport(self) -> bool:
        """入口是否在当前可视区域内（存在但被滑出屏幕时返回 False）。"""
        rect = self.rect_of(self.wait_any(*self.ENTRY_SELECTORS, timeout=2.0))
        if not rect:
            return False
        screen = self.screen
        return rect["y"] >= 0 and rect["y"] < screen.height

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def open(self, timeout: float = 5.0) -> CoinPage:
        """点击挂件进入金币中心，返回金币中心页面对象。"""
        from pages.coin_page import CoinPage

        try:
            selector = self.wait_any(*self.ENTRY_SELECTORS, timeout=timeout)
        except PageNotReadyError as exc:
            raise PageNotReadyError(
                "推荐页未找到「领淘金币」入口。该挂件是活动位（双 11/618 期间可能整块换皮肤），"
                f"当前可能未投放。已尝试: {list(self.ENTRY_SELECTORS)}（activity={self.activity}）"
            ) from exc

        self.log.info("点击金币入口: %s", selector)
        self.tap_center(selector, timeout=timeout)
        return CoinPage(self.dev)
