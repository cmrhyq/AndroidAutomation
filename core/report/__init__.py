"""测试报告（Allure）埋点封装。

业务代码用 step() 包步骤、note() 记上下文、attach_*() 挂附件；
未挂 pytest 插件时（脚本单独跑业务流）全部退化为 no-op。"""

from __future__ import annotations

from core.report.collector import (
    attach_file,
    attach_html,
    attach_image,
    attach_json,
    attach_table,
    attach_text,
    disable,
    enable,
    is_enabled,
    note,
    step,
)
from core.report.helper import AllureHelper, allure_step

__all__ = [
    "AllureHelper",
    "allure_step",
    "attach_file",
    "attach_html",
    "attach_image",
    "attach_json",
    "attach_table",
    "attach_text",
    "disable",
    "enable",
    "is_enabled",
    "note",
    "step",
]
