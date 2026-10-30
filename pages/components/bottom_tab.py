"""底部导航栏组件（首页 / 购物车 / 我的淘宝）。

实测（PFGM00 / Android 12 / 淘宝 10.x）：底部 TabHost 占 y=2232~2400，
五个格子等分 1080 宽且**可点击属性都挂在 content-desc 上**（TextView 的
text 只有文案、不可点）。所以优先用 desc=，最后退化为相对坐标点击。
"""

from __future__ import annotations

from data.constant.taobao import TAB_CART_TEXTS, TAB_HOME_TEXTS, TAB_MINE_TEXTS
from pages.base_page import BasePage


class BottomTab(BasePage):
    """底部导航栏。"""

    name = "bottom_tab"

    #: tab 名 -> 候选文案
    TABS: dict[str, tuple[str, ...]] = {
        "home": TAB_HOME_TEXTS,
        "cart": TAB_CART_TEXTS,
        "mine": TAB_MINE_TEXTS,
    }

    #: 文案全部失效时的坐标兜底（相对屏幕宽高）。
    #: 实测 1080x2400：5 格等分，中心 rx = 0.1/0.3/0.5/0.7/0.9，ry = 0.965。
    FALLBACK_POINTS: dict[str, tuple[float, float]] = {
        "home": (0.10, 0.965),
        "cart": (0.70, 0.965),
        "mine": (0.90, 0.965),
    }

    def selectors_for(self, tab: str) -> tuple[str, ...]:
        texts = self.TABS.get(tab)
        if texts is None:
            raise ValueError(f"未知 tab {tab!r}，可用: {list(self.TABS)}")
        return tuple(f"desc={text}" for text in texts) + tuple(f"text={text}" for text in texts)

    def goto(self, tab: str, timeout: float = 8.0) -> None:
        """切到指定 tab。"""
        hit = self.exists_any(*self.selectors_for(tab), timeout=timeout)
        if hit:
            self.log.info("切换 tab: %s (%s)", tab, hit)
            self.dev.tap(hit)
        else:
            rx, ry = self.FALLBACK_POINTS[tab]
            self.log.warning("未找到 tab 文案，改用坐标点击 %s (%.2f, %.2f)", tab, rx, ry)
            self.dev.tap_relative(rx, ry)
        self.dev.wait_medium(f"切换 tab 到 {tab}")

    def is_visible(self, timeout: float = 5.0) -> bool:
        """底部导航栏是否出现（可用于判断是否还在主框架内）。"""
        return bool(self.exists_any(*self.selectors_for("home"), timeout=timeout))
