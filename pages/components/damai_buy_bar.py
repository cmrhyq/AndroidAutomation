"""购票 CTA 安全守卫：只允许点击「开售后」的购买按钮。

大麦在开售前把同一个位置渲染成「预约抢票」，开售后才变成「立即购票」。
两者外观相似、位置相同，点错就进入完全不同的流程（提交预约而非下单），
白费一次抢票机会。

因此这里用**白名单 + 精确相等**：

- 只有 :data:`data.constant.damai.SAFE_TEXTS` 里的文案才算可点
- 空串、带空格、未知文案、黑名单文案一律拒绝（fail-closed）

不做 ``in`` 包含匹配 —— 「购买」被「预约购买」包含这类边角会出错，
精确相等最省心。

另一个形态是大麦 ≥9.0.2x 把 CTA 改成 **Canvas 自绘**：整个购买栏读不到任何
文案，只有容器 bounds。这时唯一可用的信号是「坐标存在 + 已到开售时间」，
由 :meth:`DamaiBuyBar.cta_center` 提供坐标，安全性由时间闸门保证。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from core.android import ui_tree
from core.logger import get_logger
from data.constant.damai import BLOCKED_TEXTS, BUY_BUTTON_RESOURCE_IDS, SAFE_TEXTS
from pages.base_damai import DamaiBasePage

logger = get_logger(__name__)


def is_safe_to_click(button_text: str | None) -> bool:
    """仅当文案精确等于已知安全购买文案时返回 ``True``。"""
    if not button_text:
        return False
    if button_text in SAFE_TEXTS:
        return True
    if button_text in BLOCKED_TEXTS:
        logger.warning("拒绝点击预约/预售文案: %s", button_text)
    else:
        logger.warning("未知按钮文案，按不可点处理: %s", button_text)
    return False


@dataclass(frozen=True)
class CtaState:
    """一次 CTA 读取的结果。"""

    resource_id: str
    #: 购买栏内全部文案拼接（Canvas 版本为空串）
    text: str
    center: tuple[int, int]

    @property
    def readable(self) -> bool:
        """读得到文案 —— ``False`` 说明很可能是 Canvas 自绘。"""
        return bool(self.text)

    @property
    def safe(self) -> bool:
        return is_safe_to_click(self.text)


class DamaiBuyBar(DamaiBasePage):
    """详情页底部购买栏。

    典型用法是 :meth:`wait_until_safe`（等开售）和 :meth:`tap`（点进购票流程），
    两者都会先确认文案安全，不会盲点。
    """

    name = "damai_buy_bar"
    ready_selectors: tuple[str, ...] = tuple(f"id={rid}" for rid in BUY_BUTTON_RESOURCE_IDS)

    def read(self) -> CtaState | None:
        """读 CTA 的文案与坐标；购买栏不在当前页面时返回 ``None``。

        文案取**整棵子树**的文本拼接而不是节点自身的 ``text``：
        大麦的按钮常是「容器 + 内部若干 TextView」结构，容器自身的 text
        往往是空的，只看它会误判成 Canvas 版本。
        """
        root = self.xml_root(ttl=0.2)
        if root is None:
            return None

        for resource_id in BUY_BUTTON_RESOURCE_IDS:
            for node in ui_tree.find_by_resource_id(root, resource_id):
                center = ui_tree.node_center(node)
                if center is None:
                    continue
                text = "".join(ui_tree.descendant_texts(node))
                return CtaState(resource_id=resource_id, text=text, center=center)
        return None

    def read_cta_text(self) -> str | None:
        state = self.read()
        return (state.text or None) if state else None

    def cta_center(self) -> tuple[int, int] | None:
        state = self.read()
        return state.center if state else None

    def wait_until_safe(self, timeout: float = 10.0, poll: float = 0.05) -> bool:
        """轮询直到文案变成安全购买文案。"""
        deadline = time.monotonic() + timeout
        last: str | None = None
        while True:
            last = self.read_cta_text()
            if last and is_safe_to_click(last):
                logger.info("检测到可点购买文案: %s", last)
                return True
            if time.monotonic() >= deadline:
                logger.warning("等待安全购买文案超时（最后读到: %r）", last)
                return False
            time.sleep(poll)

    def tap(self) -> bool:
        """点击购买入口，返回是否真的点了。

        文案可读时走白名单校验；读不到文案（Canvas 自绘）时按坐标点 ——
        此时安全性由调用方的开售时间闸门保证，不是这里。
        """
        state = self.read()
        if state is None:
            logger.warning("购买栏不在当前页面，无法点击")
            return False
        if state.readable and not state.safe:
            logger.warning("购买入口文案不安全，拒绝点击: %s", state.text)
            return False
        logger.info("点击购买入口 (%s) @ %s", state.resource_id, state.center)
        self.dev.tap_xy(*state.center)
        return True
