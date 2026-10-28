"""步骤收集器：给业务流提供 ``with step("步骤名"):`` 埋点能力。

为什么需要它
------------
UI 自动化失败时，pytest 只会告诉你「第 40 行断言失败」。但真正想知道的是
「走到哪一步炸的、每步花了多久」。业务流里包一层 ``step`` 就能回答这个问题，
而页面对象无需感知报告的存在（零侵入）。
"""

from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from core.report.models import FAILED, PASSED, CaseRecord, StepRecord


class StepCollector:
    """当前用例的步骤收集器（每个用例一个实例，由报告插件注入）。"""

    def __init__(self, case: CaseRecord) -> None:
        self.case = case
        self._depth = 0

    # ------------------------------------------------------------------ #
    # 步骤埋点
    # ------------------------------------------------------------------ #
    @contextmanager
    def step(self, name: str, screenshot_on_error: object | None = None) -> Iterator[StepRecord]:
        """记录一个步骤的耗时与结果。

        Args:
            name: 步骤名，报告里直接展示，建议用中文短句。
            screenshot_on_error: 失败时用于截图的 AndroidDevice（可选）。传了就
                自动留证，没传就只记错误文本 —— 避免业务流被迫依赖设备对象。
        """
        record = StepRecord(name=name, started_at=time.time(), status=PASSED)
        start = time.perf_counter()
        self.case.steps.append(record)
        self._depth += 1
        # 嵌套步骤缩进展示，让「搜索 → 点卡片 → 加购」的层级关系看得见
        record.name = ("　" * (self._depth - 1)) + name
        try:
            yield record
        except Exception as exc:
            record.status = FAILED
            record.duration = time.perf_counter() - start
            record.error = f"{type(exc).__name__}: {exc}"
            if screenshot_on_error is not None:
                record.screenshot = _safe_screenshot(screenshot_on_error, name)
                if record.screenshot:
                    self.case.screenshots.append(record.screenshot)
            raise
        else:
            record.duration = time.perf_counter() - start
        finally:
            self._depth -= 1

    def note(self, **context: str) -> None:
        """记录业务上下文（搜索词、商品标题等），显示在用例详情里。"""
        self.case.context.update({k: str(v) for k, v in context.items() if v})

    def attach_traceback(self) -> None:
        """把当前异常栈写入用例（供报告展示）。"""
        self.case.traceback_text = traceback.format_exc()


# ---------------------------------------------------------------------- #
# 全局注册表：pytest 插件按用例切换，业务流随时取当前收集器
# ---------------------------------------------------------------------- #
_local = threading.local()


def bind(case: CaseRecord) -> StepCollector:
    """绑定当前线程的收集器（由 pytest 插件在用例开始时调用）。"""
    collector = StepCollector(case)
    _local.collector = collector
    return collector


def unbind() -> None:
    _local.collector = None


def current() -> StepCollector | None:
    return getattr(_local, "collector", None)


@contextmanager
def step(name: str, screenshot_on_error: object | None = None) -> Iterator[StepRecord]:
    """在业务流里包一步。没有绑定收集器时退化为透明 no-op。

    这样同一份业务代码既能被 pytest 跑（带报告），也能被脚本单独调用（不带报告）。
    """
    collector = current()
    if collector is None:
        yield StepRecord(name=name)
        return
    with collector.step(name, screenshot_on_error=screenshot_on_error) as record:
        yield record


def note(**context: str) -> None:
    collector = current()
    if collector is not None:
        collector.note(**context)


def _safe_screenshot(dev: object, tag: str) -> Path | None:
    """尽力截图，任何异常都吞掉 —— 报告不能因为截图失败而掩盖真正的错误。"""
    take = getattr(dev, "screenshot", None)
    if not callable(take):
        return None
    try:
        safe_tag = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in tag)[:40]
        return take(f"step_{safe_tag}")
    except Exception:
        return None
