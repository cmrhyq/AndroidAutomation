"""淘宝搜索结果页页面对象。

真机实测结构（PFGM00 / 淘宝 10.x / 1080x2400）
------------------------------------------------
排序栏位于 y≈728~823，每个排序项的可点击文案挂在 ``content-desc`` 上，
且带选中态后缀，例如::

    desc="综合"          desc="销量,未选中"     desc="全部,已选中"
    desc="天猫,未选中"    desc="店铺,未选中"     desc="闪购,未选中"
    desc="筛选按钮"

商品卡片是**无 resource-id 的 ViewGroup/View**，可点击，真正的商品信息
拼在 ``content-desc`` 里，格式非常规整::

    【音质TOP·1】德国远超千元音质蓝牙耳机适用苹果华为小米 272.00元 全网5000+人付款
    千问AI耳夹式耳机 耳夹式蓝牙运动 Bose调音 882.87元 1000+人付款

所以标题和价格直接从 desc 正则解析，比自己拼选择器可靠得多。
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

from selenium.common.exceptions import WebDriverException

from pages.base_page import BasePage

if TYPE_CHECKING:
    from pages.detail_page import DetailPage

#: 卡片 desc 里的价格段：「272.00元」「69元」
_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*元")
#: 付款人数段：「全网5000+人付款」「3万+人付款」
_SALES_RE = re.compile(r"([\d.]+\+?[万千]?\+?人付款)")


class SearchResultPage(BasePage):
    """搜索结果列表页（含排序栏、商品卡片瀑布流）。"""

    name = "search_result"

    #: 就绪判据：排序栏是结果页独有的，首页和搜索输入页都没有
    ready_selectors: tuple[str, ...] = (
        "desc~=综合",
        "desc~=销量",
        "desc~=筛选按钮",
        "id=btn_go_back",
    )

    #: 排序项文案前缀（真实 desc 带 ,未选中 / ,已选中 后缀，用 contains 匹配）
    SORT_LABELS: dict[str, str] = {
        "综合": "综合",
        "销量": "销量",
        "全部": "全部",
        "天猫": "天猫",
        "店铺": "店铺",
        "闪购": "闪购",
    }

    #: 无结果提示
    EMPTY_SELECTORS: tuple[str, ...] = (
        "text~=没有找到",
        "text~=暂无相关",
        "text~=换个词试试",
        "text~=没找到",
    )

    #: 价格提取不到时的兜底正则（desc 末尾常见裸数字）
    _BARE_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*$")

    # ------------------------------------------------------------------ #
    # 卡片识别
    # ------------------------------------------------------------------ #
    def _card_elements(self) -> list[tuple[object, str, str, str]]:
        """返回 [(element, desc, title, price)]，只保留真正像商品卡片的元素。

        判定：可点击 + content-desc 含「元」+ 长度 > 12。
        淘宝把「标题 价格元 付款人数」整段拼进 desc，所以含「元」是个很强的特征；
        而长度过滤能排掉「更多」「进店」这类短 desc 的功能按钮。
        """
        cards: list[tuple[object, str, str, str]] = []
        seen: set[tuple[int, int]] = set()
        try:
            elements = self.dev.driver.find_elements("xpath", "//*[@clickable='true']")
        except WebDriverException:
            return cards

        for el in elements:
            try:
                desc = (el.get_attribute("content-desc") or "").strip()
            except WebDriverException:
                continue
            if "元" not in desc or len(desc) <= 12:
                continue
            try:
                rect = el.rect
            except WebDriverException:
                continue
            # 同一张卡片常有多层可点击节点，用左上角坐标去重
            key = (rect["x"], rect["y"])
            if key in seen:
                continue
            seen.add(key)
            title, price = self.parse_card(desc)
            cards.append((el, desc, title, price))

        # 按屏幕阅读顺序（先 y 后 x）排序，保证 index=0 真的是第一个商品
        cards.sort(key=lambda item: (item[0].rect["y"], item[0].rect["x"]))
        return cards

    @staticmethod
    def parse_card(desc: str) -> tuple[str, str]:
        """从卡片 desc 里拆出 (标题, 价格)。解析不出价格时返回空串。"""
        match = _PRICE_RE.search(desc)
        if not match:
            return desc.strip(), ""
        price = f"¥{match.group(1)}"
        title = desc[: match.start()].strip()
        return title, price

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def result_count(self) -> int:
        return len(self._card_elements())

    def has_results(self, timeout: float = 10.0) -> bool:
        """先看卡片（成功场景立即返回），再看「无结果」提示，避免白等。"""
        if self._card_elements():
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._card_elements():
                return True
            if self.probe(*self.EMPTY_SELECTORS):
                self.log.info("命中无结果提示: %s", self.probe(*self.EMPTY_SELECTORS))
                return False
            time.sleep(self.poll_interval)
        return bool(self._card_elements())

    def is_empty(self, timeout: float = 3.0) -> bool:
        return bool(self.exists_any(*self.EMPTY_SELECTORS, timeout=timeout))

    def first_product(self) -> tuple[str, str]:
        """第一条商品的 (标题, 价格)，读不到返回 ("", "")。"""
        cards = self._card_elements()
        if not cards:
            return "", ""
        _, _, title, price = cards[0]
        return title, price

    def product_at(self, index: int = 0) -> tuple[str, str]:
        cards = self._card_elements()
        if index >= len(cards):
            return "", ""
        _, _, title, price = cards[index]
        return title, price

    def all_products(self, limit: int = 20) -> list[tuple[str, str]]:
        return [(t, p) for _, _, t, p in self._card_elements()[:limit]]

    def sort_by(self, label: str = "销量") -> None:
        """切换排序。真实 desc 带选中态后缀，用 contains 匹配即可。"""
        key = self.SORT_LABELS.get(label, label)
        selector = self.exists_any(f"desc~={key}", timeout=5.0)
        if not selector:
            raise AssertionError(f"排序栏未找到 {label!r}（当前 activity={self.activity}）")
        self.log.info("切换排序: %s -> %s", label, selector)
        self.tap_center(selector)
        time.sleep(2.0)

    # ------------------------------------------------------------------ #
    # 进入详情
    # ------------------------------------------------------------------ #
    def open_product(self, index: int = 0, timeout: float = 10.0) -> DetailPage:
        """点开第 index 个商品（0 起），返回详情页。"""
        from pages.detail_page import DetailPage

        deadline = time.monotonic() + timeout
        cards: list = []
        while time.monotonic() < deadline:
            cards = self._card_elements()
            if len(cards) > index:
                break
            time.sleep(self.poll_interval)

        if len(cards) > index:
            el, desc, title, price = cards[index]
            rect = el.rect
            x = rect["x"] + rect["width"] // 2
            y = rect["y"] + rect["height"] // 2
            self.log.info("打开第 %d 个商品: %r %s @ (%d, %d)", index + 1, title[:24], price, x, y)
            self.dev.tap_xy(x, y)
        else:
            self.log.warning("未识别到商品卡片（共 %d 张），退化为坐标点击首个结果", len(cards))
            self.tap_relative(0.5, 0.58, reason="商品卡片未识别")

        return DetailPage(self.dev).wait_ready(timeout=timeout)
