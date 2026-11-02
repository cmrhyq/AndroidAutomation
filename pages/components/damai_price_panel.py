"""票档面板组件：枚举票档卡片、按配置票价点中目标档位。

卡片判据是结构性的，不是按 id 猜的：**价格容器的直接子节点里 clickable=true 的
FrameLayout**。大麦的票档卡就是这个形状；用「所有可点节点」会把整页按钮都算进来，
用某个固定 id 又会在改版后全灭。

选档不走 OCR。源项目在 UI 读不到价格时调 ImageMagick + Tesseract 裁剪识别，
需要两个外部二进制，且只在「文案被自定义 View 吞掉」时才有用。真机 9.0.37 的
票档文案在 UI 树里，走不到那条路；留着只是每轮多一次截图 + 4 个 psm 的 OCR
子进程开销，热路径上纯亏。

「预售 / 可预约」这类卡片必须当成不可选：它们看着能点，点下去进的是预约流程。
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Any

from core.android import ui_tree
from core.logger import get_logger, log_event
from core.text import normalize_text
from data.constant.damai import PRICE_CONTAINER_IDS, PRICE_UNIT_TEXTS, UNAVAILABLE_PRICE_TAGS
from data.entity.damai import PriceCard
from pages.base_damai import DamaiBasePage

logger = get_logger(__name__)

_PRICE_DIGITS_RE = re.compile(r"([1-9]\d{1,4})")


class PriceUnavailableError(RuntimeError):
    """目标票档不可选（售罄 / 预售 / 越界）。"""


class PricePanelMissingError(RuntimeError):
    """票档面板没出现。"""


# --------------------------------------------------------------------------- #
# 纯函数
# --------------------------------------------------------------------------- #
def extract_price_digits(text: Any) -> int | None:
    """从票价文案里抠出数字：``看台318`` → ``318``。"""
    match = _PRICE_DIGITS_RE.search(text if isinstance(text, str) else "")
    return int(match.group(1)) if match else None


def collapse_card_texts(texts: list[str] | tuple[str, ...]) -> str:
    """把一张卡片里的多段文案拼成一个票价标签。

    大麦把价格拆成 ``¥`` / ``318`` / ``起`` 三个 TextView，还夹着「惠」「荐」
    这类营销角标。角标必须丢掉，否则「惠」会被当成票档的一部分参与匹配。
    """
    parts = [
        str(text).strip()
        for text in texts
        if text and str(text).strip() and str(text).strip() not in PRICE_UNIT_TEXTS
    ]
    return "".join(parts)


def price_matches(card_text: str, target: str) -> bool:
    """票价文案是否指向目标票价。

    先做归一化后的互相包含（``看台318`` vs ``318``），再退回纯数字比较，
    以兼容「¥318 起」「318元」这类写法差异。
    """
    normalized_target = normalize_text(target)
    normalized_card = normalize_text(card_text)
    if (
        normalized_target
        and normalized_card
        and (normalized_target in normalized_card or normalized_card in normalized_target)
    ):
        return True
    target_digits = extract_price_digits(target)
    card_digits = extract_price_digits(card_text)
    return target_digits is not None and target_digits == card_digits


def parse_price_cards(root: ET.Element | None) -> list[PriceCard]:
    """从 UI 树枚举票档卡片。"""
    if root is None:
        return []

    container: ET.Element | None = None
    for container_id in PRICE_CONTAINER_IDS:
        found = ui_tree.find_by_resource_id(root, container_id)
        if found:
            container = found[0]
            break
    if container is None:
        return []

    cards: list[PriceCard] = []
    for child in container:
        if child.get("class") != "android.widget.FrameLayout":
            continue
        if not ui_tree.is_clickable(child):
            continue
        texts = ui_tree.descendant_texts(child)
        tag = next((text for text in texts if text in UNAVAILABLE_PRICE_TAGS), "")
        cards.append(
            PriceCard(
                index=len(cards),
                price_text=collapse_card_texts(texts),
                coords=ui_tree.node_center(child),
                tag=tag,
                raw_texts=tuple(texts),
            )
        )
    return cards


def detect_panel_state(root: ET.Element | None) -> str:
    """票档面板状态：``loading`` / ``sold_out`` / ``ready`` / ``unknown``。

    优先级：加载中 > 售罄 > 就绪 > 未知。「加载中」必须排在售罄前面 ——
    面板刚弹出时列表为空，很容易被误判成售罄而提前放弃。
    """
    if root is None:
        return "unknown"
    cards = parse_price_cards(root)
    if not cards:
        if ui_tree.find_by_text(root, "加载中", contains=True) or ui_tree.find_by_text(
            root, "请稍候", contains=True
        ):
            return "loading"
        return "unknown"
    if all(not card.available for card in cards):
        return "sold_out"
    return "ready"


# --------------------------------------------------------------------------- #
# 页面组件
# --------------------------------------------------------------------------- #
class DamaiPricePanel(DamaiBasePage):
    """票档选择面板（详情页底部或 SKU 页里）。"""

    name = "damai_price_panel"

    def cards(self) -> list[PriceCard]:
        return parse_price_cards(self.xml_root(ttl=0.3))

    def wait_cards(self, timeout: float = 10.0) -> list[PriceCard]:
        """等票档卡片出现。面板弹出生效需要一点时间。"""
        deadline = time.monotonic() + timeout
        while True:
            cards = self.cards()
            if cards:
                log_event(logger, "price_cards_ready", count=len(cards))
                return cards
            if time.monotonic() >= deadline:
                state = detect_panel_state(self.xml_root(ttl=0.0))
                log_event(logger, "price_cards_missing", panel_state=state, timeout_s=timeout)
                return cards
            self.dev.wait_short("等票档面板加载")

    def select(self, target_price: str | None = None, *, index: int | None = None) -> PriceCard:
        """选一张可用票档并点击，返回选中的卡片。

        优先级：文案匹配 → 配置索引 → 报错。文案优先是因为它可验证；
        索引只在「文案读不出来」时兜底。

        Raises:
            PricePanelMissingError: 面板没出现。
            PriceUnavailableError: 目标不可选 / 索引越界 / 全部售罄。
        """
        cards = self.wait_cards()
        if not cards:
            raise PricePanelMissingError("票档面板没有出现（没找到价格容器）")

        available = [card for card in cards if card.available]
        if not available:
            raise PriceUnavailableError(
                f"全部 {len(cards)} 个票档均不可选（售罄/预售）: "
                f"{[card.price_text for card in cards]}"
            )

        chosen: PriceCard | None = None
        if target_price:
            matches = [card for card in available if price_matches(card.price_text, target_price)]
            if matches:
                chosen = matches[0]
                log_event(
                    logger, "price_selected", match="text",
                    price=chosen.price_text, index=chosen.index,
                )

        if chosen is None and index is not None:
            if not (0 <= index < len(available)):
                raise PriceUnavailableError(
                    f"price_index={index} 越界（可选 {len(available)} 个: "
                    f"{[card.price_text for card in available]}）"
                )
            chosen = available[index]
            log_event(
                logger, "price_selected", match="index",
                price=chosen.price_text, index=chosen.index,
            )

        if chosen is None:
            raise PriceUnavailableError(
                f"没有匹配 {target_price!r} 的票档，可选: "
                f"{[card.price_text for card in available]}"
            )

        logger.info("点击票档 [%d] %r @ %s", chosen.index, chosen.price_text, chosen.coords)
        self.dev.tap_xy(*chosen.coords)  # type: ignore[misc]
        self.dev.invalidate_ui()
        return chosen
