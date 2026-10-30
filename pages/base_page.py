"""页面对象基类：多候选选择器轮询、显式等待、滚动查找、异常留证。

设计要点
--------
1. **多候选选择器**：淘宝 UI 改版频繁，同一个元素往往有多个特征可用。
   页面对象统一用「候选元组 + 轮询」的方式定位，任一命中即算成功，
   比写死单一 xpath 稳定得多。
2. **不做隐式等待**：与 ``AndroidDevice`` 保持一致，全部走显式轮询，
   避免等待时间被放大（项目约定 implicit wait 恒为 0）。
3. **探测不阻塞**：``probe`` 用一次 ``find_elements`` 瞬时判断，
   轮询由 ``wait_any`` 控制节奏，避免多候选串行等待导致超时成倍放大。
4. **等待节奏统一**：所有睡眠都走系统级的 ``wait_short/medium/long``
   （``core.android.waits``），不再出现裸 ``time.sleep`` 与写死的轮询间隔。
"""

from __future__ import annotations

import time
from pathlib import Path

from selenium.common.exceptions import StaleElementReferenceException, WebDriverException

from core.android.actions import AndroidDevice, Screen, to_by
from core.logger import get_logger
from data.constant.taobao import TAOBAO_PACKAGE


class PageNotReadyError(AssertionError):
    """页面在超时时间内未进入预期状态（属于业务断言失败，不是框架异常）。"""


