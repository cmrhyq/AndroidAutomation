"""业务流层：跨页面的端到端编排。

- ``flows/taobao_flow.py`` —— 淘宝主流程（启动 → 搜索 → 详情 → 加购 → 购物车校验）

用例只依赖本层，不直接碰页面细节。
"""

from __future__ import annotations

from flows.taobao_flow import CartFlowResult, FlowError, ProductSnapshot, TaobaoFlow

__all__ = ["TaobaoFlow", "FlowError", "ProductSnapshot", "CartFlowResult"]
