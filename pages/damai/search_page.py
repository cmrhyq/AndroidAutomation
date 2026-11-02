"""大麦搜索结果页：输入关键词、解析结果卡、按配置选中目标演出。

这里是「抢错场」风险最集中的地方 —— 搜索「许嵩 演唱会」会同时返回十几个城市
的巡演，随便点第一张就是买错票。所以选卡不靠顺序，靠打分：

正向证据
    target_title 命中 +100、keyword 全等 +80 / 包含 +50、词元命中 +20/个、
    城市命中 +20、场馆命中 +30、相似度补分（最高 +50）

负向证据 → **一票否决**
    ``tv_project_city`` 字段写着另一个已知城市；关键词与标题都写了四位年份但不同

为什么负向必须是否决而不是扣分：正向证据叠起来轻松到 130+（target_title +100
+ keyword 包含 +50 + 词元），扣 60~80 分根本压不住阈值 60，罚则形同虚设。
而且城市字段是结构化字段，比从标题里猜城市可靠得多 —— 按项目既定原则
「负向信号永远优先于放松匹配」，直接否决。

点卡片用**坐标**而不是元素点击：卡片是层层嵌套的容器，元素点击容易命中外层
而跳错页面或毫无反应。
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from core.android import ui_tree
from core.logger import get_logger, log_event
from core.text import city_keyword, find_conflicting_city, normalize_text, title_similarity
from data.constant.damai import (
    CLICK_SCORE_THRESHOLD,
    SEARCH_INPUT_CLEAR_ID,
    SEARCH_INPUT_RESOURCE_IDS,
    SEARCH_RESULT_CITY_ID,
    SEARCH_RESULT_RESOURCE_ID,
    SEARCH_RESULT_TIME_ID,
    SEARCH_RESULT_TITLE_ID,
    SEARCH_RESULT_VENUE_ID,
    SIMILARITY_BONUS_MIN,
    TITLE_FUZZY_THRESHOLD,
    YEAR_PATTERN,
)
from data.entity.damai import SearchCard
from pages.base_damai import DamaiBasePage

if TYPE_CHECKING:
    from pages.damai.detail_page import DamaiDetailPage

logger = get_logger(__name__)


class SearchEmptyError(RuntimeError):
    """搜索没有返回任何结果。"""


class SearchAmbiguousError(RuntimeError):
    """有多张卡都像目标，无法唯一确定。"""


# --------------------------------------------------------------------------- #
# 解析（纯函数）
# --------------------------------------------------------------------------- #
_CARD_FIELD_IDS: tuple[str, ...] = (
    SEARCH_RESULT_TITLE_ID,
    SEARCH_RESULT_VENUE_ID,
    SEARCH_RESULT_CITY_ID,
    SEARCH_RESULT_TIME_ID,
)


def _card_fields(node: ET.Element) -> dict[str, str]:
    """把一张卡片的子字段读成 ``{resource_id: text}``。"""
    fields: dict[str, str] = {}
    for child in node.iter("node"):
        resource_id = ui_tree.resource_id_of(child)
        if resource_id in _CARD_FIELD_IDS and resource_id not in fields:
            fields[resource_id] = ui_tree.node_text(child)
    return fields


def parse_cards(root: ET.Element | None) -> list[SearchCard]:
    """从 UI 树里解析全部搜索结果卡。"""
    cards: list[SearchCard] = []
    for node in ui_tree.find_by_resource_id(root, SEARCH_RESULT_RESOURCE_ID):
        fields = _card_fields(node)
        cards.append(
            SearchCard(
                index=len(cards),
                title=fields.get(SEARCH_RESULT_TITLE_ID, ""),
                venue=fields.get(SEARCH_RESULT_VENUE_ID, ""),
                city=fields.get(SEARCH_RESULT_CITY_ID, ""),
                show_time=fields.get(SEARCH_RESULT_TIME_ID, ""),
                coords=ui_tree.node_center(node),
            )
        )
    return cards


# --------------------------------------------------------------------------- #
# 打分（纯函数）
# --------------------------------------------------------------------------- #
def keyword_tokens(keyword: str) -> list[str]:
    """关键词切词元：按空白拆，去空、去重、保留顺序。"""
    seen: set[str] = set()
    tokens: list[str] = []
    for raw in str(keyword or "").split():
        token = normalize_text(raw)
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


def title_matches_target(title: str, target_title: str) -> bool:
    """标题是否指向目标演出：包含优先，否则看模糊相似度。"""
    if not target_title or not title:
        return False
    normalized_title = normalize_text(title)
    normalized_target = normalize_text(target_title)
    if not normalized_title or not normalized_target:
        return False
    if normalized_target in normalized_title or normalized_title in normalized_target:
        return True
    return title_similarity(target_title, title) >= TITLE_FUZZY_THRESHOLD


def score_card(
    card: SearchCard,
    *,
    keyword: str,
    target_title: str = "",
    target_venue: str = "",
    city: str = "",
) -> int:
    """给一张搜索卡打分。负分表示被否决，绝不可点。"""
    normalized_title = normalize_text(card.title)
    if not normalized_title:
        return -1

    # ---- 负向信号先行：命中即否决，不给正向证据任何翻盘机会 ----
    if city and card.city:
        normalized_city_text = normalize_text(card.city)
        normalized_target_city = normalize_text(city_keyword(city))
        city_field_ok = bool(normalized_target_city) and normalized_target_city in normalized_city_text
        if not city_field_ok:
            conflict = find_conflicting_city(card.city, city)
            if conflict:
                log_event(
                    logger, "search_card_city_veto",
                    title=card.title, city_text=card.city,
                    target_city=city, conflict_city=conflict,
                )
                return -100

    if keyword:
        keyword_years = set(YEAR_PATTERN.findall(keyword))
        title_years = set(YEAR_PATTERN.findall(card.title))
        if keyword_years and title_years and not (keyword_years & title_years):
            log_event(
                logger, "search_card_year_veto",
                title=card.title, keyword_years=sorted(keyword_years),
                title_years=sorted(title_years),
            )
            return -100

    # ---- 正向证据 ----
    score = 0
    if title_matches_target(card.title, target_title):
        score += 100

    normalized_keyword = normalize_text(keyword)
    if normalized_keyword:
        if normalized_keyword == normalized_title:
            score += 80
        elif normalized_keyword in normalized_title:
            score += 50

    tokens = keyword_tokens(keyword)
    if tokens:
        hits = sum(1 for token in tokens if token in normalized_title)
        score += hits * 20
        if hits == len(tokens) and len(tokens) >= 2:
            score += 30

    if city:
        normalized_target_city = normalize_text(city_keyword(city))
        if normalized_target_city and normalized_target_city in normalized_title:
            score += 20
        # 城市字段对得上补 +10 而不是 +20：+20 会把「同城无关演出」精确推到
        # 阈值 60 上，等于把否决机制抹平。
        if card.city:
            score += 10

    if target_venue:
        normalized_venue = normalize_text(card.venue)
        normalized_target_venue = normalize_text(target_venue)
        if normalized_target_venue and normalized_target_venue in normalized_venue:
            score += 30

    # 相似度补分：「巡演」这类写法不含连写的「演唱会」，词元分不够过阈值
    if keyword:
        similarity = title_similarity(keyword, card.title)
        if similarity >= SIMILARITY_BONUS_MIN:
            score += int(50 * similarity)

    return score


# --------------------------------------------------------------------------- #
# 页面对象
# --------------------------------------------------------------------------- #
class DamaiSearchPage(DamaiBasePage):
    """大麦搜索页（输入框 + 结果列表）。"""

    name = "damai_search"

    # -------------------------------------------------------------- #
    # 输入
    # -------------------------------------------------------------- #
    def _input_selector(self, timeout: float = 8.0) -> str:
        """探测搜索输入框，返回命中的选择器。"""
        selectors = tuple(f"id={rid}" for rid in SEARCH_INPUT_RESOURCE_IDS)
        return self.wait_any(*selectors, timeout=timeout)

    def search(self, keyword: str, *, timeout: float = 8.0) -> DamaiSearchPage:
        """输入关键词并提交搜索。"""
        selector = self._input_selector(timeout=timeout)
        # 上一轮的关键词会留在框里，必须先清掉，否则变成「许嵩 演唱会周杰伦」
        if self.dev.exists(f"id={SEARCH_INPUT_CLEAR_ID}", timeout=0.5):
            self.tap_center(f"id={SEARCH_INPUT_CLEAR_ID}")
        self.type_text(selector, keyword)
        logger.info("提交搜索: %r", keyword)
        self.dev.press("enter")
        self.dev.invalidate_ui()
        return self

    # -------------------------------------------------------------- #
    # 结果
    # -------------------------------------------------------------- #
    def cards(self) -> list[SearchCard]:
        return parse_cards(self.xml_root(ttl=0.5))

    def wait_cards(self, timeout: float = 12.0, *, min_count: int = 1) -> list[SearchCard]:
        """等结果卡渲染出来。超时返回空列表（由调用方决定怎么处置）。"""
        deadline = time.monotonic() + timeout
        while True:
            cards = self.cards()
            if len(cards) >= min_count:
                log_event(logger, "search_results_ready", count=len(cards))
                return cards
            if time.monotonic() >= deadline:
                log_event(logger, "search_results_empty", level=logging.WARNING, timeout_s=timeout)
                return cards
            self.dev.wait_short("等搜索结果渲染")

    def score_all(self) -> list[SearchCard]:
        """给当前所有结果卡打分，返回带分数的新列表（保持页面顺序）。"""
        config = self.config
        keyword = str(getattr(config, "keyword", "") or "")
        target_title = str(getattr(config, "target_title", "") or "")
        target_venue = str(getattr(config, "target_venue", "") or "")
        city = str(getattr(config, "city", "") or "")
        scored: list[SearchCard] = []
        for card in self.cards():
            point = score_card(
                card, keyword=keyword, target_title=target_title,
                target_venue=target_venue, city=city,
            )
            scored.append(
                SearchCard(
                    index=card.index, title=card.title, venue=card.venue,
                    city=card.city, show_time=card.show_time,
                    coords=card.coords, score=point,
                )
            )
        return scored

    def pick_target(self, *, strict: bool = True) -> SearchCard:
        """选出分数最高的目标卡。

        Args:
            strict: 最高分并列时是否报错。默认报错 —— 并列说明配置不够具体，
                随便挑一张就是 50% 概率买错票。只有明确「随便哪个都行」时才关掉。

        Raises:
            SearchEmptyError: 没有任何结果卡。
            SearchAmbiguousError: 最高分未过阈值，或（strict 下）最高分并列。
        """
        scored = self.score_all()
        if not scored:
            raise SearchEmptyError("搜索结果为空")

        ranked = sorted(scored, key=lambda card: card.score, reverse=True)
        for card in scored:
            logger.info("候选 #%d 分数=%d 标题=%r 城市=%r", card.index, card.score, card.title[:30], card.city)

        best = ranked[0]
        if best.score < CLICK_SCORE_THRESHOLD:
            raise SearchAmbiguousError(
                f"最高分 {best.score} 未达阈值 {CLICK_SCORE_THRESHOLD}（{best.title!r}），"
                f"共 {len(scored)} 张候选，拒绝盲点"
            )
        tied = [card for card in ranked if card.score == best.score]
        if strict and len(tied) > 1:
            raise SearchAmbiguousError(
                f"{len(tied)} 张卡片同分 {best.score}，无法唯一确定目标："
                f"{[card.title[:20] for card in tied]}"
            )
        return best

    def open_card(self, card: SearchCard, *, timeout: float = 15.0) -> DamaiDetailPage:
        """点开指定结果卡，返回详情页。"""
        from pages.damai.detail_page import DamaiDetailPage

        if card.coords is None:
            raise SearchEmptyError(f"卡片 #{card.index} 没有可点坐标")
        logger.info("打开结果卡 #%d: %r", card.index, card.title[:30])
        self.dev.tap_xy(*card.coords)
        self.dev.invalidate_ui()
        return DamaiDetailPage(self.dev, self.config)