class BasePage:
    """所有页面对象与可复用组件的基类。"""

    #: 页面名，用于日志与失败信息
    name: str = "base"
    #: 该页面/组件所属应用包名
    package: str = TAOBAO_PACKAGE
    #: 页面就绪的候选元素，命中任意一个即认为页面已加载
    ready_selectors: tuple[str, ...] = ()
    #: 默认超时（秒）
    default_timeout: float = 15.0

    def __init__(self, dev: AndroidDevice) -> None:
        self.dev = dev
        self.log = get_logger(f"pages.{self.name}")

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"

    # ------------------------------------------------------------------ #
    # 基础信息
    # ------------------------------------------------------------------ #
    @property
    def screen(self) -> Screen:
        return self.dev.screen

    @property
    def activity(self) -> str:
        return self.dev.current_activity

    # ------------------------------------------------------------------ #
    # 探测与等待
    # ------------------------------------------------------------------ #
    def probe(self, *selectors: str) -> str | None:
        """瞬时探测：元素存在且可见则返回命中的选择器，否则 None（不等待）。"""
        for selector in selectors:
            by, value = to_by(selector)
            try:
                if any(el.is_displayed() for el in self.dev.driver.find_elements(by, value)):
                    return selector
            except (WebDriverException, StaleElementReferenceException):
                continue
        return None

    def wait_any(self, *selectors: str, timeout: float | None = None) -> str:
        """轮询多个候选，返回第一个命中的选择器；全部超时则抛 PageNotReadyError。"""
        if not selectors:
            raise ValueError("wait_any 至少需要一个选择器")
        timeout = self.default_timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        while True:
            hit = self.probe(*selectors)
            if hit:
                return hit
            if time.monotonic() >= deadline:
                raise PageNotReadyError(
                    f"[{self.name}] {timeout}s 内未出现任一元素: {list(selectors)}（当前 activity={self.activity}）"
                )
            self.dev.wait_short("轮询等待")

    def exists_any(self, *selectors: str, timeout: float = 3.0) -> str | None:
        """带超时的存在性判断，不抛异常。"""
        try:
            return self.wait_any(*selectors, timeout=timeout)
        except PageNotReadyError:
            return None

    def wait_ready(self, timeout: float | None = None) -> BasePage:
        """等待页面就绪（子类通过 ready_selectors 声明）。"""
        if not self.ready_selectors:
            return self
        self.wait_any(*self.ready_selectors, timeout=timeout)
        self.log.info("页面就绪: %s", self.name)
        return self

    def wait_leave(self, timeout: float | None = None) -> bool:
        """等待页面元素全部消失（例如从详情页返回列表页）。"""
        if not self.ready_selectors:
            return True
        timeout = self.default_timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        while self.probe(*self.ready_selectors):
            if time.monotonic() >= deadline:
                return False
            self.dev.wait_short("轮询等待")
        return True

    def assert_here(self) -> None:
        """断言当前确实在该页面，失败信息带 activity，便于排查。"""
        hit = self.probe(*self.ready_selectors) if self.ready_selectors else None
        if not hit:
            raise PageNotReadyError(
                f"不在 [{self.name}] 页面（当前 activity={self.activity}，"
                f"候选元素均未出现: {list(self.ready_selectors)}）"
            )

    def is_here(self) -> bool:
        return bool(self.probe(*self.ready_selectors)) if self.ready_selectors else False

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def tap_any(self, *selectors: str, timeout: float | None = None) -> str:
        """点击第一个命中的候选元素，返回命中的选择器。"""
        selector = self.wait_any(*selectors, timeout=timeout)
        self.log.info("点击 %s", selector)
        self.dev.tap(selector)
        return selector

    def rect_of(self, selector: str) -> dict | None:
        """取第一个可见元素的 rect（x/y/width/height），取不到返回 None。"""
        by, value = to_by(selector)
        try:
            for el in self.dev.driver.find_elements(by, value):
                if el.is_displayed():
                    return el.rect
        except (WebDriverException, StaleElementReferenceException):
            pass
        return None

    def tap_center(self, *selectors: str, timeout: float | None = None) -> str:
        """按元素中心点击，返回命中的选择器。

        为什么不用 ``element.click()``：淘宝详情页的「加入购物车」按钮
        ``clickable=false``，WebDriver 的 click 会静默失败或抛不可交互异常；
        用坐标点击则不受该属性影响。实测这是详情页唯一稳定的点击方式。
        """
        selector = self.wait_any(*selectors, timeout=timeout)
        rect = self.rect_of(selector)
        if rect is None:
            self.log.warning("%s 取不到 rect，退化为元素点击", selector)
            self.dev.tap(selector)
            return selector
        x = rect["x"] + rect["width"] // 2
        y = rect["y"] + rect["height"] // 2
        self.log.info("坐标点击 %s @ (%d, %d)", selector, x, y)
        self.dev.tap_xy(x, y)
        return selector

    def tap_relative(self, rx: float, ry: float, reason: str = "") -> None:
        """按屏幕相对坐标点击（Weex 浮层等拿不到原生节点的场景）。"""
        self.log.info("相对坐标点击 (%.3f, %.3f) %s", rx, ry, reason)
        self.dev.tap_relative(rx, ry)

    def text_of_any(self, *selectors: str, timeout: float = 3.0, default: str = "") -> str:
        """读取第一个命中元素的文本，找不到返回 default（不抛异常）。"""
        selector = self.exists_any(*selectors, timeout=timeout)
        if not selector:
            return default
        try:
            return self.dev.get_text(selector, timeout=timeout) or default
        except WebDriverException:
            return default

    def text_or_desc(self, selector: str, timeout: float = 2.0) -> str:
        """读元素文案：优先 ``content-desc``，其次 ``text``；都没有返回空串。

        为什么需要它：``desc=xxx`` 命中的元素（底部 tab、金币挂件、活动挂件）
        **只有 content-desc 没有 text**，直接 ``get_text`` 拿到的是空串，
        会让人误以为「元素找到了但读不出文案」（实测踩过）。
        """
        by, value = to_by(selector)
        deadline = time.monotonic() + timeout
        while True:
            try:
                for element in self.dev.driver.find_elements(by, value):
                    if not element.is_displayed():
                        continue
                    desc = (element.get_attribute("content-desc") or "").strip()
                    text = (element.text or "").strip()
                    if desc or text:
                        return desc or text
            except (WebDriverException, StaleElementReferenceException):
                pass
            if time.monotonic() >= deadline:
                return ""
            self.dev.wait_short("轮询等待")

    def type_text(self, selector: str, text: str, clear: bool = True) -> None:
        self.log.info("输入 %r -> %s", text, selector)
        self.dev.input_text(selector, text, clear=clear)

    def back(self) -> None:
        self.log.info("返回上一页")
        self.dev.press("back")

    def scroll_down(self, times: int = 1, scale: float = 0.6) -> None:
        """向下滑动列表（内容向上滚动）。"""
        for _ in range(times):
            self.dev.swipe_direction("up", scale=scale)
            self.dev.wait_short("滑动后等列表渲染")

    def scroll_to(self, *selectors: str, max_swipes: int = 5) -> str | None:
        """边滑边找，命中返回选择器，滑完仍没有返回 None。"""
        for _ in range(max_swipes):
            hit = self.probe(*selectors)
            if hit:
                return hit
            self.scroll_down()
        return self.probe(*selectors)

    # ------------------------------------------------------------------ #
    # 留证
    # ------------------------------------------------------------------ #
    def screenshot(self, tag: str) -> Path:
        return self.dev.screenshot(f"{self.name}_{tag}")

    def dump(self, tag: str = "ui") -> Path:
        """导出 UI 层级，用于新页面校准选择器。"""
        return self.dev.dump_page_source(f"{self.name}_{tag}")
