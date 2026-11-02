"""页面级的等待与恢复辅助函数。

只做两件事，都是导航主链路容易出错的地方：

- :func:`wait_for_home_ready` —— 等首页可识别。超时**落盘 XML 再抛异常**，
  因为「首页没起来」几乎总是弹窗/白屏/换皮肤，只看日志永远查不出来。
- :func:`select_search_result` —— 等结果列表并按 0/1/N 规则确定性选择。

两个函数都只吃 ``dump_hierarchy()`` + ``click(x, y)``，不依赖任何业务对象，
因此可以离线用假 device 测。

另外提供 :func:`read_detail_title` / :func:`read_detail_venue`：详情页标题
的读取口径散落在导航和恢复两处，收在这里以免两边判据漂移。
"""

from __future__ import annotations

import contextlib
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

from core.logger import get_logger
from damai.constants import (
    SEARCH_RESULT_RESOURCE_ID,
    SEARCH_RESULT_TITLE_ID,
)

logger = get_logger("damai.page_helpers")

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

SEARCH_RESULT_TITLE_RESOURCE_ID = SEARCH_RESULT_TITLE_ID

#: 详情页标题：主标题优先，其次两段式标题拼接
DETAIL_TITLE_RESOURCE_IDS: tuple[str, ...] = ("cn.damai:id/title_tv",)
DETAIL_TITLE_PART_RESOURCE_IDS: tuple[str, ...] = (
    "cn.damai:id/project_title_tv1",
    "cn.damai:id/project_title_tv2",
)
DETAIL_VENUE_RESOURCE_IDS: tuple[str, ...] = (
    "cn.damai:id/venue_name_0",
    "cn.damai:id/tv_project_venueName",
)


# ---------------------------------------------------------------------- #
# 异常
# ---------------------------------------------------------------------- #
class HomeNotReadyError(RuntimeError):
    """等不到大麦首页。"""


class SearchEmptyError(RuntimeError):
    """搜索结果 0 条。"""


class SearchAmbiguousError(RuntimeError):
    """N>1 条结果且严格模式下无法消歧。"""


# ---------------------------------------------------------------------- #
# XML 工具
# ---------------------------------------------------------------------- #
def parse_bounds_center(bounds_str: str | None) -> tuple[int, int] | None:
    if not bounds_str:
        return None
    match = _BOUNDS_RE.match(bounds_str)
    if not match:
        return None
    x1, y1, x2, y2 = (int(v) for v in match.groups())
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def build_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in parent}


def find_clickable_ancestor(
    parent_map: dict[ET.Element, ET.Element], node: ET.Element
) -> ET.Element | None:
    cur = parent_map.get(node)
    while cur is not None:
        if cur.get("clickable") == "true":
            return cur
        cur = parent_map.get(cur)
    return None


def find_text_by_resource_id(xml_root: ET.Element, resource_id: str) -> str:
    """返回首个匹配 ``resource_id`` 的非空 text。"""
    for node in xml_root.iter("node"):
        if node.get("resource-id") != resource_id:
            continue
        text = (node.get("text") or "").strip()
        if text:
            return text
    return ""


def safe_dump_hierarchy(driver: Any) -> str:
    """``dump_hierarchy()`` 的安全包装，异常一律返回空串。"""
    try:
        return driver.dump_hierarchy() or ""
    except Exception as exc:  # noqa: BLE001 — dump 失败不该中断主流程
        logger.debug("dump_hierarchy 失败: %s", exc)
        return ""


