"""大麦演出详情页：目标校验、场次选择、开售等待、点进购票。

三个容易踩的点，都在这里处理：

1. **点错演出**。搜索结果看着对、点进去是另一场（同名跨城巡演很常见），
   所以进详情页第一件事是校验标题/场馆，不符立刻返回，绝不继续往下点票档。
2. **点错场次**。多场次面板上选日期必须**唯一命中**，同日多条直接报错 ——
   选错场次买错票比买不到更糟。
3. **点错入口**。「预约抢票」和「立即购票」同一位置，靠
   :class:`pages.components.damai_buy_bar.DamaiBuyBar` 的白名单挡住。

开售等待也在这里（:meth:`DamaiDetailPage.wait_until_buyable`），因为它守的
就是本页的购买栏。校时逻辑在 ``core.clock``。
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import timedelta

from core.android import ui_tree
from core.clock import WAIT_WINDOW_AFTER_SALE_S, parse_sale_time, true_now
from core.logger import get_logger, log_event
from core.text import normalize_date, title_similarity
from data.constant.damai import (
    BUY_BUTTON_RESOURCE_IDS,
    CTA_BLOCKED_KEYWORDS,
    CTA_READY_KEYWORDS,
    DETAIL_TITLE_IDS,
    DETAIL_TITLE_PART_IDS,
    DETAIL_VENUE_IDS,
    PRICE_CONTAINER_IDS,
    SALE_READY_TEXTS,
    SESSION_DATE_RESOURCE_ID,
    SESSION_PANEL_RESOURCE_ID,
    TITLE_FUZZY_THRESHOLD,
)
from data.entity.damai import SessionCard
from pages.base_damai import DamaiBasePage
from pages.components.damai_buy_bar import DamaiBuyBar, is_safe_to_click

logger = get_logger(__name__)

#: Canvas 快路径的短睡间隔。开售前不做任何文案查询，只睡到点。
_CANVAS_POLL_SLEEP_S = 0.02
#: 常规轮询间隔
_POLL_SLEEP_S = 0.05

#: 开售后可能出现的可点文案（含已进入 SKU/确认页的情形）
_READY_TEXTS: tuple[str, ...] = (
    *SALE_READY_TEXTS,
    "立即购买",
    "选座购买",
    "立即提交",
    "提交订单",
)


class WrongEventError(RuntimeError):
    """详情页不是目标演出。"""


class SessionNotFoundError(RuntimeError):
    """无法唯一确定目标场次。"""


def enumerate_sessions(root: ET.Element | None) -> list[SessionCard]:
    """从 UI 树里解析多场次面板的日期卡片（纯函数）。"""
    sessions: list[SessionCard] = []
    panels = ui_tree.find_by_resource_id(root, SESSION_PANEL_RESOURCE_ID)
    if not panels:
        return sessions

    parent_map = ui_tree.build_parent_map(root)
    for panel in panels:
        for date_node in ui_tree.find_by_resource_id(panel, SESSION_DATE_RESOURCE_ID):
            # 日期节点自身通常不可点（clickable=false），真正接点击的是祖先容器。
            # 这里必须显式判 None：ElementTree 的空元素布尔值为 False，
            # 用 ``or date_node`` 会在「祖先链上没有可点节点」时静默取错 bounds。
            clickable = ui_tree.clickable_ancestor(parent_map, date_node)
            if clickable is None:
                clickable = date_node
            date_text = ui_tree.node_text(date_node)
            sessions.append(
                SessionCard(
                    index=len(sessions),
                    date_text=date_text,
                    date_key=normalize_date(date_text) or "",
                    coords=ui_tree.node_center(clickable),
                    sold_out=any(
                        tag in "".join(ui_tree.descendant_texts(clickable))
                        for tag in ("无票", "缺货", "售罄")
                    ),
                )
            )
    return sessions


def pick_session(sessions: list[SessionCard], target_date: str | None) -> SessionCard:
    """按日期选场次，要求唯一命中。

    Raises:
        SessionNotFoundError: 没有可选场次 / 无命中 / 命中多条。
    """
    available = [item for item in sessions if item.coords is not None]
    if not available:
        raise SessionNotFoundError("场次面板里没有可点的日期卡片")

    if not target_date:
        raise SessionNotFoundError(f"未配置场次日期（共 {len(available)} 个候选，拒绝盲选）")

    wanted = normalize_date(target_date) or target_date.strip()
    matches = [item for item in available if item.date_key == wanted]
    if not matches:
        raise SessionNotFoundError(
            f"没有日期为 {wanted} 的场次，候选: {[item.date_text for item in available]}"
        )
    if len(matches) > 1:
        raise SessionNotFoundError(
            f"日期 {wanted} 命中 {len(matches)} 条场次，无法唯一确定"
            f"（{len(available)} 个候选中）"
        )
    chosen = matches[0]
    if chosen.sold_out:
        raise SessionNotFoundError(f"日期 {chosen.date_text} 的场次显示无票")
    return chosen


class DamaiDetailPage(DamaiBasePage):
    """演出详情页。"""

    name = "damai_detail"
    ready_selectors: tuple[str, ...] = tuple(f"id={rid}" for rid in BUY_BUTTON_RESOURCE_IDS)

    # -------------------------------------------------------------- #
    # 目标校验
    # -------------------------------------------------------------- #
    def title(self) -> str:
        """详情页标题。两段式标题会拼起来，否则只能拿到半截。"""
        root = self.xml_root()
        for resource_id in DETAIL_TITLE_IDS:
            for node in ui_tree.find_by_resource_id(root, resource_id):
                text = ui_tree.node_text(node)
                if text:
                    return text
        parts = [
            ui_tree.node_text(node)
            for resource_id in DETAIL_TITLE_PART_IDS
            for node in ui_tree.find_by_resource_id(root, resource_id)
        ]
        return "".join(part for part in parts if part)

    def venue(self) -> str:
        root = self.xml_root()
        for resource_id in DETAIL_VENUE_IDS:
            for node in ui_tree.find_by_resource_id(root, resource_id):
                text = ui_tree.node_text(node)
                if text:
                    return text
        return ""

    def verify_target(self) -> str:
        """校验当前详情页确实是目标演出，返回实际标题。

        只校验配置里写了的那几项：``target_title`` 走模糊相似度，``target_venue``
        走包含匹配。任一项不符就抛 :class:`WrongEventError` —— 宁可这一轮报废，
        也不能买错票。

        Raises:
            WrongEventError: 标题或场馆与配置不符。
        """
        actual_title = self.title()
        actual_venue = self.venue()
        config = self.config

        expected_title = str(getattr(config, "target_title", "") or "")
        if expected_title:
            similarity = title_similarity(expected_title, actual_title)
            if similarity < TITLE_FUZZY_THRESHOLD:
                raise WrongEventError(
                    f"详情页标题不匹配（相似度 {similarity:.2f} < {TITLE_FUZZY_THRESHOLD}）："
                    f"期望 {expected_title!r}，实际 {actual_title!r}"
                )

        expected_venue = str(getattr(config, "target_venue", "") or "")
        if expected_venue and expected_venue not in actual_venue:
            raise WrongEventError(
                f"详情页场馆不匹配：期望含 {expected_venue!r}，实际 {actual_venue!r}"
            )

        log_event(
            logger, "detail_target_verified",
            title=actual_title, venue=actual_venue,
        )
        return actual_title

    # -------------------------------------------------------------- #
    # 场次
    # -------------------------------------------------------------- #
    def sessions(self) -> list[SessionCard]:
        return enumerate_sessions(self.xml_root(ttl=0.3))

    def select_session(self) -> SessionCard:
        """按配置日期选场次并点击，返回选中的卡片。"""
        sessions = self.sessions()
        if not sessions:
            # 没有多场次面板说明只有一个场次，直接放行
            logger.info("未发现多场次面板，按单场次处理")
            return SessionCard(index=-1, date_text="", date_key="")

        target_date = str(getattr(self.config, "date", "") or "")
        chosen = pick_session(sessions, target_date)
        logger.info("选中场次 %r (index=%d)", chosen.date_text, chosen.index)
        self.dev.tap_xy(*chosen.coords)  # type: ignore[misc]
        self.dev.invalidate_ui()
        self.dev.wait_short("选场次后等面板收起")
        return chosen

    # -------------------------------------------------------------- #
    # 购买栏
    # -------------------------------------------------------------- #
    def buy_bar(self) -> DamaiBuyBar:
        return DamaiBuyBar(self.dev, self.config)

    def is_sale_ready(self) -> bool:
        """当前是否可以买。

        三层判据，从最便宜的开始：可见 CTA 文案 → 票档容器 → 购买栏文案。
        """
        for text in _READY_TEXTS:
            if self.dev.exists(f"text~={text}", timeout=0.35):
                return True
        if any(self.dev.exists(f"id={rid}", timeout=0.35) for rid in PRICE_CONTAINER_IDS):
            return not self.is_reservation_mode()
        if self.dev.exists(f"id={BUY_BUTTON_RESOURCE_IDS[1]}", timeout=0.35):
            return self._purchase_bar_text_ready()
        return False

    def _purchase_bar_text_ready(self) -> bool:
        """读购买栏整棵子树的文案判断是否已开售。

        先看阻断词再看可点词，顺序不能反：「预约抢票」里也含「抢票」，
        先看可点词会把预约入口当成已开售。
        """
        root = self.xml_root()
        texts: list[str] = []
        for resource_id in BUY_BUTTON_RESOURCE_IDS:
            for node in ui_tree.find_by_resource_id(root, resource_id):
                texts.extend(ui_tree.descendant_texts(node))
        merged = "".join(texts)
        if not merged:
            return False
        if any(keyword in merged for keyword in CTA_BLOCKED_KEYWORDS):
            return False
        return any(keyword in merged for keyword in CTA_READY_KEYWORDS)

    # -------------------------------------------------------------- #
    # 开售等待
    # -------------------------------------------------------------- #
    def wait_until_buyable(self) -> str:
        """守在详情页直到可以点购票，返回触发原因。

        返回值（用于日志与测试断言）：
        ``no_schedule`` / ``already_past`` / ``guard_text`` /
        ``canvas_coords`` / ``poll_loop`` / ``timeout``

        三条关键设计：
        - 校时后才比较开售时间（本机实测比设备快 833ms，不校就抢早或抢晚）
        - Canvas 自绘 CTA 开售前**只短睡**，不做文案查询 —— 单次遍历子树 ~1s，
          开抢那一下会被这轮查询整体拖后
        - 等过 ``sell_time + 8s`` 就放弃，交给上层重试，不在这里死等
        """
        config = self.config
        sell_time = parse_sale_time(str(getattr(config, "sell_start_time", "") or ""))
        if sell_time is None:
            return "no_schedule"

        clock_offset_ms = int(getattr(config, "clock_offset_ms", 0) or 0)
        if true_now(clock_offset_ms) >= sell_time:
            logger.info("开售时间已过，跳过等待")
            return "already_past"

        lead_ms = int(getattr(config, "countdown_lead_ms", 0) or 0)
        poll_start = sell_time - _ms(lead_ms)
        sleep_seconds = (poll_start - true_now(clock_offset_ms)).total_seconds()
        if sleep_seconds > 0:
            logger.info(
                "等待开售：%s 前 %dms 开始轮询（本机校时偏移 %dms）",
                sell_time.isoformat(timespec="seconds"), lead_ms, clock_offset_ms,
            )
            time.sleep(sleep_seconds)

        bar = self.buy_bar()
        initial = bar.read()
        canvas_cta = initial is not None and not initial.readable
        if canvas_cta:
            logger.info("预判 CTA 为 Canvas 自绘：开售前只做短睡，不做文案查询")

        deadline = sell_time + _ms(WAIT_WINDOW_AFTER_SALE_S * 1000)
        started = time.monotonic()
        polls = 0
        saw_cta_text = False

        while True:
            now = true_now(clock_offset_ms)
            if now >= deadline:
                break
            polls += 1

            # (0) Canvas 快路径：开售前一次 u2 查询都不做
            if canvas_cta and not saw_cta_text:
                if now < sell_time:
                    time.sleep(_CANVAS_POLL_SLEEP_S)
                    continue
                state = bar.read()
                if state is not None:
                    log_event(
                        logger, "cta_canvas_fallback",
                        resource_id=state.resource_id, coords=state.center,
                        waited_ms=int((time.monotonic() - started) * 1000), polls=polls,
                    )
                    return "canvas_coords"
                canvas_cta = False  # 坐标意外丢失，回落通用轮询

            # (1) 文案路径：读到安全文案立即返回（开售前文案翻转即可提前返回）
            state = bar.read()
            text = state.text if state else ""
            if text:
                saw_cta_text = True
                if is_safe_to_click(text):
                    log_event(
                        logger, "sale_ready", source="buy_bar",
                        cta_text=text, polls=polls,
                        duration_ms=int((time.monotonic() - started) * 1000),
                    )
                    return "guard_text"

            # (2) Canvas 兜底：到点 + 全程无文案 + 坐标可用
            #     必须排在 is_sale_ready 之前，否则被它每轮 ~1s 的查询拖后。
            if now >= sell_time and not saw_cta_text:
                center = bar.cta_center()
                if center is not None:
                    log_event(
                        logger, "cta_canvas_fallback", resource_id=None,
                        coords=center, waited_ms=int((time.monotonic() - started) * 1000),
                        polls=polls,
                    )
                    return "canvas_coords"

            # (3) 页面结构兜底
            if self.is_sale_ready():
                log_event(
                    logger, "sale_ready", source="poll_loop", cta_text="?",
                    polls=polls, duration_ms=int((time.monotonic() - started) * 1000),
                )
                return "poll_loop"

            time.sleep(_POLL_SLEEP_S)

        log_event(
            logger, "sale_wait_timeout", level=logging.WARNING,
            polls=polls, duration_ms=int((time.monotonic() - started) * 1000),
        )
        return "timeout"


def _ms(milliseconds: float) -> timedelta:
    """毫秒 → ``timedelta``，省得每处都写全 ``timedelta(milliseconds=...)``。"""
    return timedelta(milliseconds=milliseconds)
