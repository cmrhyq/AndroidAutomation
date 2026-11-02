"""从首页搜索到目标演出详情页，以及多场次选择。

这是整条链路里**唯一「宁可慢也不能错」**的一段：抢票开售前有几十秒可以
慢慢导航，一旦导到错的演出，后面所有动作都白做。因此这里的匹配逻辑明显
比别处保守：

- 城市冲突是**否决式**的，凌驾于所有「看起来挺像」的正向信号之上。
  标题写着别的城市，就算所有关键词都命中也不点。
- 年份冲突扣 60 分。同名跨年巡演太常见，光靠标题相似度分不开。
- 相似度兜底阈值 0.75，且只在前面的精确路径全失败后才启用。

源项目把这些逻辑挂在 ``DamaiBot`` 上（``_score_search_result``、
``_title_matches_target`` 等 10 个方法），``EventNavigator`` 只做转发，
两边回指。这里全部收进 ``EventNavigator``，配置与 ``GrabHost`` 直接从
构造参数拿，没有第二处真源。
"""

from __future__ import annotations

import contextlib
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

from selenium.webdriver.common.by import By

from core.logger import get_logger, log_event
from damai.constants import (
    HOME_READY_RESOURCE_IDS,
    SEARCH_INPUT_CLEAR_ID,
    SEARCH_INPUT_RESOURCE_IDS,
    SEARCH_RESULT_CITY_ID,
    SEARCH_RESULT_RESOURCE_ID,
    SEARCH_RESULT_TIME_ID,
    SEARCH_RESULT_TITLE_ID,
    SEARCH_RESULT_VENUE_ID,
    SESSION_DATE_RESOURCE_ID,
    SESSION_PANEL_RESOURCE_ID,
)
from damai.date_utils import normalize_date
from damai.page_helpers import (
    build_parent_map,
    find_clickable_ancestor,
    parse_bounds_center,
    read_detail_title,
    read_detail_venue,
)
from damai.primitives import ANDROID_UIAUTOMATOR
from damai.text_utils import (
    city_keyword,
    find_conflicting_city,
    normalize_text,
    title_similarity,
)

logger = get_logger("damai.navigator")

#: 标题模糊回退阈值。词序颠倒 0.87 / 官方词序 0.82 / 省略号截断 0.85 需通过，
#: 无关演出 ≤0.31 需拒绝 —— 0.75 落在两者之间。
TITLE_FUZZY_THRESHOLD = 0.75
#: 搜索卡点击分数阈值
CLICK_SCORE_THRESHOLD = 60
#: 相似度加分下限：巡演写法 0.48 需加分，无关演出 0.31 不加分
SIMILARITY_BONUS_MIN = 0.45

#: 年份冲突扣分（限 19xx/20xx，避免误伤票价类四位数字）
_YEAR_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")

#: 页面处在这些状态时才可能已经是目标
_TARGET_STATES = frozenset({"detail_page", "sku_page"})


class SessionNotFoundError(RuntimeError):
    """无法唯一确定目标场次。"""


# ---------------------------------------------------------------------- #
# 场次选择（纯函数式，只需要 driver）
# ---------------------------------------------------------------------- #
def enumerate_sessions_from_xml(xml_str: str) -> list[dict[str, Any]]:
    """从层级树里枚举多场次面板的日期卡片。"""
    if not xml_str:
        return []
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return []

    panel = None
    for node in root.iter("node"):
        if node.get("resource-id") == SESSION_PANEL_RESOURCE_ID:
            panel = node
            break
    if panel is None:
        return []

    parent_map = build_parent_map(root)
    cards: list[dict[str, Any]] = []
    for date_node in panel.iter("node"):
        if date_node.get("resource-id") != SESSION_DATE_RESOURCE_ID:
            continue
        # 日期节点本身通常不可点，真正可点的是它的某个祖先。
        # 这里必须显式判 None：ElementTree 的空元素布尔值为 False，
        # 用 ``or date_node`` 会在「祖先链上找不到可点节点」的合法情形下静默
        # fallback，把本该取祖先 bounds 的场景变成取日期节点 bounds。
        clickable = find_clickable_ancestor(parent_map, date_node)
        if clickable is None:
            clickable = date_node
        texts = [
            (desc.get("text") or "").strip()
            for desc in clickable.iter("node")
            if (desc.get("text") or "").strip()
        ]
        cards.append(
            {
                "index": len(cards),
                "date": (date_node.get("text") or "").strip(),
                "text": " ".join(texts),
                "bounds": clickable.get("bounds") or date_node.get("bounds"),
            }
        )
    return cards