def dump_ui(driver: Any, dump_dir: Any, scene: str) -> str | None:
    """把当前层级树存成 ``<dump_dir>/<scene>_<ts>.xml``，失败返回 ``None``。

    写入失败永不抛——调用方通常正在处理别的异常，dump 只是附加材料。
    """
    try:
        dump_path = os.fspath(dump_dir)
        os.makedirs(dump_path, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(dump_path, f"{scene}_{ts}.xml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(safe_dump_hierarchy(driver))
        return path
    except Exception as exc:  # noqa: BLE001
        logger.debug("UI dump 写入失败 (%s): %s", scene, exc)
        return None


def summarise_ui(xml_str: str, max_items: int = 10) -> dict[str, list[str]]:
    """提取可见文案与 resource-id，用于失败日志里一眼看出停在哪。"""
    texts: list[str] = []
    ids: list[str] = []
    if not xml_str:
        return {"texts": texts, "resource_ids": ids}
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return {"texts": texts, "resource_ids": ids}
    for node in root.iter("node"):
        text = (node.get("text") or "").strip()
        if text:
            texts.append(text)
        rid = (node.get("resource-id") or "").strip()
        if rid:
            ids.append(rid)
    return {"texts": texts[:max_items], "resource_ids": ids[:max_items]}


# ---------------------------------------------------------------------- #
# 详情页文案读取
# ---------------------------------------------------------------------- #
def read_detail_title(xml_root: ET.Element | None) -> str:
    """读详情页标题。主标题为空时退回两段式标题拼接。"""
    if xml_root is None:
        return ""
    for resource_id in DETAIL_TITLE_RESOURCE_IDS:
        title = find_text_by_resource_id(xml_root, resource_id)
        if title:
            return title
    parts = [
        find_text_by_resource_id(xml_root, rid) for rid in DETAIL_TITLE_PART_RESOURCE_IDS
    ]
    return "".join(part.strip() for part in parts if part).strip()


def read_detail_venue(xml_root: ET.Element | None) -> str:
    """读详情页场馆名。"""
    if xml_root is None:
        return ""
    for resource_id in DETAIL_VENUE_RESOURCE_IDS:
        venue = find_text_by_resource_id(xml_root, resource_id)
        if venue:
            return venue
    return ""


# ---------------------------------------------------------------------- #
# 首页就绪
# ---------------------------------------------------------------------- #
def wait_for_home_ready(
    driver: Any,
    probe: Any,
    *,
    timeout: float = 8.0,
    poll_interval: float = 0.2,
    dump_dir: Any = "tmp",
) -> dict[str, Any]:
    """轮询首页状态，超时落盘 XML 并抛 :class:`HomeNotReadyError`。

    Args:
        driver: u2 device（需 ``dump_hierarchy()``）。
        probe: :class:`damai.probe.PageProbe`。
        timeout: 最长等待秒数。
        poll_interval: 轮询间隔。
        dump_dir: 失败时 XML 落盘目录。

    Returns:
        ``state == "homepage"`` 的探测结果。

    Raises:
        HomeNotReadyError: 超时仍未识别到首页。
    """
    deadline = time.time() + max(0.0, float(timeout))
    last_result: dict[str, Any] | None = None
    while True:
        with contextlib.suppress(AttributeError):
            probe.invalidate()
        last_result = probe.probe(fast=True)
        if (last_result or {}).get("state") == "homepage":
            return last_result
        if time.time() >= deadline:
            break
        time.sleep(poll_interval)

    dump_path = dump_ui(driver, dump_dir, "home_probe")
    snapshot = summarise_ui(safe_dump_hierarchy(driver))
    logger.warning(
        "wait_for_home_ready 超时: timeout=%.1fs state=%s dump=%s texts=%s ids=%s",
        timeout,
        (last_result or {}).get("state"),
        dump_path,
        snapshot["texts"],
        snapshot["resource_ids"],
    )
    raise HomeNotReadyError(
        f"首页未就绪 (timeout={timeout:.1f}s, "
        f"state={(last_result or {}).get('state')}, dump={dump_path})"
    )


# ---------------------------------------------------------------------- #
# 搜索结果选择
# ---------------------------------------------------------------------- #
def enumerate_search_results_from_xml(xml_str: str) -> list[dict[str, Any]]:
    """从层级树里解析出每一张搜索卡片。"""
    if not xml_str:
        return []
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return []

    results: list[dict[str, Any]] = []
    for node in root.iter("node"):
        if node.get("resource-id") != SEARCH_RESULT_RESOURCE_ID:
            continue
        title = ""
        for desc in node.iter("node"):
            if desc.get("resource-id") == SEARCH_RESULT_TITLE_RESOURCE_ID:
                title = (desc.get("text") or "").strip()
                break
        results.append(
            {"index": len(results), "title": title, "bounds": node.get("bounds")}
        )
    return results


def _click_search_result(driver: Any, item: dict[str, Any]) -> None:
    coords = parse_bounds_center(item.get("bounds"))
    if coords is None:
        raise RuntimeError(f"无法解析搜索结果 bounds={item.get('bounds')!r}")
    driver.click(*coords)


def _fuzzy_match(target: str, title: str) -> bool:
    if not target or not title:
        return False
    left = target.strip().lower()
    right = title.strip().lower()
    if not left or not right:
        return False
    return left in right or right in left


def select_search_result(
    driver: Any,
    *,
    keyword: str = "",
    target_title: str | None = None,
    timeout: float = 5.0,
    poll_interval: float = 0.2,
    strict: bool = False,
    dump_dir: Any = "tmp",
) -> dict[str, Any]:
    """等结果列表出现，然后按 0/1/N 规则确定性地选一条。

    - **0 条** → 落盘 + 抛 :class:`SearchEmptyError`
    - **1 条** → 直接点
    - **N 条** → 优先按 ``target_title`` 模糊匹配；没匹配上时，
      ``strict=False`` 退回第一条，``strict=True`` 抛
      :class:`SearchAmbiguousError`

    「N 条时退回第一条」是有意的：抢票场景宁可点错再退回来，也不能因为
    犹豫而错过开售。需要严格时传 ``strict=True``。
    """
    deadline = time.time() + max(0.0, float(timeout))
    results: list[dict[str, Any]] = []
    while True:
        results = enumerate_search_results_from_xml(safe_dump_hierarchy(driver))
        if results:
            break
        if time.time() >= deadline:
            break
        time.sleep(poll_interval)

    if not results:
        dump_path = dump_ui(driver, dump_dir, "search_probe")
        logger.warning(
            "search_result 0 条 keyword=%r timeout=%.1fs dump=%s",
            keyword,
            timeout,
            dump_path,
        )
        raise SearchEmptyError(
            f"搜索结果为空 (keyword={keyword!r}, timeout={timeout:.1f}s, dump={dump_path})"
        )

    if len(results) == 1:
        chosen = results[0]
        logger.info("select_search_result: 唯一结果 idx=0 title=%r", chosen.get("title"))
        _click_search_result(driver, chosen)
        return chosen

    if target_title:
        for item in results:
            if _fuzzy_match(target_title, item.get("title") or ""):
                logger.info(
                    "select_search_result: 模糊匹配 idx=%d title=%r target=%r",
                    item["index"],
                    item.get("title"),
                    target_title,
                )
                _click_search_result(driver, item)
                return item

    if strict:
        dump_path = dump_ui(driver, dump_dir, "search_probe")
        titles = [r.get("title") for r in results[:5]]
        logger.warning(
            "search_result 歧义 keyword=%r target_title=%r titles=%s dump=%s",
            keyword,
            target_title,
            titles,
            dump_path,
        )
        raise SearchAmbiguousError(
            f"搜索结果歧义 (共 {len(results)} 条, target_title={target_title!r}, "
            f"dump={dump_path})"
        )

    chosen = results[0]
    logger.warning(
        "select_search_result: %d 条结果未匹配 target_title=%r，回退到 idx=0 title=%r",
        len(results),
        target_title,
        chosen.get("title"),
    )
    _click_search_result(driver, chosen)
    return chosen
