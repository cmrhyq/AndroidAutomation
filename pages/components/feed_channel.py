"""推荐页顶部频道栏组件（关注 / 推荐 / 闪购 …）。

频道 desc 自带选中态后缀（「推荐,已选中」/「销量,未选中」），选中状态可直接断言；
个别频道（如「双11」）没有后缀，故候选里同时给带后缀与裸名两种写法。"""

from __future__ import annotations

import time

from core.logger import get_logger
from data.constant.taobao import (
    FEED_CHANNEL_SELECTED_HINT,
    FEED_CHANNEL_UNSELECTED_HINT,
    FEED_CHANNELS,
)
from pages.base_page import BasePage, PageNotReadyError


logger = get_logger(__name__)


class FeedChannelBar(BasePage):
    """推荐页顶部频道栏。"""

    name = "feed_channel"

    #: 频道名 -> 候选选择器（带状态后缀的精确匹配优先，再退裸名）
    def selectors_for(self, channel: str) -> tuple[str, ...]:
        return (
            f"desc={channel},{FEED_CHANNEL_SELECTED_HINT}",
            f"desc={channel},{FEED_CHANNEL_UNSELECTED_HINT}",
            f"desc={channel}",
        )

    def is_selected(self, channel: str) -> bool:
        """该频道当前是否处于选中态（读不到返回 False）。"""
        if self.probe(f"desc={channel},{FEED_CHANNEL_SELECTED_HINT}"):
            return True
        # 部分频道（如「双11」）没有状态后缀，退化为「存在且不带未选中标记」
        bare = self.probe(f"desc={channel}")
        return bool(bare) and not self.probe(f"desc={channel},{FEED_CHANNEL_UNSELECTED_HINT}")

    def current(self) -> str:
        """当前选中的频道名（识别不到返回空串）。"""
        for channel in FEED_CHANNELS:
            if self.is_selected(channel):
                return channel
        return ""

    def _wait_selected(self, channel: str, timeout: float) -> bool:
        """轮询等待某个频道变成选中态。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_selected(channel):
                return True
            self.dev.wait_short("等频道选中态刷新")
        return False

    def select(self, channel: str, timeout: float = 6.0, attempts: int = 3) -> None:
        """切到指定频道，并确认其进入选中态。

        为什么要重试：实测连续快速切换时，**第一次点击会被吞掉** ——
        日志里坐标点击执行了，但频道没变（前一个频道的商品流还在加载，
        点击被加载态蒙层吃掉）。所以这里做有限重试，而不是点一次就当成功。
        """
        selectors = self.selectors_for(channel)
        for attempt in range(1, attempts + 1):
            if not self.exists_any(*selectors, timeout=timeout):
                raise PageNotReadyError(
                    f"[{self.name}] 未找到频道 {channel!r}（候选 {list(selectors)}，activity={self.activity}）"
                )
            logger.info("切换到频道: %s（第 %d/%d 次）", channel, attempt, attempts)
            self.tap_center(*selectors, timeout=timeout)
            # 频道内容是懒加载的，切换后首个屏幕要重新渲染，给足 6s
            if self._wait_selected(channel, 6.0):
                return
            logger.warning("第 %d 次点击 %r 未生效，重试", attempt, channel)
            self.dev.wait_medium(f"重试切换频道 {channel}")
        raise PageNotReadyError(
            f"[{self.name}] 频道 {channel!r} 切换失败：{attempts} 次点击后选中态仍未变化（activity={self.activity}）"
        )
