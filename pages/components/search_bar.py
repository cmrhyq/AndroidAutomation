"""搜索栏组件：首页搜索入口 + 关键词提交。

两段式交互（先点入口进搜索页，再输入关键词），两个阶段的选择器都会因版本变化，
所以每一步都给多个候选。

实测（PFGM00 / 淘宝 10.x）：首页搜索入口与右侧橙色「搜索」按钮都用 content-desc，
**不要**用 `text=推荐` 之类的顶部 tab 文案做兜底 —— 那会点进推荐流而不是搜索页。
"""

from __future__ import annotations

from pages.base_page import BasePage


class SearchBar(BasePage):
    """淘宝搜索栏。"""

    name = "search_bar"

    #: 首页搜索入口（点进去才会出现输入框）。desc=搜索栏 实测命中 [184,229][756,322]
    ENTRY_SELECTORS: tuple[str, ...] = (
        "desc=搜索栏",
        "id=com.taobao.taobao:id/search_bar",
        "text~=搜索宝贝",
        "text~=搜索天猫",
    )

    #: 输入框
    INPUT_SELECTORS: tuple[str, ...] = (
        "cls=android.widget.EditText",
        "xpath=//android.widget.EditText",
    )

    #: 提交按钮（部分版本只有键盘上的「搜索」键）
    SUBMIT_SELECTORS: tuple[str, ...] = (
        "id=com.taobao.taobao:id/search_button",
        "desc=搜索",
        "text=搜索",
    )

    def open(self, timeout: float = 8.0) -> None:
        """从当前页面点开搜索输入页。"""
        self.tap_any(*self.ENTRY_SELECTORS, timeout=timeout)
        self.wait_any(*self.INPUT_SELECTORS, timeout=timeout)
        self.dev.wait_short("等搜索页展开动画")

    def submit(self, keyword: str, timeout: float = 10.0) -> None:
        """在已打开的搜索页输入关键词并提交。"""
        if not self.exists_any(*self.INPUT_SELECTORS, timeout=timeout):
            self.open(timeout=timeout)

        input_selector = self.wait_any(*self.INPUT_SELECTORS, timeout=timeout)
        self.type_text(input_selector, keyword)

        # 优先点「搜索」按钮（实测 id=searchbtn 稳定）；没有按钮才用键盘回车。
        # 实测回车键不一定会触发搜索，所以按钮必须优先。
        if self.exists_any(*self.SUBMIT_SELECTORS, timeout=3.0):
            self.tap_center(*self.SUBMIT_SELECTORS, timeout=3.0)
        else:
            self.log.info("未找到搜索按钮，改用键盘回车提交")
            self.dev.press("enter")
        self.dev.wait_medium("等搜索结果页加载")

    def search_phrase(self, keyword: str, timeout: float = 8.0) -> None:
        """一步完成「打开搜索页 + 输入 + 提交」。"""
        self.open(timeout=timeout)
        self.submit(keyword, timeout=timeout)