def _date_equals(card_date: str, target_date: str) -> bool:
    if not card_date or not target_date:
        return False
    return (normalize_date(card_date) or card_date.strip()) == (
        normalize_date(target_date) or target_date.strip()
    )


def select_session(
    driver: Any,
    *,
    date: str | None = None,
    city: str | None = None,
    fallback_index: int | None = None,
) -> int:
    """在多场次面板上选一个场次，返回选中的零基索引。

    优先级：``date+city`` 唯一命中 → 仅 ``date`` 唯一命中 → ``fallback_index``。

    「唯一」是硬要求：命中多条时直接抛错而不是随便挑一条。选错场次买错票，
    比买不到更糟。

    Raises:
        SessionNotFoundError: 无卡片 / 命中多条 / 无可用判据。
    """
    try:
        xml_str = driver.dump_hierarchy()
    except Exception as exc:  # noqa: BLE001
        log_event(logger, "error", level=logging.ERROR, scene="select_session.dump", type=type(exc).__name__)
        raise SessionNotFoundError(f"dump_hierarchy 失败: {exc}") from exc

    cards = enumerate_sessions_from_xml(xml_str)
    if not cards:
        log_event(logger, "error", level=logging.ERROR, scene="select_session.no_cards")
        raise SessionNotFoundError("未发现可选场次（面板内没有日期节点）")

    chosen: dict[str, Any] | None = None

    if date and city:
        matches = [c for c in cards if _date_equals(c["date"], date) and city in c["text"]]
        if len(matches) == 1:
            chosen = matches[0]
            log_event(
                logger, "session_selected", match="date+city",
                date=date, city=city, index=chosen["index"], total=len(cards),
            )
        elif len(matches) > 1:
            raise SessionNotFoundError(
                f"date={date} + city={city} 命中 {len(matches)} 条场次，无法唯一确定"
            )

    if chosen is None and date:
        matches = [c for c in cards if _date_equals(c["date"], date)]
        if len(matches) == 1:
            chosen = matches[0]
            log_event(
                logger, "session_selected", match="date",
                date=date, city=None, index=chosen["index"], total=len(cards),
            )
        elif len(matches) > 1:
            raise SessionNotFoundError(
                f"date={date} 命中 {len(matches)} 条场次，需配合 city 才能确定"
            )

    if chosen is None and fallback_index is not None:
        if 0 <= fallback_index < len(cards):
            chosen = cards[fallback_index]
            log_event(
                logger, "session_selected", level=logging.WARNING,
                match="fallback_index", index=fallback_index, total=len(cards),
            )
        else:
            raise SessionNotFoundError(
                f"fallback_index={fallback_index} 越界（共 {len(cards)} 条场次）"
            )

    if chosen is None:
        raise SessionNotFoundError(
            f"未提供 date/city/fallback_index，无法选择场次（共 {len(cards)} 条候选）"
        )

    coords = parse_bounds_center(chosen.get("bounds"))
    if coords is None:
        raise SessionNotFoundError(f"无法解析场次卡片 bounds={chosen.get('bounds')!r}")
    try:
        driver.click(*coords)
    except Exception as exc:  # noqa: BLE001
        raise SessionNotFoundError(f"点击场次卡片失败: {exc}") from exc
    return chosen["index"]


