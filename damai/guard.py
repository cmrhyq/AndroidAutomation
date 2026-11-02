"""购票 CTA 安全守卫：只允许点击「开售后」的购买按钮。

大麦在开售前把同一个位置渲染成「预约抢票」，开售后才变成「立即购票」。
这两者外观相似、位置相同，一旦点错就进入完全不同的流程（提交预约而非下单），
抢票直接白费一次。

因此这里用**白名单 + 精确相等**判定：

- 只有 :data:`~damai.constants.SAFE_TEXTS` 里的文案才算可点
- 空串、带空格、未知文案、黑名单文案一律拒绝（fail-closed）

不做 ``in`` 包含匹配 —— "立即购票" 与 "预约抢票" 谁包含谁都不成立，
但 "购买" 包含于 "预约购买" 这类边角会出错，精确相等最省心。
"""

from __future__ import annotations

import time
from typing import Any

from core.logger import get_logger
from damai.constants import BLOCKED_TEXTS, BUY_BUTTON_RESOURCE_IDS, SAFE_TEXTS

__all__ = ["BLOCKED_TEXTS", "SAFE_TEXTS", "BuyButtonGuard", "is_safe_to_click"]

logger = get_logger("damai.guard")


def is_safe_to_click(button_text: str | None) -> bool:
    """仅当文案精确等于已知安全购买文案时返回 True。"""
    if not button_text:
        return False
    if button_text in SAFE_TEXTS:
        return True
    if button_text in BLOCKED_TEXTS:
        logger.warning("拒绝点击预约/预售文案: %s", button_text)
    else:
        logger.warning("未知按钮文案，按不可点处理: %s", button_text)
    return False


class BuyButtonGuard:
    """读取购买按钮状态并给出「能不能点」的判断。

    Args:
        device: uiautomator2 device。
    """

    def __init__(self, device: Any) -> None:
        self._device = device
        #: 最近一次命中的候选 resource-id，供结构化日志使用
        self._last_matched_resource_id: str | None = None

    @property
    def last_matched_resource_id(self) -> str | None:
        return self._last_matched_resource_id

    def is_safe_to_click(self, button_text: str | None) -> bool:
        return is_safe_to_click(button_text)

    def _find_buy_button(self) -> Any:
        """按候选顺序找购买入口；旧 ID 在前，保证 v8.x 一次命中。"""
        for resource_id in BUY_BUTTON_RESOURCE_IDS:
            try:
                element = self._device(resourceId=resource_id)
                if element.exists:
                    self._last_matched_resource_id = resource_id
                    return element
            except Exception:
                logger.debug("查找购买按钮失败 (resource-id=%s)", resource_id)
        self._last_matched_resource_id = None
        return None

    def read_cta_text(self) -> str | None:
        """读取购买按钮文案，读不到返回 ``None``。

        Canvas 自绘版本（≥9.0.2x）恒返回 ``None`` —— 这是「读不到」而不是
        「还没开售」，调用方需要靠 :meth:`cta_center` 区分。
        """
        element = self._find_buy_button()
        if element is None:
            return None
        try:
            text = element.get_text()
            return text if text else None
        except Exception:
            logger.debug("读取购买按钮文案失败")
            return None

    #: 源项目命名
    get_current_text = read_cta_text

    def cta_center(self) -> tuple[int, int] | None:
        """取 CTA 元素 bounds 中心坐标，用于 Canvas 自绘按钮的坐标兜底点击。"""
        for resource_id in BUY_BUTTON_RESOURCE_IDS:
            try:
                element = self._device(resourceId=resource_id)
                if not element.exists:
                    continue
                bounds = element.info["bounds"]
                self._last_matched_resource_id = resource_id
                return (
                    (int(bounds["left"]) + int(bounds["right"])) // 2,
                    (int(bounds["top"]) + int(bounds["bottom"])) // 2,
                )
            except Exception:
                logger.debug("读取 CTA 坐标失败 (resource-id=%s)", resource_id)
        return None

    #: 源项目命名
    get_cta_center_coords = cta_center

    def wait_until_safe(self, timeout_s: float = 10.0, poll_ms: int = 50) -> bool:
        """轮询直到文案变成安全购买文案。"""
        deadline = time.time() + timeout_s
        poll_s = poll_ms / 1000.0
        last_text: str | None = None
        while True:
            last_text = self.read_cta_text()
            if last_text and is_safe_to_click(last_text):
                logger.info("检测到可点购买文案: %s", last_text)
                return True
            if time.time() >= deadline:
                logger.warning("等待安全购买文案超时（最后读到: %s）", last_text)
                return False
            time.sleep(poll_s)
