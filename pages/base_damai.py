"""大麦页面对象基类：页面状态判定、浮层清理、UI 树复用。

与 ``pages/base_page.py`` 平级，而不是塞进 ``pages/damai/``：
``pages/components/`` 里的大麦组件也要继承它，放进子包会形成
components → pages.damai → pages.components 的循环导入。

继承 :class:`pages.base_page.BasePage`，只补大麦特有的三件事：

1. **状态判定**。大麦的页面跳转没有可靠的 URL，只能靠 Activity 子串 +
   元素特征组合判断。Activity 优先（一次 adb 查询），特征兜底（要 dump）。
2. **浮层清理**。启动广告、活动弹窗、版本更新提示会挡住搜索框，且**每次冷启动
   都可能不同**，所以不能靠固定坐标，只能按文案池逐个尝试。
3. **UI 树复用**。详情页 dump 一次 2s+，一轮流程里要查好几次，必须共用快照。

查找一律走显式等待（``BasePage`` 已保证 implicit wait 为 0）。
热路径上的重复点击用 ``dev.tap_cached``，TTL 内不再定位元素。
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET

from core.android import ui_tree
from core.logger import get_logger, log_event
from data.constant.damai import (
    BUY_BAR_CONTAINER_ID,
    BUY_BUTTON_ID,
    CONFIRM_SUBMIT_TEXTS,
    DAMAI_PACKAGE,
    POPUP_CLOSE_TEXTS,
    PRICE_CONTAINER_IDS,
    SESSION_DATE_RESOURCE_ID,
    SESSION_PANEL_RESOURCE_ID,
)
from data.entity.damai import PageState, state_from_activity
from pages.base_page import BasePage

logger = get_logger(__name__)


class DamaiBasePage(BasePage):
    """大麦所有页面对象的基类。"""

    package = DAMAI_PACKAGE
    default_timeout = 12.0

    def __init__(self, dev, config=None) -> None:
        super().__init__(dev)
        #: 业务配置（抢哪场、哪个票档）。可选 —— 基础页面操作不依赖它，
        #: 只有需要判断「是不是目标演出」的页面才用得上。
        self.config = config

    # ------------------------------------------------------------------ #
    # UI 树
    # ------------------------------------------------------------------ #
    def xml_root(self, *, ttl: float = 0.2) -> ET.Element | None:
        """当前 UI 树根节点。``ttl`` 秒内复用同一份快照。"""
        return ui_tree.parse_xml(self.dev.ui_xml(ttl=ttl))

    def refresh_xml(self) -> ET.Element | None:
        """强制重新 dump（点击之后调用）。"""
        self.dev.invalidate_ui()
        return self.xml_root(ttl=0.0)

    def texts_in(self, resource_id: str) -> list[str]:
        """某个容器内全部后代文案（含自身）。"""
        root = self.xml_root()
        texts: list[str] = []
        for node in ui_tree.find_by_resource_id(root, resource_id):
            texts.extend(ui_tree.descendant_texts(node))
        return texts

    def center_of(self, resource_id: str) -> tuple[int, int] | None:
        """按 resource-id 取第一个可见节点的中心坐标。"""
        root = self.xml_root()
        for node in ui_tree.find_by_resource_id(root, resource_id):
            center = ui_tree.node_center(node)
            if center:
                return center
        return None

    # ------------------------------------------------------------------ #
    # 状态判定
    # ------------------------------------------------------------------ #
    def state(self, *, fast: bool = False) -> PageState:
        """当前页面状态。

        Args:
            fast: 只按 Activity 判断，不做元素查询。轮询里用它可以省掉每次
                一两秒的 dump —— 页面跳转时 Activity 一定先变。
        """
        activity = self.dev.current_activity or ""
        guess = state_from_activity(activity)
        if fast:
            return PageState(guess) if guess else PageState.UNKNOWN

        # 场次面板 / 票档面板是最强的结构信号，优先于 Activity
        if self.dev.exists(f"id={SESSION_PANEL_RESOURCE_ID}", timeout=0.6) or self.dev.exists(
            f"id={SESSION_DATE_RESOURCE_ID}", timeout=0.6
        ):
            return PageState.SESSION_PICKER

        if any(self.dev.exists(f"text={text}", timeout=0.4) for text in CONFIRM_SUBMIT_TEXTS):
            return PageState.ORDER_CONFIRM_PAGE

        if self.dev.exists(f"id={BUY_BUTTON_ID}", timeout=0.6) or self.dev.exists(
            f"id={BUY_BAR_CONTAINER_ID}", timeout=0.6
        ):
            return PageState.DETAIL_PAGE

        if guess:
            return PageState(guess)
        return PageState.UNKNOWN

    def wait_state(self, target: PageState | str, timeout: float | None = None) -> bool:
        """等到进入指定状态，超时返回 ``False``（不抛异常）。"""
        wanted = PageState(target) if not isinstance(target, PageState) else target
        timeout = self.default_timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        while True:
            current = self.state(fast=True)
            if current is wanted:
                return True
            if time.monotonic() >= deadline:
                log_event(
                    logger, "state_wait_timeout",
                    expected=wanted.value, actual=current.value,
                    activity=self.dev.current_activity,
                )
                return False
            self.dev.wait_short("等页面切换")

    def is_reservation_mode(self) -> bool:
        """当前是不是「预约」流程而不是买票。

        判据：票档容器存在、但没有可点的购买按钮 —— 说明这一场还没开票，
        大麦把入口换成了预约登记。误进预约流会浪费整轮重试。
        """
        has_price_panel = any(
            self.dev.exists(f"id={resource_id}", timeout=0.5) for resource_id in PRICE_CONTAINER_IDS
        )
        if not has_price_panel:
            return False
        return not self.dev.exists(f"id={BUY_BUTTON_ID}", timeout=0.5)

    # ------------------------------------------------------------------ #
    # 浮层
    # ------------------------------------------------------------------ #
    def dismiss_popups(self, *, rounds: int = 3) -> int:
        """关掉挡路的浮层，返回关掉的个数。

        只点 :data:`POPUP_CLOSE_TEXTS` 里的无副作用文案。「取消 / 不同意 /
        退出」这类绝不点 —— 它们可能中止掉正常的业务流程。
        """
        closed = 0
        for _ in range(max(1, rounds)):
            hit = self.probe(*(f"text={text}" for text in POPUP_CLOSE_TEXTS))
            if not hit:
                break
            logger.info("关闭浮层: %s", hit)
            self.tap_center(hit)
            closed += 1
            self.dev.wait_short("浮层关闭后等动画")
        return closed
