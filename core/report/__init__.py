"""测试报告模块：Allure 埋点封装 + pytest 插件。

业务代码只需要记住两个函数::

    from core.report import step, note

    with step("打开淘宝首页"):
        home = TaobaoFlow(dev).start(fresh=True)
    note(keyword="蓝牙耳机", 卡片数="20")

- ``step`` 映射成 Allure 的可折叠步骤，失败时自动补现场截图；
- ``note`` 攒成一张「业务上下文」表附件；
- 没有挂 pytest 插件时（脚本单独跑业务流）两者都是透明 no-op。

HTML 报告由 Allure 生成，本模块不自己画页面。
"""

from __future__ import annotations

from core.report.collector import (
    attach_image,
    attach_table,
    attach_text,
    disable,
    enable,
    is_enabled,
    note,
    step,
)

__all__ = [
    "attach_image",
    "attach_table",
    "attach_text",
    "disable",
    "enable",
    "is_enabled",
    "note",
    "step",
]
