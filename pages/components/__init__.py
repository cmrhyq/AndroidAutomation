"""可复用组件：浮层清理、底部导航栏、搜索栏。

组件与页面对象的区别：组件不绑定某个具体页面，可被多个页面复用（例如浮层清理
在首页、搜索结果页、详情页都要跑）。
"""

from __future__ import annotations

from pages.components.bottom_tab import BottomTab
from pages.components.popup import PopupCleaner
from pages.components.search_bar import SearchBar

__all__ = ["BottomTab", "PopupCleaner", "SearchBar"]
