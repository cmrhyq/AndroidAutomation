"""测试报告模块：Allure 埋点封装 + pytest 插件。

业务代码只需要记住两个函数::

    from core.report import step, note

    with step("打开淘宝首页"):
        home = TaobaoFlow(dev).start(fresh=True)
    note(keyword="蓝牙耳机", 卡片数="20")

- ``step`` 映射成 Allure 的可折叠步骤，失败时自动补现场截图；
- ``note`` 攒成一张「业务上下文」表附件；
- 没有挂 pytest 插件时（脚本单独跑业务流）两者都是透明 no-op。

需要挂截图 / JSON / HTML / 文件等附件时有两种写法，实现完全相同::

    from core.report import attach_image, attach_json      # 函数式，框架内部推荐
    from core.report import AllureHelper                   # 类式，贴合外部接入习惯

    attach_image(dev, "金币中心现场")
    AllureHelper.attach_json({"角标": "0 → 1"}, "加购结果")

HTML 报告由 Allure 生成，本模块不自己画页面。
"""

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
