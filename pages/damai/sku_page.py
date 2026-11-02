"""大麦 SKU 页（选座/选票档的中转页）。

从详情页点「立即购票」后的第一站。用户实际要做的只有两件事：选票档、点确定。
票档的枚举与点击逻辑在 :class:`pages.components.damai_price_panel.DamaiPricePanel`，
这里只负责「选完票档之后怎么往下走」。
"""

from __future__ import annotations

import time

from core.logger import get_logger, log_event
from data.constant.damai import SESSION_DATE_RESOURCE_ID, SESSION_PANEL_RESOURCE_ID
from pages.base_damai import DamaiBasePage
from pages.components.damai_price_panel import DamaiPricePanel

logger = get_logger(__name__)

#: 选完票档后的确认按钮文案。大麦不同版本写法有差异，按序尝试。
CONFIRM_TEXTS: tuple[str, ...] = ("确定", "确认", "下一步", "选好了")


class DamaiSkuPage(DamaiBasePage):
    """SKU 选票档页。"""

    name = "damai_sku"
    ready_selectors: tuple[str, ...] = (
        f"id={SESSION_PANEL_RESOURCE_ID}",
        f"id={SESSION_DATE_RESOURCE_ID}",
    )

    def price_panel(self) -> DamaiPricePanel:
        return DamaiPricePanel(self.dev, self.config)

    def select_price(self, target_price: str | None = None, *, index: int | None = None):
        """选票档，返回选中的卡片。"""
        return self.price_panel().select(target_price, index=index)

    def tap_confirm(self, *, timeout: float = 6.0) -> bool:
        """点确定往下走。找不到确认按钮返回 ``False``（部分票档选完自动跳转）。"""
        selectors = tuple(f"text={text}" for text in CONFIRM_TEXTS)
        deadline = time.monotonic() + timeout
        while True:
            hit = self.probe(*selectors)
            if hit:
                logger.info("SKU 页确认: %s", hit)
                self.tap_center(hit)
                self.dev.invalidate_ui()
                return True
            if time.monotonic() >= deadline:
                log_event(logger, "sku_confirm_missing", candidates=list(CONFIRM_TEXTS))
                return False
            self.dev.wait_short("等 SKU 确认按钮")
