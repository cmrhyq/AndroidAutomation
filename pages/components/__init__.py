"""跨页复用组件。

组件与页面对象的区别：组件不绑定某个具体页面，可被多个页面复用。

大麦抢票链路的三件套：购买栏守卫、票档面板、观演人列表。它们同样跨页复用
（票档面板在详情页和 SKU 页都会出现）。
"""

from __future__ import annotations

from pages.components.damai_attendee_list import DamaiAttendeePanel
from pages.components.damai_buy_bar import DamaiBuyBar, is_safe_to_click
from pages.components.damai_price_panel import DamaiPricePanel

__all__ = [
    "DamaiAttendeePanel",
    "DamaiBuyBar",
    "DamaiPricePanel",
    "is_safe_to_click",
]