# ---------------------------------------------------------------------- #
# 导航器
# ---------------------------------------------------------------------- #
class EventNavigator:
    """搜索并打开目标演出详情页。"""

    def __init__(self, host: Any) -> None:
        self._host = host
        #: 最终失败时的候选缓存，供失败文案列出 top-5 引导用户补标题
        self.last_failed_candidates: list[dict[str, Any]] = []

    @property
    def _config(self) -> Any:
        return self._host.config

    # ------------------------------------------------------------------ #
    # 文案匹配
    # ------------------------------------------------------------------ #
    def keyword_tokens(self) -> list[str]:
        """把配置里的 keyword 切成可用于模糊命中判断的词元。"""
        tokens: list[str] = []
        for raw in re.split(r"[\s,，、|/]+", self._config.keyword or ""):
            token = normalize_text(raw)
            if len(token) >= 2 and token not in tokens:
                tokens.append(token)
        return tokens

    def title_matches_target(self, title_text: str) -> bool:
        """标题是否指向目标演出。城市冲突一票否决。"""
        normalized_title = normalize_text(title_text)
        if not normalized_title:
            return False

        target_city = self._config.city if isinstance(self._config.city, str) else None
        if target_city:
            conflict_city = find_conflicting_city(normalized_title, target_city)
            if conflict_city:
                log_event(
                    logger, "title_city_conflict", level=logging.WARNING,
                    title=title_text, conflict_city=conflict_city, target_city=target_city,
                )
                return False

        candidates = [
            candidate
            for candidate in (self._config.target_title, self._config.keyword)
            if candidate
        ]
        for candidate in candidates:
            normalized_candidate = normalize_text(candidate)
            if not normalized_candidate:
                continue
            if (
                normalized_candidate in normalized_title
                or normalized_title in normalized_candidate
            ):
                return True

        tokens = self.keyword_tokens()
        if tokens and all(token in normalized_title for token in tokens):
            return True

        # 模糊回退：词序颠倒/短标题/UI 省略号截断
        best_similarity = 0.0
        best_candidate = None
        for candidate in candidates:
            similarity = title_similarity(candidate, title_text)
            if similarity > best_similarity:
                best_similarity = similarity
                best_candidate = candidate
        if best_similarity >= TITLE_FUZZY_THRESHOLD:
            log_event(
                logger, "title_fuzzy_match", title=title_text,
                candidate=best_candidate, similarity=round(best_similarity, 3),
            )
            return True
        return False

    def current_page_matches_target(
        self, page_probe: dict[str, Any], clicked_title: str | None = None
    ) -> bool:
        """当前详情/SKU 页是否就是目标演出。

        Args:
            page_probe: 探测结果。
            clicked_title: 刚点进去的那张搜索卡标题。详情页标题和搜索卡文案
                是两套东西，用「刚点的卡」做锚更宽容，能容忍词序/截断差异。
        """
        if page_probe.get("state") not in _TARGET_STATES:
            return False

        if not self._config.target_title and not self._config.keyword:
            return True

        detail_title = read_detail_title(self._host._dump_hierarchy_xml())

        if clicked_title:
            if not detail_title:
                # 详情页标题异步渲染，慢网络下可持续读空数秒。
                # 单次 0.3s 重试不够 —— 真机实测会误判 mismatch 把正确条目拉黑。
                for _ in range(6):
                    time.sleep(0.35)
                    detail_title = read_detail_title(self._host._dump_hierarchy_xml())
                    if detail_title:
                        break

            if not detail_title:
                log_event(
                    logger, "detail_title_empty", level=logging.WARNING,
                    clicked_title=clicked_title,
                )
                target_venue = normalize_text(self._config.target_venue or "")
                if target_venue:
                    detail_venue = normalize_text(
                        read_detail_venue(self._host._dump_hierarchy_xml()) or ""
                    )
                    if detail_venue and (
                        target_venue in detail_venue or detail_venue in target_venue
                    ):
                        log_event(
                            logger, "detail_title_verified",
                            match="venue_fallback", venue=detail_venue,
                        )
                        return True
                # 标题「读不到」≠「不一致」。clicked_title 已经过搜索卡评分和
                # 城市否决筛选，这里 reject 会把正确条目拉黑导致死循环。
                # 错误页面的风险交给下游场次/票档匹配兜底。
                log_event(
                    logger, "detail_title_unverified_accept", level=logging.WARNING,
                    clicked_title=clicked_title,
                )
                return True

            normalized_clicked = normalize_text(clicked_title)
            normalized_detail = normalize_text(detail_title)
            similarity = title_similarity(clicked_title, detail_title)
            if (
                normalized_clicked
                and normalized_detail
                and (
                    normalized_clicked in normalized_detail
                    or normalized_detail in normalized_clicked
                )
            ) or similarity >= TITLE_FUZZY_THRESHOLD:
                log_event(
                    logger, "detail_title_verified",
                    match="clicked_card", similarity=round(similarity, 3),
                )
                return True

        if self.title_matches_target(detail_title):
            return True

        if clicked_title:
            log_event(
                logger, "detail_title_mismatch", level=logging.WARNING,
                detail_title=detail_title, clicked_title=clicked_title,
                keyword=self._config.keyword,
                similarity=round(title_similarity(clicked_title, detail_title), 3),
            )
        return False

    def score_search_result(
        self, title_text: str, venue_text: str, city_text: str | None = None
    ) -> int:
        """给一张搜索卡打分。低于 :data:`CLICK_SCORE_THRESHOLD` 不点。

        结构是「**先否决，再打分**」。源项目把两类负向信号（城市字段冲突、
        年份冲突）做成扣分项放在末尾，但正向分一旦叠满（``title_matches_target``
        的 +100 加上 keyword 包含 +50、词元 +70、相似度 +38）能到 200 以上，
        扣 80/60 之后照样过阈值——罚则实际是失效的。而源项目自己的注释写着
        「负向信号永远优先于放松匹配」，所以这里把罚则实现成它声明的那样：
        命中冲突直接返回负分，不再参与排序。

        Returns:
            分数；``-1`` 表示标题不可用，``-100`` 表示被否决。
        """
        normalized_title = normalize_text(title_text)
        normalized_venue = normalize_text(venue_text)
        if not normalized_title:
            return -1

        keyword = self._config.keyword if isinstance(self._config.keyword, str) else None
        target_city = self._config.city if isinstance(self._config.city, str) else None

        # -- 否决 1：城市字段写明另一个已知城市 -- #
        #
        # 只拦「明确写了别的已知城市」：字段为空、写「全国」、写联演城市都不触发
        # （find_conflicting_city 只认已知城市名，目标城市出现在文案里时返回 None）。
        city_matches = False
        if target_city and isinstance(city_text, str) and city_text:
            normalized_city_text = normalize_text(city_text)
            normalized_target_city = normalize_text(city_keyword(target_city))
            if normalized_target_city and normalized_target_city in normalized_city_text:
                city_matches = True
            else:
                conflict_city = find_conflicting_city(city_text, target_city)
                if conflict_city:
                    log_event(
                        logger, "search_result_city_conflict", level=logging.WARNING,
                        title=title_text, city_text=city_text,
                        target_city=target_city, conflict_city=conflict_city,
                    )
                    return -100

        # -- 否决 2：keyword 与标题都写明年份且无交集 -- #
        #
        # 只在两边**都**有四位年份时才判：标题没写年份不构成冲突证据。
        if keyword:
            keyword_years = set(_YEAR_PATTERN.findall(keyword))
            title_years = set(_YEAR_PATTERN.findall(title_text))
            if keyword_years and title_years and not (keyword_years & title_years):
                log_event(
                    logger, "search_result_year_conflict", level=logging.WARNING,
                    title=title_text, keyword=keyword,
                    keyword_years=sorted(keyword_years),
                    title_years=sorted(title_years),
                )
                return -100

        # -- 正向打分 -- #
        score = 0
        if self.title_matches_target(title_text):
            score += 100

        normalized_keyword = normalize_text(self._config.keyword)
        if normalized_keyword:
            if normalized_keyword == normalized_title:
                score += 80
            elif normalized_keyword in normalized_title:
                score += 50

        tokens = self.keyword_tokens()
        if tokens:
            token_hits = sum(1 for token in tokens if token in normalized_title)
            score += token_hits * 20
            if token_hits == len(tokens) and len(tokens) >= 2:
                score += 30

        normalized_city = normalize_text(city_keyword(self._config.city))
        if normalized_city and normalized_city in normalized_title:
            score += 20

        if self._config.target_venue:
            expected_venue = normalize_text(self._config.target_venue)
            if expected_venue and expected_venue in normalized_venue:
                score += 30

        # 相似度补分：「巡演」这类写法不含连写的「演唱会」，词元分不够过阈值
        if keyword:
            similarity = title_similarity(keyword, title_text)
            if similarity >= SIMILARITY_BONUS_MIN:
                score += int(50 * similarity)

        # 城市字段对得上：+10 而非 +20 —— +20 会把「同城无关演出」精确推到
        # 点击阈值 60 上，等于让所有同城演出都够格被点。
        if city_matches:
            score += 10

        return score

    # ------------------------------------------------------------------ #
    # 首页 → 搜索页 → 结果列表
    # ------------------------------------------------------------------ #
    def open_search_from_homepage(self) -> bool:
        """从首页进入搜索页。"""
        selectors: list[tuple[str, str]] = [
            (By.ID, resource_id) for resource_id in HOME_READY_RESOURCE_IDS
        ]
        selectors.append((ANDROID_UIAUTOMATOR, 'new UiSelector().text("搜索")'))

        for by, value in selectors:
            if not self._host.ultra_fast_click(by, value, timeout=0.8):
                continue
            probe = self._host.wait_for_page_state(
                {"search_page"}, timeout=2.5, poll_interval=0.15
            )
            if probe["state"] == "search_page":
                return True

        if self._host.probe_current_page()["state"] == "search_page":
            return True

        logger.warning("未能从首页打开搜索页")
        return False

    def submit_search_keyword(self, keyword: str | None = None) -> bool:
        """在搜索页填入关键词并提交，等到结果列表出现。"""
        keyword = keyword if keyword is not None else self._config.keyword
        if not keyword:
            logger.warning("缺少 keyword，无法自动搜索")
            return False

        with self._host.timed_step("搜索页输入并提交关键词", manual_baseline_seconds=6.0):
            search_input = None
            for resource_id in SEARCH_INPUT_RESOURCE_IDS:
                try:
                    search_input = self._host._wait_for_element(By.ID, resource_id, timeout=1.5)
                    break
                except Exception:  # noqa: BLE001
                    continue
            if search_input is None:
                logger.warning("未找到搜索输入框")
                return False

            self._host._click_element_center(search_input)
            time.sleep(0.12)

            current = self._host._read_element_text(search_input).strip()
            if current and current != keyword:
                if self._host._has_element(By.ID, SEARCH_INPUT_CLEAR_ID):
                    self._host.ultra_fast_click(By.ID, SEARCH_INPUT_CLEAR_ID, timeout=0.4)
                    time.sleep(0.05)
                else:
                    with contextlib.suppress(Exception):
                        search_input.clear()

            if self._host._read_element_text(search_input).strip() != keyword:
                try:
                    search_input.set_text(keyword)
                except Exception:  # noqa: BLE001
                    return False

            if not self._host._press_keycode_safe(66, context="提交搜索关键词"):
                return False

            # 有时会落到「综合」页签，需要切到「演出」
            if self._host._has_element(ANDROID_UIAUTOMATOR, 'new UiSelector().text("演出")'):
                self._host.smart_wait_and_click(
                    ANDROID_UIAUTOMATOR, 'new UiSelector().text("演出")', timeout=0.8
                )
                time.sleep(0.1)

            deadline = time.time() + 3.5
            while time.time() < deadline:
                if self._host._find_all(By.ID, SEARCH_RESULT_RESOURCE_ID):
                    return True
                time.sleep(0.04)
            logger.warning("搜索结果加载超时")
            return False

    def scroll_search_results(self) -> None:
        self._host.d.swipe(540, 1770, 540, 520, duration=0.3)

    def collect_search_results(
        self, max_scrolls: int = 0, max_results: int = 5
    ) -> list[dict[str, Any]]:
        """收集搜索结果摘要，不点进去。"""
        seen: set[str] = set()
        collected: list[dict[str, Any]] = []

        for scroll_index in range(max_scrolls + 1):
            for card in self._host._find_all(By.ID, SEARCH_RESULT_RESOURCE_ID):
                title = self._host._safe_element_text(card, By.ID, SEARCH_RESULT_TITLE_ID)
                if not title:
                    continue
                normalized = normalize_text(title)
                if normalized in seen:
                    continue
                venue = self._host._safe_element_text(card, By.ID, SEARCH_RESULT_VENUE_ID)
                city = (
                    self._host._safe_element_text(card, By.ID, SEARCH_RESULT_CITY_ID)
                    .replace("|", "")
                    .strip()
                )
                collected.append(
                    {
                        "title": title,
                        "venue": venue,
                        "city": city,
                        "time": self._host._safe_element_text(
                            card, By.ID, SEARCH_RESULT_TIME_ID
                        ),
                        "score": self.score_search_result(title, venue, city),
                    }
                )
                seen.add(normalized)

            if len(collected) >= max_results:
                break
            if scroll_index < max_scrolls:
                self.scroll_search_results()
                time.sleep(0.4)

        collected.sort(key=lambda item: item["score"], reverse=True)
        return collected[:max_results]

    def open_target_from_search_results(
        self, max_scrolls: int = 2, max_results: int = 5, return_details: bool = False
    ) -> Any:
        """在结果列表里挑最高分卡片点开，进去后校验标题。

        校验失败会把该卡加入黑名单再退回来重试 —— 没有黑名单会反复点同一张
        失败卡，形成死循环（源项目 issue #51 的根因）。
        """
        seen_titles: set[str] = set()
        collected: list[dict[str, Any]] = []
        rejected: set[str] = set()

        with self._host.timed_step("搜索结果扫描并打开目标", manual_baseline_seconds=12.0):
            for scroll_index in range(max_scrolls + 1):
                best_card = None
                best_score = -1
                best_title = ""

                for card in self._host._find_all(By.ID, SEARCH_RESULT_RESOURCE_ID):
                    title = self._host._safe_element_text(
                        card, By.ID, SEARCH_RESULT_TITLE_ID
                    )
                    if not title:
                        continue
                    venue = self._host._safe_element_text(
                        card, By.ID, SEARCH_RESULT_VENUE_ID
                    )
                    city = (
                        self._host._safe_element_text(card, By.ID, SEARCH_RESULT_CITY_ID)
                        .replace("|", "")
                        .strip()
                    )
                    score = self.score_search_result(title, venue, city)

                    normalized = normalize_text(title)
                    if normalized and normalized not in seen_titles:
                        collected.append(
                            {
                                "title": title,
                                "venue": venue,
                                "city": city,
                                "time": self._host._safe_element_text(
                                    card, By.ID, SEARCH_RESULT_TIME_ID
                                ),
                                "score": score,
                            }
                        )
                        seen_titles.add(normalized)

                    if normalized in rejected:
                        log_event(logger, "search_result_rejected", title=title)
                        continue

                    if score > best_score:
                        best_score = score
                        best_card = card
                        best_title = title

                if best_card is not None and best_score >= CLICK_SCORE_THRESHOLD:
                    self._host._click_element_center(best_card)
                    probe = self._host.wait_for_page_state(
                        _TARGET_STATES, timeout=5.5
                    )
                    if probe["state"] in _TARGET_STATES and self.current_page_matches_target(
                        probe, clicked_title=best_title
                    ):
                        collected.sort(key=lambda item: item["score"], reverse=True)
                        details = {
                            "opened": True,
                            "search_results": collected[:max_results],
                        }
                        return details if return_details else True

                    logger.warning("已进入详情页但标题不符，退回结果列表继续尝试")
                    rejected.add(normalize_text(best_title))
                    if not self._host._press_keycode_safe(4, context="返回搜索列表"):
                        break
                    time.sleep(0.25)
                    self._host.dismiss_startup_popups()
                else:
                    logger.info(
                        "本屏没有明确匹配项，已扫描 %d 条", len(seen_titles)
                    )

                if scroll_index < max_scrolls:
                    self.scroll_search_results()
                    time.sleep(0.2)

        logger.warning("搜索后未找到目标演出")
        collected.sort(key=lambda item: item["score"], reverse=True)
        details = {"opened": False, "search_results": collected[:max_results]}
        return details if return_details else False

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def navigate_to_target_event(self, initial_probe: dict[str, Any] | None = None) -> bool:
        """从任意页面导航到目标演出详情页。"""
        from damai.recovery import exit_non_target_event_context, recover_to_navigation_start

        if not self._config.auto_navigate:
            logger.warning("auto_navigate 未启用")
            return False

        page_probe = initial_probe or self._host.probe_current_page()
        page_probe = recover_to_navigation_start(self._host, page_probe)

        if page_probe["state"] in _TARGET_STATES:
            if self.current_page_matches_target(page_probe):
                return True
            page_probe = exit_non_target_event_context(self._host, page_probe)
            if page_probe["state"] in _TARGET_STATES and self.current_page_matches_target(
                page_probe
            ):
                return True

        if page_probe["state"] == "homepage":
            logger.info("当前在首页，开始自动搜索目标演出")
            if not self.open_search_from_homepage():
                return False
            page_probe = self._host.probe_current_page()

        if page_probe["state"] != "search_page":
            logger.warning("当前页面不适合自动搜索: %s", page_probe["state"])
            return False

        if not self.submit_search_keyword():
            return False

        return bool(self.open_target_from_search_results())

    def discover_target_event(
        self,
        keyword_candidates: list[str],
        initial_probe: dict[str, Any] | None = None,
        search_scrolls: int = 1,
        result_limit: int = 5,
    ) -> dict[str, Any] | None:
        """依次尝试多个关键词，收集候选并打开最佳匹配。

        返回 ``None`` 表示全部失败（调用方依赖 falsy 判定）；失败时
        ``last_failed_candidates`` 里会留着 top-5 候选，供失败文案列出。
        """
        from damai.recovery import exit_non_target_event_context, recover_to_navigation_start

        self.last_failed_candidates = []
        page_probe = initial_probe or self._host.probe_current_page()
        page_probe = recover_to_navigation_start(self._host, page_probe)

        if page_probe["state"] in _TARGET_STATES:
            if self.current_page_matches_target(page_probe):
                return {
                    "used_keyword": self._config.keyword,
                    "search_results": [],
                    "page_probe": page_probe,
                }
            page_probe = exit_non_target_event_context(self._host, page_probe)
            if page_probe["state"] in _TARGET_STATES and self.current_page_matches_target(
                page_probe
            ):
                return {
                    "used_keyword": self._config.keyword,
                    "search_results": [],
                    "page_probe": page_probe,
                }

        if page_probe["state"] == "homepage":
            if not self.open_search_from_homepage():
                return None
            page_probe = self._host.probe_current_page()

        if page_probe["state"] != "search_page":
            logger.warning("当前页面不适合检索: %s", page_probe["state"])
            return None

        tried: set[str] = set()
        failed: list[dict[str, Any]] = []

        for keyword in keyword_candidates:
            normalized_keyword = normalize_text(keyword)
            if not normalized_keyword or normalized_keyword in tried:
                continue

            if tried:
                # 上一轮失败后可能停在详情页/首页，先退回搜索页再试下一个词
                self._host.dismiss_startup_popups()
                probe = self._host.probe_current_page()
                if probe["state"] in _TARGET_STATES:
                    probe = exit_non_target_event_context(self._host, probe)
                    if probe["state"] in _TARGET_STATES and self.current_page_matches_target(
                        probe
                    ):
                        return {
                            "used_keyword": keyword,
                            "search_results": [],
                            "page_probe": probe,
                        }
                if probe["state"] == "homepage":
                    if not self.open_search_from_homepage():
                        continue
                    probe = self._host.probe_current_page()
                if probe["state"] != "search_page":
                    logger.warning("重试前页面状态异常: %s，跳过该关键词", probe["state"])
                    continue

            logger.info("尝试搜索关键词: %s", keyword)
            if not self.submit_search_keyword(keyword):
                tried.add(normalized_keyword)
                continue

            result = self.open_target_from_search_results(
                max_scrolls=search_scrolls,
                max_results=result_limit,
                return_details=True,
            )
            search_results = result["search_results"]
            if search_results:
                logger.info(
                    "搜索到 %d 条候选，最高分 %d", len(search_results), search_results[0]["score"]
                )
            if result["opened"]:
                return {
                    "used_keyword": keyword,
                    "search_results": search_results,
                    "page_probe": self._host.probe_current_page(),
                }

            for item in search_results:
                failed.append({**item, "used_keyword": keyword})
            tried.add(normalized_keyword)

        # 各轮候选合并去重（同标题取高分），留 top-5
        merged: dict[str, dict[str, Any]] = {}
        for entry in failed:
            key = normalize_text(str(entry.get("title") or ""))
            if not key:
                continue
            existing = merged.get(key)
            if existing is None or entry.get("score", 0) > existing.get("score", 0):
                merged[key] = entry
        top = sorted(merged.values(), key=lambda item: item.get("score", 0), reverse=True)[:5]
        self.last_failed_candidates = top

        log_event(
            logger, "discovery_failed", level=logging.WARNING,
            keywords_tried=len(tried),
            candidates=len(top),
            top_score=top[0].get("score") if top else None,
        )
        logger.warning("尝试多个关键词后仍未打开目标演出")
        return None
