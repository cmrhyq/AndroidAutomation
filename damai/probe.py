"""页面状态机：判断「现在停在哪一页」。

两种探测成本差一个数量级，必须分开用：

- **快探** (~100ms)：只读当前 Activity 名。开售前的轮询、重试前的判断用它。
- **全探** (~1.5s)：Activity + 元素存在性。只在真正需要控件就绪度时用。

结果带 TTL 缓存（默认 0.5s）—— 同一步骤里连续问三次不该问设备三次。
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from enum import Enum
from typing import Any

from core.logger import get_logger
from damai.constants import BUY_BAR_CONTAINER_ID, BUY_BUTTON_ID, SOLD_OUT_RE

__all__ = [
    "PageProbe",
    "PageState",
    "detect_price_panel_state",
    "is_buy_button_sold_out",
    "state_from_activity",
]

logger = get_logger("damai.probe")


class PageState(str, Enum):
    """页面状态。继承 ``str``，让 ``state == "sku_page"`` 这种比较继续成立。"""

    HOMEPAGE = "homepage"
    SEARCH_PAGE = "search_page"
    DETAIL_PAGE = "detail_page"
    SKU_PAGE = "sku_page"
    SESSION_PICKER = "session_picker"
    ORDER_CONFIRM_PAGE = "order_confirm_page"
    CONSENT_DIALOG = "consent_dialog"
    PENDING_ORDER_DIALOG = "pending_order_dialog"
    UNKNOWN = "unknown"


#: Activity 子串 → 页面状态。顺序有意义：先匹配具体页，再匹配兜底的 MainActivity。
_ACTIVITY_STATE_MAP: tuple[tuple[str, str], ...] = (
    ("ProjectDetail", PageState.DETAIL_PAGE.value),
    ("NcovSku", PageState.SKU_PAGE.value),
    ("MainActivity", PageState.HOMEPAGE.value),
    ("SearchActivity", PageState.SEARCH_PAGE.value),
)


def state_from_activity(activity: str) -> str | None:
    """由 Activity 名推断页面状态；不认识返回 ``None``（交由全探继续判断）。"""
    if not activity:
        return None
    for substring, state in _ACTIVITY_STATE_MAP:
        if substring in activity:
            return state
    return None


# ---------------------------------------------------------------------- #
# 票档面板 / 售罄
# ---------------------------------------------------------------------- #
_PRICE_LOADING_INDICATORS = (
    "cn.damai:id/loading_progress",
    "cn.damai:id/sku_loading",
    "cn.damai:id/loading_view",
)
_PRICE_READY_CONTAINERS = (
    "cn.damai:id/project_detail_perform_price_flowlayout",
    "cn.damai:id/layout_price",
)
_PRICE_SOLD_OUT_TEXTS = ("已售罄", "全部售罄", "无票", "暂无可售票档", "缺货登记")


def _safe_exists(device: Any, **selector: Any) -> bool:
    try:
        return bool(device(**selector).exists)
    except Exception:
        return False


def detect_price_panel_state(device: Any) -> str:
    """判断票档面板状态：``loading`` / ``sold_out`` / ``ready`` / ``unknown``。

    顺序是刻意安排的：先看 loading（面板可能还在渲染）、再看售罄
    （空卡片不能点）、最后才是 ready。
    """
    for resource_id in _PRICE_LOADING_INDICATORS:
        if _safe_exists(device, resourceId=resource_id):
            return "loading"

    for text in _PRICE_SOLD_OUT_TEXTS:
        if _safe_exists(device, text=text) or _safe_exists(device, textContains=text):
            return "sold_out"

    for container_id in _PRICE_READY_CONTAINERS:
        if _safe_exists(device, resourceId=container_id):
            return "ready"

    return "unknown"


def _node_shows_sold_out(node: ET.Element) -> bool:
    return bool(
        SOLD_OUT_RE.search(node.get("text", "")) or SOLD_OUT_RE.search(node.get("content-desc", ""))
    )


def is_buy_button_sold_out(xml: str | ET.Element | None) -> bool:
    """购买按钮/购买栏本身是否显示缺货。

    只看按钮节点，不看整页 —— 否则会被其它票档的「缺货登记」误判成整体售罄。

    注意遍历顺序：``iter("node")`` 是文档序（父先于子），购买栏容器往往就是
    ``btn_buy_view`` 的**祖先**。所以不能在首个命中的容器上提前返回，
    否则 v8.x 带文案的按钮会被空文案容器短路，漏检缺货。

    Canvas 自绘版本（≥9.0.2x）容器 text/content-desc 全为空，此时返回
    ``False`` —— 判不了就不判，绝不误报售罄。
    """
    if xml is None:
        return False
    if isinstance(xml, str):
        if not xml.strip():
            return False
        try:
            root: ET.Element | None = ET.fromstring(xml)
        except ET.ParseError:
            return False
    elif isinstance(xml, ET.Element):
        root = xml
    else:
        return False

    if root is None:
        return False

    container_node: ET.Element | None = None
    for node in root.iter("node"):
        resource_id = node.get("resource-id", "")
        if resource_id == BUY_BUTTON_ID:
            return _node_shows_sold_out(node)
        if container_node is None and resource_id == BUY_BAR_CONTAINER_ID:
            container_node = node

    if container_node is not None:
        return _node_shows_sold_out(container_node)
    return False


# ---------------------------------------------------------------------- #
# PageProbe
# ---------------------------------------------------------------------- #
_DEFAULT_RESULT: dict[str, Any] = {
    "state": PageState.UNKNOWN.value,
    "purchase_button": False,
    "price_container": False,
    "quantity_picker": False,
    "submit_button": False,
    "reservation_mode": False,
    "pending_order_dialog": False,
}

#: 详情页价格区容器候选。9.0.2x 起 project_detail_price_layout 已移除，
#: 改为 info_v2_price_layout（真机 dump 证实）。新 ID 在前，少一次元素查询。
_DETAIL_PRICE_LAYOUT_IDS = (
    "cn.damai:id/info_v2_price_layout",
    "cn.damai:id/project_detail_price_layout",
)

_SESSION_PICKER_RESOURCE_IDS = ("cn.damai:id/sku_panel_dates",)
_SESSION_PICKER_TEXTS = ("请选择场次", "选择日期", "请选择日期", "请选择演出日期")


def _make_result(**overrides: Any) -> dict[str, Any]:
    return {**_DEFAULT_RESULT, **overrides}


class PageProbe:
    """判断大麦 App 当前页面状态。

    Args:
        device: uiautomator2 device（或兼容的鸭子类型对象）。
        config: 抢票配置（仅用 ``app_package``）。
        cache_ttl_s: 探测结果缓存时长。
        unknown_threshold: 连续多少次 unknown 后落一份 XML 便于排查。
    """

    def __init__(
        self,
        device: Any,
        config: Any = None,
        cache_ttl_s: float = 0.5,
        *,
        unknown_threshold: int = 3,
    ) -> None:
        self._device = device
        self._config = config
        self._bot: Any = None
        self._cache_ttl_s = cache_ttl_s
        self._cached_result: dict[str, Any] | None = None
        self._cached_at: float = 0.0
        self._unknown_threshold = max(1, int(unknown_threshold))
        self._consecutive_unknown = 0
        self._forced_state: str | None = None

    def set_bot(self, bot: Any) -> None:
        """绑定编排器，用于委托 ``is_reservation_sku_mode()`` 这类判定。"""
        self._bot = bot

    # -- 公开接口 --
    def probe(self, fast: bool = False) -> dict[str, Any]:
        if self._forced_state is not None:
            result = _make_result(state=self._forced_state)
            self._cache(result)
            return result

        if self._cached_result is not None and (time.time() - self._cached_at) < self._cache_ttl_s:
            return self._cached_result

        result = self._probe_fast() if fast else self._probe_full()
        self._cache(result)
        self._track_unknown(result)
        return result

    #: 兼容源项目命名
    probe_current_page = probe

    def classify(self, fast: bool = False) -> dict[str, Any]:
        return self.probe(fast=fast)

    def force_state(self, state: PageState | str | None) -> None:
        """强制后续探测返回指定状态（恢复流程用），传 ``None`` 解除。"""
        if state is None:
            self._forced_state = None
            self.invalidate()
            return
        self._forced_state = state.value if isinstance(state, PageState) else str(state)
        self._consecutive_unknown = 0
        self.invalidate()

    def invalidate(self) -> None:
        self._cached_result = None
        self._cached_at = 0.0

    def get_current_activity(self) -> str:
        try:
            return (self._device.app_current() or {}).get("activity", "") or ""
        except Exception:
            logger.warning("读取当前 Activity 失败")
            return ""

    # -- 内部 --
    def _cache(self, result: dict[str, Any]) -> None:
        self._cached_result = result
        self._cached_at = time.time()

    def _track_unknown(self, result: dict[str, Any]) -> None:
        if result.get("state") != PageState.UNKNOWN.value:
            self._consecutive_unknown = 0
            return
        self._consecutive_unknown += 1
        if self._consecutive_unknown == self._unknown_threshold:
            logger.warning("连续 %d 次无法识别页面状态，正在落一份 UI 层级", self._consecutive_unknown)
            self._dump_unknown()

    def _dump_unknown(self) -> None:
        try:
            from damai.device import safe_dump_xml, write_dump

            xml = safe_dump_xml(self._device)
            if xml:
                path = write_dump(xml, "probe_unknown")
                logger.warning("UI 层级已保存: %s", path)
        except Exception as exc:  # pragma: no cover - 诊断信息尽力而为
            logger.debug("落 UI 层级失败: %s", exc)

    def _probe_fast(self) -> dict[str, Any]:
        activity = self.get_current_activity()
        state = state_from_activity(activity)
        if state is not None:
            return _make_result(state=state)
        return self._probe_full()

    def _probe_full(self) -> dict[str, Any]:
        activity = self.get_current_activity()

        if "ProjectDetail" in activity:
            return _make_result(
                state=PageState.DETAIL_PAGE.value,
                purchase_button=self._exists_by_resource_id(BUY_BAR_CONTAINER_ID),
                # price_container 不能留默认 False：probe 模式的就绪判定依赖它
                price_container=any(
                    self._exists_by_resource_id(rid) for rid in _DETAIL_PRICE_LAYOUT_IDS
                ),
            )

        if "NcovSku" in activity:
            if self._has_session_picker_markers():
                return _make_result(state=PageState.SESSION_PICKER.value, price_container=False)
            return _make_result(
                state=PageState.SKU_PAGE.value,
                price_container=True,
                quantity_picker=True,
                reservation_mode=self._check_reservation_mode(),
            )

        state = state_from_activity(activity)
        if state in (PageState.HOMEPAGE.value, PageState.SEARCH_PAGE.value):
            return _make_result(state=state)

        # 以下为「Activity 认不出来」时的元素兜底，顺序即优先级
        if self._exists_by_resource_id("cn.damai:id/id_boot_action_agree"):
            return _make_result(state=PageState.CONSENT_DIALOG.value)

        pending = self._exists_by_text_contains("未支付订单")

        submit = self._exists_by_text("立即提交")
        checkbox = self._exists_by_resource_id("cn.damai:id/checkbox")
        if submit or checkbox:
            return _make_result(
                state=PageState.ORDER_CONFIRM_PAGE.value,
                submit_button=submit,
                pending_order_dialog=pending,
            )

        if self._exists_by_resource_id("cn.damai:id/layout_sku") or self._exists_by_resource_id(
            "cn.damai:id/sku_contanier"
        ):
            if self._has_session_picker_markers():
                return _make_result(
                    state=PageState.SESSION_PICKER.value, pending_order_dialog=pending
                )
            return _make_result(
                state=PageState.SKU_PAGE.value,
                quantity_picker=True,
                pending_order_dialog=pending,
                reservation_mode=self._check_reservation_mode(),
            )

        purchase_bar = self._exists_by_resource_id(BUY_BAR_CONTAINER_ID)
        price_layout = any(self._exists_by_resource_id(rid) for rid in _DETAIL_PRICE_LAYOUT_IDS)
        if purchase_bar or price_layout or self._exists_by_resource_id("cn.damai:id/title_tv"):
            return _make_result(
                state=PageState.DETAIL_PAGE.value,
                purchase_button=purchase_bar,
                price_container=price_layout,
                pending_order_dialog=pending,
            )

        if self._exists_by_resource_id(
            "cn.damai:id/homepage_header_search"
        ) or self._exists_by_resource_id("cn.damai:id/pioneer_homepage_header_search_btn"):
            return _make_result(state=PageState.HOMEPAGE.value, pending_order_dialog=pending)

        if self._exists_by_resource_id("cn.damai:id/header_search_v2_input"):
            return _make_result(state=PageState.SEARCH_PAGE.value, pending_order_dialog=pending)

        if pending:
            return _make_result(state=PageState.UNKNOWN.value, pending_order_dialog=True)

        return _make_result()

    # -- 元素判断 --
    def _exists_by_resource_id(self, resource_id: str) -> bool:
        return _safe_exists(self._device, resourceId=resource_id)

    def _exists_by_text(self, text: str) -> bool:
        return _safe_exists(self._device, text=text)

    def _exists_by_text_contains(self, text: str) -> bool:
        return _safe_exists(self._device, textContains=text)

    def _has_session_picker_markers(self) -> bool:
        """多场次活动会先让你选场次，此时票档列表还没展开。"""
        for resource_id in _SESSION_PICKER_RESOURCE_IDS:
            if self._exists_by_resource_id(resource_id):
                return True
        return any(
            self._exists_by_text(text) or self._exists_by_text_contains(text)
            for text in _SESSION_PICKER_TEXTS
        )

    def _check_reservation_mode(self) -> bool:
        """当前 SKU 页是不是「预约」而不是「购买」。委托给编排器判断。"""
        if self._bot is not None:
            try:
                return bool(self._bot.is_reservation_sku_mode())
            except Exception:
                logger.debug("委托 is_reservation_sku_mode 失败，按非预约处理")
        return False
