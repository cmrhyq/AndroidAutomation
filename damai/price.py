"""票档选择：在大麦 SKU/详情页上点中目标票价。

源项目的 ``PriceSelector`` 是个 1161 行的委托壳——自身逻辑放在 ``DamaiBot``
上，两边用 ``bot._click_price_option_by_config_index()`` 之类的互相回指。
这里改成自包含：坐标解析、卡片枚举、点击全部在本类内，只向 ``GrabHost``
要原语。行为差别只有两处，都是刻意砍掉的：

1. **OCR 兜底整个删除**。源项目在 UI 读不到价格时调 ImageMagick + Tesseract
   裁剪识别数字，需要两个外部二进制，且只在「卡片文案被自定义 View 吞掉」
   时才有用。真机 9.0.37 的票档卡片文案在 UI 树里，走不到那条路；留着只是
   每次多一次截图 + 4 个 psm 的 OCR 子进程开销，热路径上纯亏。
2. **Appium 分支删除**，只保留 u2 路径。

选档策略按「快 → 稳」排列，任一步成功即返回：

1. 配置索引直接点坐标（最快，不读文案）
2. 配置索引对应的可见卡片，文案匹配则点
3. 可见卡片里文案匹配的任意一张
4. 文本 ``textContains`` 快速命中
5. 兜底再按索引点一次
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from core.logger import get_logger
from damai.constants import UNAVAILABLE_PRICE_TAGS
from damai.primitives import ANDROID_UIAUTOMATOR
from damai.probe import detect_price_panel_state
from damai.text_utils import normalize_text

logger = get_logger("damai.price")

#: 票档卡片上的「不可选」标记 —— 出现任一即认为该档位点不了
UNAVAILABLE_TAGS: frozenset[str] = UNAVAILABLE_PRICE_TAGS

#: 拼接价格文案时要丢弃的装饰性文案
_DECORATIVE_TEXTS: frozenset[str] = frozenset({"惠", "荐", "热", "可选"})

#: 票价容器候选：详情页与 SKU 页布局不同，都要试
PRICE_CONTAINER_IDS: tuple[str, ...] = (
    "cn.damai:id/project_detail_perform_price_flowlayout",
    "cn.damai:id/layout_price",
)


class PriceSelectorError(RuntimeError):
    """票档定位失败：无卡片 / 索引越界 / 加载超时。"""


class SoldOutError(RuntimeError):
    """票档面板整体售罄。

    与越界分开是因为上层处置不同：售罄应当停止重试并退出，越界是配置错误。
    """


@dataclass(frozen=True)
class PriceCard:
    """一张可见票档卡片。"""

    index: int
    price_text: str
    coords: tuple[int, int] | None = None
    tag: str = ""
    raw_texts: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return self.tag not in UNAVAILABLE_TAGS


# ---------------------------------------------------------------------- #
# 纯函数：文案 → 票价 / 售罄判定
# ---------------------------------------------------------------------- #
def extract_price_digits(text: Any) -> int | None:
    """从票价文案里抠出数字，如 ``看台318`` → ``318``。"""
    match = re.search(r"([1-9]\d{1,4})", text if isinstance(text, str) else "")
    return int(match.group(1)) if match else None


def price_text_matches_target(text: Any, target: Any) -> bool:
    """可见票价文案是否指向目标票价。

    先做归一化后的互相包含（``看台318`` vs ``318``），再退回纯数字比较，
    以兼容「¥318 起」「318元」这类写法差异。
    """
    normalized_target = normalize_text(target)
    normalized_text = normalize_text(text)
    if (
        normalized_target
        and normalized_text
        and (
            normalized_target in normalized_text
            or normalized_text in normalized_target
        )
    ):
        return True
    target_digits = extract_price_digits(target)
    text_digits = extract_price_digits(text)
    return target_digits is not None and target_digits == text_digits


def collapse_card_texts(texts: list[str] | tuple[str, ...]) -> str:
    """把卡片里的多段文案拼成一条票价标签。

    大麦把价格拆成 ``¥`` / ``318`` / ``起`` 三个节点，还会混进「惠」「荐」
    这类角标。全丢掉角标后拼起来，再按形态补 ``元``。
    """
    filtered = [
        text.strip()
        for text in texts
        if text and text.strip() and text.strip() not in _DECORATIVE_TEXTS
    ]
    if not filtered:
        return ""

    merged = "".join(filtered)
    if merged.isdigit():
        return f"{merged}元"
    if re.fullmatch(r"[\u4e00-\u9fffA-Za-z]+[0-9]{2,5}", merged):
        return f"{merged}元"
    return merged


def price_cards_from_xml(xml_root: ET.Element | None) -> list[PriceCard]:
    """从层级树里枚举票档卡片。

    卡片判据是「价格容器的**直接子节点**且 ``clickable=true`` 的
    ``FrameLayout``」——大麦的票档卡就是这个结构，不能用「所有可点节点」，
    否则会把整页的按钮都算进来。
    """
    if xml_root is None:
        return []

    container = None
    for container_id in PRICE_CONTAINER_IDS:
        for node in xml_root.iter("node"):
            if node.get("resource-id") == container_id:
                container = node
                break
        if container is not None:
            break
    if container is None:
        return []

    cards: list[PriceCard] = []
    for child in container:
        if child.get("class") != "android.widget.FrameLayout":
            continue
        if child.get("clickable") != "true":
            continue

        texts: list[str] = []
        seen: set[str] = set()
        for desc in child.iter("node"):
            text = (desc.get("text") or "").strip()
            if text and text not in seen:
                texts.append(text)
                seen.add(text)

        tag = next((text for text in texts if text in UNAVAILABLE_TAGS), "")
        cards.append(
            PriceCard(
                index=len(cards),
                price_text=collapse_card_texts(texts),
                coords=bounds_center(child.get("bounds")),
                tag=tag,
                raw_texts=tuple(texts),
            )
        )
    return cards


def bounds_center(bounds_str: str | None) -> tuple[int, int] | None:
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str or "")
    if not match:
        return None
    left, top, right, bottom = map(int, match.groups())
    return ((left + right) // 2, (top + bottom) // 2)


def select_card_by_index(cards: list[PriceCard], index: int) -> PriceCard:
    """按索引取卡片，失败时把可用档位全列出来（配置错误时唯一有用的信息）。"""
    if not cards:
        raise PriceSelectorError(
            "未发现可点击票档卡片。可能原因：\n"
            "  1. 演出尚未开售\n"
            "  2. 价格区已售罄\n"
            "  3. 大麦 UI 变更，需更新 damai/constants.py"
        )
    if index < 0 or index >= len(cards):
        available = "\n".join(
            f"  [{card.index}] {card.price_text or '(空)'}"
            + (f" [{card.tag}]" if card.tag else "")
            for card in cards
        )
        raise PriceSelectorError(
            f"price_index={index} 越界（可用 0..{len(cards) - 1}）。\n"
            f"可用票档：\n{available}\n"
            "请修改 damai/config.jsonc 里的 price_index"
        )
    return cards[index]


# ---------------------------------------------------------------------- #
# 选择器
# ---------------------------------------------------------------------- #
class PriceSelector:
    """在详情页 / SKU 页上选中目标票档并读取购买按钮坐标。"""

    def __init__(self, host: Any) -> None:
        self._host = host

    @property
    def _config(self) -> Any:
        return self._host.config

    # -- 面板状态 -- #
    def await_price_panel(self, *, max_wait_s: float = 2.0) -> str:
        """采样票档面板状态，``loading`` 时最多阻塞 ``max_wait_s``。

        Raises:
            SoldOutError: 面板判定为售罄。
            PriceSelectorError: 一直停在 ``loading`` 超过超时。
        """
        state = detect_price_panel_state(self._host.d)
        if state == "loading":
            deadline = time.monotonic() + max_wait_s
            while time.monotonic() < deadline:
                time.sleep(0.1)
                state = detect_price_panel_state(self._host.d)
                if state != "loading":
                    break
            if state == "loading":
                raise PriceSelectorError(
                    f"票档面板加载超时（>{max_wait_s:.1f}s），疑似设备/网络异常"
                )

        if state == "sold_out":
            raise SoldOutError("票档面板检测到全部售罄")
        if state == "unknown":
            logger.warning("票档面板状态 unknown — 选择器可能已过期，继续按索引尝试")
        return state

    # -- 读取 -- #
    def cards(self, xml_root: ET.Element | None = None) -> list[PriceCard]:
        """当前可见票档卡片。不传 ``xml_root`` 时自行 dump 一次。"""
        if xml_root is None:
            xml_root = self._host._dump_hierarchy_xml()
        return price_cards_from_xml(xml_root)

    def selected_coords(
        self, xml_root: ET.Element | None = None
    ) -> tuple[int, int] | None:
        """配置索引对应卡片的中心坐标。"""
        if xml_root is None:
            xml_root = self._host._dump_hierarchy_xml()
        cards = price_cards_from_xml(xml_root)
        index = self._config.price_index
        if not (0 <= index < len(cards)):
            if cards:
                logger.debug(
                    "price_index=%s 超出 %d 张可点卡片", index, len(cards)
                )
            return None
        return cards[index].coords

    def buy_button_coords(
        self, xml_root: ET.Element | None = None
    ) -> tuple[int, int] | None:
        """购买 / 确认按钮的中心坐标。

        按钮被 Canvas 自绘时 ``btn_buy_view`` 不存在，此时退回购买栏容器
        的 bounds 中心——那条是固定高度的底栏，中心点必定落在按钮上。
        """
        from damai.constants import BUY_BUTTON_RESOURCE_IDS

        if xml_root is None:
            xml_root = self._host._dump_hierarchy_xml()
        if xml_root is None:
            return None

        found: dict[str, tuple[int, int]] = {}
        for node in xml_root.iter("node"):
            resource_id = node.get("resource-id", "")
            if resource_id in BUY_BUTTON_RESOURCE_IDS and resource_id not in found:
                center = bounds_center(node.get("bounds"))
                if center is not None:
                    found[resource_id] = center
                    if len(found) == len(BUY_BUTTON_RESOURCE_IDS):
                        break

        for resource_id in BUY_BUTTON_RESOURCE_IDS:
            if resource_id in found:
                return found[resource_id]
        return None

    # -- 点击 -- #
    def select(self, *, burst: bool = False) -> bool:
        """选中配置的票档。成功返回 ``True``。

        五步递进，每步失败才走下一步。``burst=True``（SUBMIT 模式）时
        第一优先的坐标点击会连点两次，抢在列表刷新的间隙里落地。
        """
        self.await_price_panel()
        price = self._config.price

        # 1. 坐标直点（最快）
        coords = self.selected_coords()
        if coords is not None:
            if burst:
                self._host._burst_click_coordinates(
                    *coords, count=2, interval_ms=25, duration=25
                )
            else:
                self._host._click_coordinates(*coords)
            logger.info("按索引直点票档: price_index=%s", self._config.price_index)
            return True

        # 2-4. 读卡片文案再点
        cards = self.cards()
        if cards:
            for card in cards:
                if card.index != self._config.price_index:
                    continue
                if not card.available:
                    logger.warning(
                        "配置索引对应票档不可选: %s [%s]",
                        card.price_text or "(未识别)",
                        card.tag or "不可售",
                    )
                    return False
                text_ok = not card.price_text or price_text_matches_target(
                    card.price_text, price
                )
                if text_ok and self._click_card(card):
                    logger.info(
                        "按索引点击可见票档: %s (price_index=%s)",
                        card.price_text or price,
                        self._config.price_index,
                    )
                    return True

            for card in cards:
                if not price_text_matches_target(card.price_text, price):
                    continue
                if not card.available:
                    logger.warning(
                        "目标票档不可选: %s [%s]",
                        card.price_text or "(未识别)",
                        card.tag or "不可售",
                    )
                    return False
                if self._click_card(card):
                    logger.info(
                        "按文案匹配点击票档: %s (index=%s)", card.price_text, card.index
                    )
                    return True

        # 4.5 文本直搜（卡片结构识别不出来时的旁路）
        if price and self._host.ultra_fast_click(
            ANDROID_UIAUTOMATOR,
            f'new UiSelector().textContains("{price}")',
            timeout=0.35,
        ):
            logger.info("按文本匹配点击票档: %s", price)
            return True

        logger.warning("无法定位 price_index=%s 的票档", self._config.price_index)
        return False

    def _click_card(self, card: PriceCard) -> bool:
        """点卡片：元素点击优先，坐标兜底。

        大麦部分自定义 View 不响应裸触摸坐标，只吃 Accessibility 点击，
        所以元素点击必须排在坐标前面。
        """
        if self._click_card_element(card.index):
            return True
        if card.coords is None:
            return False
        self._host._click_coordinates(*card.coords, duration=30)
        return True

    def _click_card_element(self, index: int) -> bool:
        try:
            container = self._host.d(
                resourceId=PRICE_CONTAINER_IDS[0],
            )
            if not container.exists:
                return False
            children = container.child(
                className="android.widget.FrameLayout", clickable=True
            )
            if children.count > index:
                children[index].click()
                return True
        except Exception as exc:  # noqa: BLE001 — 元素点击失败就走坐标兜底
            logger.debug("票档元素点击失败: %s", exc)
        return False
