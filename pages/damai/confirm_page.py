"""大麦确认订单页：勾观演人、提交、判定提交结果。

**提交是整条链路唯一不可撤销的动作**，所以这里的判定必须 fail-closed：

- 提交后验不到结果 → 返回 ``submit_unverified``，由上层直接终态退出（退出码 11），
  **绝不重试**。重试意味着可能下第二单。
- 只有明确看到收银台/待支付才算 ``submitted``。看到「库存不足」「已售罄」
  才允许上层继续重试。

不碰支付。检测到收银台就停手，剩下的交给人在手机上完成。
"""

from __future__ import annotations

import time

from core.logger import get_logger, log_event
from data.constant.damai import CONFIRM_SUBMIT_TEXTS
from pages.base_damai import DamaiBasePage
from pages.components.damai_attendee_list import DamaiAttendeePanel

logger = get_logger(__name__)

#: 进入收银台的 Activity 关键字。命中即认为下单成功。
_PAY_ACTIVITY_TOKENS: tuple[str, ...] = (
    "CashierDesk",
    "AlipayClient",
    "PayActivity",
    "PayResult",
    "OrderDetail",
)

#: 收银台/待支付页面的文案特征。
_PAY_PAGE_TEXTS: tuple[str, ...] = (
    "收银台",
    "待支付",
    "确认支付",
    "支付订单",
    "订单详情",
    "支付方式",
)

#: 提交被拒绝的特征（可以重试）。
_REJECT_TEXTS: tuple[str, ...] = (
    "库存不足",
    "已售罄",
    "无票",
    "该票档已售完",
    "请重新选择",
)

#: 需要人工处理的特征（不可重试）。
_BLOCKER_TEXTS: tuple[str, ...] = (
    "验证码",
    "安全验证",
    "拖动滑块",
    "请完成验证",
    "实名认证",
)


class SubmitBlockedError(RuntimeError):
    """提交前的前置条件不满足（观演人没勾上等）。"""


def _ready_selectors() -> tuple[str, ...]:
    return tuple(f"text={text}" for text in CONFIRM_SUBMIT_TEXTS)


class DamaiConfirmPage(DamaiBasePage):
    """确认订单页。"""

    name = "damai_confirm"
    ready_selectors = _ready_selectors()

    def attendees(self) -> DamaiAttendeePanel:
        return DamaiAttendeePanel(self.dev, self.config)

    # -------------------------------------------------------------- #
    # 前置校验
    # -------------------------------------------------------------- #
    def has_blocker(self) -> str | None:
        """有需要人工处理的拦截（验证码等）就返回它的文案。"""
        for text in _BLOCKER_TEXTS:
            if self.dev.exists(f"text~={text}", timeout=0.4):
                log_event(logger, "submit_blocker", text=text)
                return text
        return None

    def prepare(self) -> list[str]:
        """勾好观演人，返回实际勾上的姓名。"""
        names = list(getattr(self.config, "users", []) or [])
        panel = self.attendees()
        selected = panel.ensure_selected(names)
        required = panel.required()
        if required and len(selected) < required:
            raise SubmitBlockedError(
                f"页面要求 {required} 位观演人，实际只勾到 {len(selected)} 位: {selected}"
            )
        return selected

    # -------------------------------------------------------------- #
    # 提交
    # -------------------------------------------------------------- #
    def submit(self) -> None:
        """点「立即提交」。调用方必须自己保证模式是 SUBMIT。"""
        selector = self.wait_any(*self.ready_selectors, timeout=8.0)
        logger.info("提交订单: %s", selector)
        log_event(logger, "submit_clicked", selector=selector)
        self.tap_center(selector)
        self.dev.invalidate_ui()

    def wait_result(self, timeout: float = 15.0) -> str:
        """等提交结果，返回 ``submitted`` / ``rejected`` / ``blocked`` / ``unverified``。

        **只有明确证据才给肯定结论**：超时一律 ``unverified``，交给上层终态退出。
        """
        deadline = time.monotonic() + timeout
        last_activity = ""
        while time.monotonic() < deadline:
            activity = self.dev.current_activity or ""
            last_activity = activity or last_activity
            if any(token in activity for token in _PAY_ACTIVITY_TOKENS):
                log_event(logger, "submit_result", result="submitted", activity=activity)
                return "submitted"

            for text in _PAY_PAGE_TEXTS:
                if self.dev.exists(f"text~={text}", timeout=0.5):
                    log_event(logger, "submit_result", result="submitted", text=text)
                    return "submitted"

            blocker = self.has_blocker()
            if blocker:
                log_event(logger, "submit_result", result="blocked", text=blocker)
                return "blocked"

            for text in _REJECT_TEXTS:
                if self.dev.exists(f"text~={text}", timeout=0.4):
                    log_event(logger, "submit_result", result="rejected", text=text)
                    return "rejected"

            self.dev.wait_short("等提交结果")

        log_event(
            logger, "submit_result", result="unverified",
            activity=last_activity, timeout_s=timeout,
        )
        return "unverified"
