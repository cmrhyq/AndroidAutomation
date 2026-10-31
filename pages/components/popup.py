"""通用浮层清理组件。

淘宝启动后常年弹这几类东西：开屏广告、版本更新、隐私协议、新人引导、活动蒙层。
它们没有统一 id，只能按文案池轮询清理。清理策略是「能关就关，关不掉就退让」——
清理失败不抛异常，避免把主流程带崩。
"""

from __future__ import annotations

from selenium.common.exceptions import WebDriverException

from core.logger import get_logger
from data.constant.taobao import POPUP_CLOSE_TEXTS
from pages.base_page import BasePage


logger = get_logger(__name__)


class PopupCleaner(BasePage):
    """按文案池批量关闭浮层。"""

    name = "popup"

    #: 关闭类文案对应的选择器（精确匹配 + content-desc 兜底）
    CLOSE_SELECTORS: tuple[str, ...] = tuple(f"text={text}" for text in POPUP_CLOSE_TEXTS) + (
        "desc=关闭",
        "desc=跳过",
    )

    def dismiss(self, rounds: int = 3, timeout: float = 1.5) -> int:
        """连续尝试关闭浮层，返回实际关闭次数。

        多轮的原因：有些浮层是「关掉一个又弹一个」的串行结构（如更新提示 → 活动弹窗）。
        """
        closed = 0
        for _ in range(rounds):
            hit = self.exists_any(*self.CLOSE_SELECTORS, timeout=timeout)
            if not hit:
                break
            try:
                logger.info("关闭浮层: %s", hit)
                self.dev.tap(hit)
            except WebDriverException:
                # 浮层在这一瞬间自己消失了 / 元素失效，忽略即可
                logger.debug("浮层点击失效（忽略）: %s", hit)
                continue
            closed += 1
            self.dev.wait_short("等浮层关闭动画")
        if closed:
            logger.info("共关闭 %d 个浮层", closed)
        return closed

    def dismiss_until(self, done_selector: str, rounds: int = 4, timeout: float = 2.0) -> bool:
        """一直清浮层，直到目标元素出现。返回是否达成。"""
        for _ in range(rounds):
            if self.probe(done_selector):
                return True
            if not self.dismiss(rounds=1, timeout=timeout):
                self.dev.wait_short("等下一个浮层弹出")
        return bool(self.probe(done_selector))
