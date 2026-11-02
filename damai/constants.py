"""大麦 App 的坐标常量：包名、Activity、resource-id、按钮文案。

集中在这里的唯一理由是**真机校准**：大麦改版时通常只动这几处（例如 9.0.2x
把购票按钮从 ``btn_buy_view`` 换成 Canvas 自绘的底部购买栏），改一处即可全链路生效。

文案池的语义要小心区分：「安全文案」是开售后才出现的可点购票入口，
「阻断文案」是开售前的预约/预售入口 —— 误点后者会进入完全不同的流程。
"""

from __future__ import annotations

import re

__all__ = [
    "BLOCKED_TEXTS",
    "BUY_BAR_CONTAINER_ID",
    "BUY_BUTTON_RESOURCE_IDS",
    "CTA_BLOCKED_KEYWORDS",
    "CTA_READY_KEYWORDS",
    "DAMAI_MAIN_ACTIVITY",
    "DAMAI_PACKAGE",
    "SAFE_TEXTS",
    "SALE_READY_TEXTS",
    "SOLD_OUT_RE",
    "UNAVAILABLE_PRICE_TAGS",
]

# ---------------------------------------------------------------------- #
# 应用标识
# ---------------------------------------------------------------------- #
DAMAI_PACKAGE = "cn.damai"
DAMAI_MAIN_ACTIVITY = ".launcher.splash.SplashMainActivity"


# ---------------------------------------------------------------------- #
# 购票 CTA
# ---------------------------------------------------------------------- #
#: 开售后才出现、可以安全点击的购票文案（精确相等比对，见 guard.is_safe_to_click）。
SAFE_TEXTS: frozenset[str] = frozenset(
    {
        "立即购买",
        "立即购票",
        "立即抢票",
        "立即预定",
        "立即预订",  # 大麦 2026-04 后新增
        "Book Now",  # 国际化兜底
        "选座购买",
        "购买",
        "抢票",
        "预定",
    }
)

#: 开售前的预约/预售文案 —— 点了会进入预约流程，必须拒绝。
BLOCKED_TEXTS: frozenset[str] = frozenset(
    {
        "预约抢票",
        "预约",
        "预售",
        "即将开抢",
        "待开售",
        "未开售",
        "提交抢票预约",
    }
)

#: 详情页「可以买」的文案前缀族（宽松包含匹配，用于开售等待的兜底信号）。
SALE_READY_TEXTS: tuple[str, ...] = (
    "立即购票",
    "立即预定",
    "立即预订",
    "立即抢票",
    "Book Now",
)

#: ``_is_sale_ready`` 认可的宽松关键字（含已进入 SKU/确认页的情形）。
CTA_READY_KEYWORDS: tuple[str, ...] = (
    *SALE_READY_TEXTS,
    "立即购买",
    "选座购买",
    "购买",
    "抢票",
    "预定",
    "提交订单",
    "去结算",
    "确定",
)

#: 出现任一即判定「还没开售」。
CTA_BLOCKED_KEYWORDS: tuple[str, ...] = (
    "预约",
    "预售",
    "即将开抢",
    "待开售",
    "未开售",
    "倒计时",
    "无票",
    "售罄",
    "缺货",
)

# ---------------------------------------------------------------------- #
# 购票入口节点
# ---------------------------------------------------------------------- #
#: v8.x 传统购买按钮（带文案，可做安全校验）。
BUY_BUTTON_ID = "cn.damai:id/btn_buy_view"
#: 大麦 ≥9.0.2x 详情页底部购买栏容器 —— CTA 变成 Canvas 自绘，读不到任何文案，
#: 只能靠 bounds 取中心坐标点击。
BUY_BAR_CONTAINER_ID = "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"

#: 候选顺序：旧 ID 在前，保证 v8.x 一次命中；Canvas 版本回退到购买栏容器。
BUY_BUTTON_RESOURCE_IDS: tuple[str, ...] = (BUY_BUTTON_ID, BUY_BAR_CONTAINER_ID)

#: 购买按钮区域出现这些字样即认为该票档不可买。
SOLD_OUT_RE = re.compile(r"缺货|售罄|无票")

#: 票档卡片上出现即视为不可选的标签。
UNAVAILABLE_PRICE_TAGS: frozenset[str] = frozenset(
    {
        "无票",
        "缺货",
        "缺货登记",
        "售罄",
        "已售罄",
        "不可选",
        "暂不可售",
    }
)
