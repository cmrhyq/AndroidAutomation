"""业务流层：跨页面的端到端编排。

``flows/damai_flow.py`` —— 大麦抢票（搜索 → 详情 → 等开售 → 票档 → 观演人 → 提交）。

用例与脚本只依赖本层，不直接碰页面细节。
"""

from __future__ import annotations

from flows.damai_flow import DamaiFlow, FlowError

__all__ = ["DamaiFlow", "FlowError"]
