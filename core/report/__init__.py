"""测试报告模块：步骤埋点 + pytest 插件 + 单文件 HTML 报告。

对外只暴露少量入口，业务代码典型的用法是::

    from core.report import step, note

    with step("打开淘宝首页"):
        home = TaobaoFlow(dev).start(fresh=True)
    note(keyword="蓝牙耳机")
"""

from __future__ import annotations

from core.report.collector import StepCollector, bind, current, note, step, unbind
from core.report.models import (
    ERROR,
    FAILED,
    PASSED,
    SKIPPED,
    CaseRecord,
    EnvironmentInfo,
    RunRecord,
    StepRecord,
    human_duration,
)

__all__ = [
    "ERROR",
    "FAILED",
    "PASSED",
    "SKIPPED",
    "CaseRecord",
    "EnvironmentInfo",
    "RunRecord",
    "StepCollector",
    "StepRecord",
    "bind",
    "current",
    "human_duration",
    "note",
    "step",
    "unbind",
]
