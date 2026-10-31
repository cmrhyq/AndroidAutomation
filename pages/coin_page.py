"""淘金币页面对象。

页面有两种形态：双 11 金币小镇（themis 容器，零 text/desc，只能截图）与原生/半原生页。
原生节点数量极大，字段一律由 snapshot() 一次遍历取回，不要逐字段各遍历一遍。"""

from __future__ import annotations

import re
from pathlib import Path

from selenium.common.exceptions import StaleElementReferenceException, WebDriverException
from selenium.webdriver.common.by import By

from core import get_logger
from core.android.actions import to_by
from data.constant.taobao import (
    COIN_BALANCE_PATTERNS,
    COIN_CLOSE_TEXTS,
    COIN_PAGE_READY_DESCS,
    COIN_TASK_ACTION_TEXTS,
)
from data.entity.taobao import CoinSnapshot
from pages.base_page import BasePage


logger = get_logger(__name__)

#: 金币余额合理区间上限（超过这个数一定是把页面上的其它数字吃进来了）
MAX_REASONABLE_BALANCE = 10_000_000


class CoinPage(BasePage):
    """淘金币页面（余额、签到、任务列表）。"""

    name = "coin_center"

    #: 页面就绪判据。
    #: 注意**不要**放 `desc~=金币` / `text~=金币` 这类宽泛 contains ——
    #: 首页挂件本身就是 `desc=领淘金币`，会把「还在首页」误判成「已进金币页」。
    ready_selectors: tuple[str, ...] = (
        *(f"desc={text}" for text in COIN_PAGE_READY_DESCS),
        *(f"text={text}" for text in COIN_PAGE_READY_DESCS),
    )

    #: 签到按钮。**候选必须克制**：每个候选都是一次全量遍历，9 个候选实测要 98 秒。
    SIGN_SELECTORS: tuple[str, ...] = (
        "text=签到",
        "text~=签到",
        "desc=签到",
    )

    #: 任务操作按钮
    TASK_ACTION_SELECTORS: tuple[str, ...] = tuple(f"text={text}" for text in COIN_TASK_ACTION_TEXTS)

    #: 关闭 / 返回类按钮
    CLOSE_SELECTORS: tuple[str, ...] = tuple(f"desc={text}" for text in COIN_CLOSE_TEXTS[:3])

    #: 已签到的文案特征
    SIGNED_HINTS: tuple[str, ...] = ("已签到", "明天再来", "今日已领")

    #: 余额解析规则（只取前两条带「金币」的规则，丢掉裸数字规则）
    BALANCE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(re.compile(pattern) for pattern in COIN_BALANCE_PATTERNS[:2])

    #: 金币页所在的渲染容器特征
    ACTIVITY_HINTS: tuple[str, ...] = ("TMSActivity", "themis", "Coin", "coin")

    #: 任务名特征词（任务行一般是「任务名 + 去完成」并排）
    TASK_KEYWORDS: tuple[str, ...] = ("浏览", "逛", "领取", "签到", "答题", "搜索", "看")

    # ------------------------------------------------------------------ #
    # 一次遍历取全量
    # ------------------------------------------------------------------ #
    def _visible_texts(self) -> list[tuple[str, str]]:
        """收集当前页所有可见元素的 (text, content-desc)。

        整个类**只允许在这里做全量遍历**，其它方法复用它或 ``snapshot()``：
        金币页元素数以千计，一次 ``find_elements`` 就要数秒。
        """
        results: list[tuple[str, str]] = []
        try:
            elements = self.dev.driver.find_elements(By.XPATH, "//*[@text or @content-desc]")
        except (WebDriverException, StaleElementReferenceException):
            return results
        for element in elements:
            try:
                if not element.is_displayed():
                    continue
                text = (element.text or "").strip()
                desc = (element.get_attribute("content-desc") or "").strip()
                if text or desc:
                    results.append((text, desc))
            except (WebDriverException, StaleElementReferenceException):
                continue
        return results

    def _parse_balance(self, texts: list[tuple[str, str]]) -> int:
        """从「含金币的文案」里解析余额；解析不到或数值不合理返回 -1。"""
        for text, desc in texts:
            for raw in (text, desc):
                if "金币" not in raw:
                    continue
                for pattern in self.BALANCE_PATTERNS:
                    match = pattern.search(raw.replace(",", ""))
                    if not match:
                        continue
                    try:
                        value = int(match.group(1))
                    except (TypeError, ValueError):
                        continue
                    if 0 <= value <= MAX_REASONABLE_BALANCE:
                        return value
                    logger.warning("余额解析 %d 超出合理区间，已忽略（原文 %r）", value, raw)
        return -1

    def snapshot(self) -> CoinSnapshot:
        """一次遍历取余额 + 签到态 + 任务名。

        重页面保护：金币小镇是 themis 容器且节点数以千计，一次
        ``find_elements('//*[@text]')`` 会直接把 UiAutomator2 的
        instrumentation **拖崩**（实测报 ``instrumentation process is not running``，
        整个会话报废）。所以命中渲染容器时**直接返回空快照**，不遍历。
        """
        if self.on_coin_container():
            logger.info("金币页为 themis/Weex 渲染容器，跳过内容遍历（避免 instrumentation 崩溃），返回空快照")
            return CoinSnapshot()
        texts = self._visible_texts()
        snap = CoinSnapshot(text_nodes=len(texts))
        if not texts:
            return snap

        snap.balance = self._parse_balance(texts)

        for text, desc in texts:
            if any(hint in f"{text}{desc}" for hint in self.SIGNED_HINTS):
                snap.signed = True
            if text and text not in COIN_TASK_ACTION_TEXTS and any(token in text for token in self.TASK_KEYWORDS):
                snap.tasks.append(text)
        return snap

    # ------------------------------------------------------------------ #
    # 单项读取（各自一次遍历；组合场景请直接用 snapshot）
    # ------------------------------------------------------------------ #
    def balance(self) -> int:
        return self.snapshot().balance

    def signed_today(self) -> bool:
        return self.snapshot().signed

    def task_names(self) -> list[str]:
        return self.snapshot().tasks

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def sign_in(self) -> bool:
        """执行签到。已签到 / 找不到按钮 / 渲染层页面都返回 False（不抛异常）。"""
        if self.on_coin_container():
            logger.warning("金币页是 themis/Weex 渲染容器，原生无签到按钮可点，跳过签到（以截图留证）")
            return False

        if self.signed_today():
            logger.info("今日已签到，跳过")
            return False

        # 候选只有 3 个，且只做一次探测，不做多轮轮询（避免秒级遍历被成倍放大）
        selector = self.exists_any(*self.SIGN_SELECTORS, timeout=2.0)
        if not selector:
            logger.warning(
                "未找到签到按钮（候选 %s 均未命中）：金币页可能是 themis 渲染，或当前无签到位",
                list(self.SIGN_SELECTORS),
            )
            return False

        logger.info("点击签到: %s", selector)
        self.tap_center(selector, timeout=2.0)
        self.dev.wait_medium("签到后等状态与余额刷新")
        return True

    def do_task(self, index: int = 0) -> bool:
        """点击第 index 个任务的「去完成」。"""
        hit = 0
        for selector in self.TASK_ACTION_SELECTORS:
            by, value = to_by(selector)
            try:
                elements = [element for element in self.dev.driver.find_elements(by, value) if element.is_displayed()]
            except (WebDriverException, StaleElementReferenceException):
                continue
            for element in elements:
                if hit == index:
                    rect = element.rect
                    logger.info("点击第 %d 个任务按钮 %r", index + 1, selector)
                    self.dev.tap_xy(rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2)
                    return True
                hit += 1
        logger.warning("任务列表里只有 %d 个可点按钮，取不到第 %d 个", hit, index + 1)
        return False

    def close(self) -> bool:
        """关闭金币页回到上一页（优先点关闭按钮，退化为返回键）。"""
        selector = self.exists_any(*self.CLOSE_SELECTORS, timeout=1.5)
        if selector:
            self.tap_center(selector, timeout=1.5)
            return True
        self.back()
        return False

    # ------------------------------------------------------------------ #
    # 就绪判定
    # ------------------------------------------------------------------ #
    def on_coin_container(self) -> bool:
        """当前是否停在金币页容器上（themis / TMSActivity）。

        这是金币页最可靠的信号：内容可能在渲染层，但 Activity 是真实的。
        """
        activity = self.activity or ""
        return any(hint in activity for hint in self.ACTIVITY_HINTS)

    def wait_ready_soft(self, timeout: float = 10.0) -> bool:
        """等待金币页就绪，超时返回 False（不抛异常）。

        判定顺序很关键：**先看 Activity，再看内容节点**。
        themis 容器页节点数以千计，先跑选择器轮询不仅慢（数秒/次），
        还可能把 instrumentation 拖崩；而 Activity 是免费且可靠的信号。
        """
        if self.on_coin_container():
            logger.info(
                "金币页已跳转（activity=%s）。themis/Weex 渲染容器，跳过内容遍历"
                "（全量遍历会拖崩 instrumentation），余额与签到状态改由截图留证",
                self.activity,
            )
            return True

        try:
            self.wait_any(*self.ready_selectors, timeout=timeout)
        except Exception:  # noqa: BLE001 - 内容层读不到属预期
            logger.warning(
                "金币页判定失败（%.1fs）：内容节点未命中，且 activity=%s 不含金币容器特征。"
                "可调用 calibrate() 导层级后补齐 ready_selectors / ACTIVITY_HINTS",
                timeout,
                self.activity,
            )
            return False

        logger.info("金币页就绪（已识别到内容节点）")
        return True

    # ------------------------------------------------------------------ #
    # 留证 / 校准
    # ------------------------------------------------------------------ #
    def capture(self, tag: str = "snapshot") -> Path:
        return self.screenshot(tag)

    def calibrate(self) -> tuple[Path, Path]:
        """导出截图 + UI 层级，用于补齐本页的候选选择器。"""
        shot = self.screenshot("calibrate")
        xml = self.dump("calibrate")
        logger.info("金币页校准产物: screenshot=%s xml=%s", shot, xml)
        return shot, xml
