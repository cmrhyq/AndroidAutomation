"""推荐页底部领券条组件。

真机实测（``artifacts/pages/home_live.xml``）
--------------------------------------------
领券条是推荐页底部的横条，不是模态弹窗，会挡住商品流底部：

    desc='popViewIdentifier'  bounds=[0,2152][1080,2256]        ← 容器
    desc='您有'                bounds=[109,2181][183,2226]
    desc='满120减15'           bounds=[183,2181][357,2226]
    desc='国际加补券待领取'     bounds=[357,2181][653,2226]
    desc='立即领取'            bounds=[828,2181][976,2226]      ← 主按钮

它只在有券待领时出现，所以和其它浮层一样必须做「存在才处理」。
与 ``PopupCleaner`` 的分工：PopupCleaner 处理模态浮层（开屏广告/更新提示），
这里处理这条常驻业务横条 —— 它的「立即领取」是**有业务副作用**的动作，
所以默认只关闭不领取。
"""

from __future__ import annotations

from data.constant.taobao import (
    COUPON_BAR_CLAIM_DESCS,
    COUPON_BAR_CONTAINER_DESC,
    COUPON_BAR_HINTS,
)
from pages.base_page import BasePage


class CouponBar(BasePage):
    """推荐页底部「待领取优惠券」横条。"""

    name = "coupon_bar"

    #: 容器本体（desc 固定为 popViewIdentifier，实测最稳）
    CONTAINER_SELECTORS: tuple[str, ...] = (
        f"desc={COUPON_BAR_CONTAINER_DESC}",
        *(f"desc={text}" for text in COUPON_BAR_HINTS),
    )

    #: 领取按钮
    CLAIM_SELECTORS: tuple[str, ...] = tuple(f"desc={text}" for text in COUPON_BAR_CLAIM_DESCS)

    #: 关闭按钮
    CLOSE_SELECTORS: tuple[str, ...] = (
        "desc=关闭",
        "desc=暂不领取",
        "text=关闭",
    )

    def present(self, timeout: float = 1.5) -> bool:
        """当前是否有领券条（没有券时不存在，属正常）。"""
        return bool(self.exists_any(*self.CONTAINER_SELECTORS, timeout=timeout))

    def description(self) -> str:
        """读券面文案（例如「满120减15 国际加补券待领取」），读不到返回空串。

        券面同样是「只有 content-desc 没有 text」，所以走 ``text_or_desc``。
        """
        if not self.present():
            return ""
        parts: list[str] = []
        for hint in COUPON_BAR_HINTS:
            selector = f"desc={hint}"
            if self.probe(selector):
                value = self.text_or_desc(selector, timeout=1.0)
                if value:
                    parts.append(value)
        return " ".join(parts)

    def claim(self, timeout: float = 3.0) -> bool:
        """点击「立即领取」（有业务副作用，用例里要显式决定是否调用）。"""
        if not self.present():
            self.log.info("当前无领券条，跳过领取")
            return False
        selector = self.exists_any(*self.CLAIM_SELECTORS, timeout=timeout)
        if not selector:
            self.log.warning("领券条存在但未找到领取按钮，候选=%s", list(self.CLAIM_SELECTORS))
            return False
        self.log.info("点击领券: %s", selector)
        self.tap_center(selector, timeout=timeout)
        return True

    def dismiss(self, timeout: float = 2.0) -> bool:
        """关闭领券条（不领取）。找不到关闭按钮时返回 False，由调用方决定是否滑动避开。"""
        if not self.present():
            return False
        selector = self.exists_any(*self.CLOSE_SELECTORS, timeout=timeout)
        if not selector:
            self.log.info("领券条无关闭按钮（正常，多数版本只能领取或忽略）")
            return False
        self.tap_center(selector, timeout=timeout)
        return True
