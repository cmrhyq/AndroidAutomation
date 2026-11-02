"""观演人勾选：确保确认页上选够了该选的人。

这是抢票链路上最容易「看起来成功实则失败」的一步——没勾观演人就提交，
大麦会停在确认页，而提交动作本身是「成功」的。所以这里的每个判断都必须
能落到可验证的状态上，不能靠「点了就当选中」。

三个坑，都是真机踩出来的：

1. ``checked`` 属性不可靠。元素层读到全 ``false`` 时，用 dump 出来的 XML
   再数一遍 ``checked="true"``——属性有抖动，层级树是同一个快照，反而更准。
2. 按姓名定位要用 XPath 上溯祖先，因为姓名 TextView 和 checkbox 不是兄弟，
   中间隔着几层自定义 View。
3. 极速路径下按坐标点，必须**先缓存坐标**。否则每轮都要 ``find_elements``
   拿 bounds，单次 100ms+ 就把开售窗口耗光了。
"""

from __future__ import annotations

import re
import time
from typing import Any

from selenium.webdriver.common.by import By

from core.logger import get_logger
from damai.constants import ATTENDEE_CHECKBOX_ID, ATTENDEE_NAME_ID
from damai.primitives import ANDROID_UIAUTOMATOR
from damai.text_utils import normalize_text

logger = get_logger("damai.attendee")

CHECKBOX_ID = ATTENDEE_CHECKBOX_ID

#: 「仅需选择 N 位」提示文案 —— 有的场次限购 1 位，写了名字也不代表能多选
_REQUIRED_HINT_RE = re.compile(r"仅需选择\s*(\d+)\s*位")

#: XML 兜底统计用：抓 checkbox 节点的 checked 属性
_CHECKED_ATTR_RE = re.compile(
    r'resource-id="cn\.damai:id/checkbox"[^>]*checked="(true|false)"'
)


