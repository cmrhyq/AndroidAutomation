"""大麦首页页面对象。

首页只做两件事：确认自己已经加载完、点开搜索入口。真正的搜索在
:class:`pages.damai.search_page.DamaiSearchPage` 里。

就绪判据用「搜索入口存在」而不是「首页文案存在」：大麦首页信息流会随
活动位整块换皮肤，文案天天变，但搜索入口的位置和 id 一直稳定
（2026-11 真机 dump 确认，见 ``artifacts/pages/damai_home_*.xml``）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.logger import get_logger
from data.constant.damai import HOME_READY_RESOURCE_IDS
from pages.base_damai import DamaiBasePage

if TYPE_CHECKING:
    from pages.damai.search_page import DamaiSearchPage

logger = get_logger(__name__)


def _home_ready_selectors() -> tuple[str, ...]:
    """首页就绪候选。任一命中即认为首页已加载。

    单独写函数而不是类体内推导式：类体作用域里拿不到同级的模块名，
    这样写更不容易踩坑。
    """
    return tuple(f"id={resource_id}" for resource_id in HOME_READY_RESOURCE_IDS)


class DamaiHomePage(DamaiBasePage):
    """大麦首页（信息流 + 顶部搜索入口）。"""

    name = "damai_home"
    ready_selectors = _home_ready_selectors()

    def ensure_ready(self, timeout: float | None = None) -> DamaiHomePage:
        """等首页就绪，顺手清掉挡路的浮层。

        浮层要清两轮：启动广告关掉后常常紧跟一个活动弹窗，只清一次会卡在
        第二个弹窗上，表现为「明明在首页却点不到搜索框」。
        """
        self.dismiss_popups(rounds=2)
        self.wait_ready(timeout=timeout)
        self.dismiss_popups(rounds=2)
        return self

    def open_search(self, timeout: float | None = None) -> DamaiSearchPage:
        """点顶部搜索入口，返回搜索页。"""
        from pages.damai.search_page import DamaiSearchPage

        logger.info("打开搜索页")
        self.tap_center(*self.ready_selectors, timeout=timeout)
        return DamaiSearchPage(self.dev, self.config)