class AttendeeSelector:
    """确认页观演人勾选器。"""

    def __init__(self, host: Any) -> None:
        self._host = host

    @property
    def _config(self) -> Any:
        return self._host.config

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def checkbox_elements(self) -> list[Any]:
        try:
            return list(self._host._find_all(By.ID, CHECKBOX_ID))
        except Exception as exc:  # noqa: BLE001
            logger.debug("查找观演人勾选框失败: %s", exc)
            return []

    def required_count(self) -> int:
        """需要勾选几位。优先信页面提示，其次按配置里的观演人数。"""
        hint = ""
        try:
            hint = self._host._safe_element_text(
                self._host.d,
                ANDROID_UIAUTOMATOR,
                'new UiSelector().textContains("仅需选择")',
            )
        except Exception:  # noqa: BLE001
            hint = ""
        match = _REQUIRED_HINT_RE.search(hint or "")
        if match:
            return max(1, int(match.group(1)))
        return max(1, len(self._config.users or []))

    def selected_count(
        self,
        elements: list[Any] | None = None,
        *,
        xml_fallback: bool = True,
    ) -> int:
        """已勾选数量。

        Args:
            elements: 已取到的勾选框；不传则现查。
            xml_fallback: 元素层读到 0 时是否用 dump 出来的 XML 复核。
                极速模式关掉它——多一次 dump 就是几百毫秒。
        """
        if elements is None:
            elements = self.checkbox_elements()

        count = sum(1 for box in elements if self._host._is_checked(box))
        if count > 0 or not xml_fallback:
            return count

        try:
            source = self._host.d.dump_hierarchy() or ""
        except Exception:  # noqa: BLE001
            return count
        if not isinstance(source, str):
            return count

        states = _CHECKED_ATTR_RE.findall(source)
        if not states:
            return count
        return sum(1 for state in states if state == "true")

    # ------------------------------------------------------------------ #
    # 点击
    # ------------------------------------------------------------------ #
    def click_checkbox(self, checkbox: Any, *, fast: bool = False) -> bool:
        """点一个勾选框并验证真的选中了。

        依次尝试「坐标点 / 元素点 / 连点」，每次点完都复核——
        只信「选中数变多了」这个事实，不信点击返回值。
        """
        if fast:
            for action in (
                lambda: checkbox.click(),
                lambda: self._host._click_element_center(checkbox, duration=28),
            ):
                try:
                    action()
                    time.sleep(0.01)
                    return True
                except Exception:  # noqa: BLE001
                    continue
            return False

        before = self.selected_count()
        for action in (
            lambda: self._host._click_element_center(checkbox, duration=35),
            lambda: checkbox.click(),
            lambda: self._host._burst_click_element_center(
                checkbox, count=2, interval_ms=30, duration=30
            ),
        ):
            try:
                action()
            except Exception:  # noqa: BLE001
                continue
            time.sleep(0.05)
            if self._host._is_checked(checkbox):
                return True
            if self.selected_count() > before:
                return True
        return False

    def _checkbox_xpaths(self, name: str) -> list[str]:
        return [
            (
                f'//*[@resource-id="{ATTENDEE_NAME_ID}" and normalize-space(@text)="{name}"]'
                '/ancestor::*[.//*[@resource-id="cn.damai:id/checkbox"]][1]'
                '//*[@resource-id="cn.damai:id/checkbox"]'
            ),
            (
                f'//*[@resource-id="{ATTENDEE_NAME_ID}" and contains(normalize-space(@text), "{name}")]'
                '/ancestor::*[.//*[@resource-id="cn.damai:id/checkbox"]][1]'
                '//*[@resource-id="cn.damai:id/checkbox"]'
            ),
        ]

    def select_by_name(self, name: str) -> bool:
        """按姓名勾选。精确匹配优先，再退回包含匹配。"""
        if not name:
            return False
        for xpath in self._checkbox_xpaths(name):
            try:
                boxes = self._host._find_all(By.XPATH, xpath)
            except Exception:  # noqa: BLE001
                boxes = []
            for box in boxes:
                if self._host._is_checked(box):
                    return True
                if self.click_checkbox(box):
                    return True
        return False

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    def ensure_selected(self, *, require_section: bool = False) -> bool:
        """确保确认页选够观演人。返回 ``False`` 表示选不够、不该继续提交。

        Args:
            require_section: 为 ``True`` 时，页面上没有观演人区域算失败。
                默认 ``False``——很多场次免选观演人，把「没这块」当失败会
                把正常流程拦住。
        """
        required = max(1, len(self._config.users or []))
        rush = bool(self._config.rush_mode)
        submit_mode = bool(self._config.if_commit_order)

        if rush and not submit_mode:
            return self._ensure_fast(required, require_section=require_section)
        return self._ensure_thorough(required, require_section=require_section)

    def _ensure_fast(self, required: int, *, require_section: bool) -> bool:
        """极速路径：认缓存坐标，缓存缺失才现查并缓存。"""
        cached = self._host._cached_hot_path_coords.get("attendee_checkboxes")
        if cached:
            logger.info("极速补选观演人：按缓存坐标点 %d 个", min(len(cached), required))
            for coords in cached[:required]:
                self._host._click_coordinates(*coords)
            return True

        boxes: list[Any] = []
        deadline = time.time() + 3.0
        while time.time() < deadline:
            boxes = self.checkbox_elements()
            if boxes:
                break
            time.sleep(0.05)
        if not boxes:
            return not require_section

        coords: list[tuple[int, int]] = []
        for box in boxes:
            center = self._bounds_center_of(box)
            if center is not None:
                coords.append(center)
        if coords:
            self._host._cached_hot_path_coords["attendee_checkboxes"] = coords

        selected = self.selected_count(boxes, xml_fallback=False)
        if selected >= required:
            return True

        logger.info("观演人未选够（%d/%d），按顺序补选", selected, required)
        clicked = sum(
            1
            for box in boxes[:required]
            if self.click_checkbox(box, fast=True)
        )
        if clicked < required:
            logger.warning("观演人补选不足（需要 %d 位，点到 %d 位）", required, clicked)
            return False
        return True

    def _ensure_thorough(self, required: int, *, require_section: bool) -> bool:
        """稳妥路径：按姓名勾，勾不上再按顺序兜底。"""
        boxes = self.checkbox_elements()

        if not self._config.rush_mode:
            has_section = self._host._has_element(
                ANDROID_UIAUTOMATOR, 'new UiSelector().textContains("实名观演人")'
            )
            if not has_section:
                return not require_section
            if not boxes:
                logger.warning("确认页有观演人区域但找不到可勾选框，请手动检查")
                return False
            required = self.required_count()
        elif not boxes:
            return not require_section

        selected = self.selected_count(boxes, xml_fallback=not self._config.rush_mode)
        if selected >= required:
            return True

        logger.info("观演人未选够（%d/%d），自动补选", selected, required)

        unmatched: list[str] = []
        for name in self._config.users or []:
            if selected >= required:
                break
            if self.select_by_name(normalize_text(name) or name):
                selected = self.selected_count()
            else:
                unmatched.append(name)
        if unmatched and selected < required:
            logger.warning("未能按姓名定位观演人: %s，改用顺序兜底", "、".join(unmatched))

        if selected < required:
            for box in self.checkbox_elements():
                if selected >= required:
                    break
                if self._host._is_checked(box):
                    continue
                if not self.click_checkbox(box):
                    continue
                selected = self.selected_count()

        if selected < required:
            logger.warning("观演人选择不足（需要 %d 位，当前 %d 位）", required, selected)
            return False
        return True

    def _bounds_center_of(self, element: Any) -> tuple[int, int] | None:
        """从 u2 元素拿中心点。

        必须走 ``_element_rect``：u2 3.x 里 ``UiObject.bounds`` 是**方法**
        而非属性，直接 ``getattr(el, "bounds")`` 拿到的是绑定方法，判断
        tuple 会永远失败。
        """
        try:
            rect = self._host._element_rect(element)
            return (
                int(rect["x"]) + int(rect["width"]) // 2,
                int(rect["y"]) + int(rect["height"]) // 2,
            )
        except Exception:  # noqa: BLE001
            return None
