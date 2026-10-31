"""测试报告数据模型。

设计原则
--------
1. **纯数据**：模型只负责承载，不做 IO，方便序列化为 JSON 供二次消费。
2. **步骤粒度**：一个用例由多个步骤（StepRecord）组成，失败时能直接看出
   是哪一步炸的 —— 这正是 UI 自动化的排障刚需。
3. **截图内嵌**：截图以 base64 存进模型，报告单文件自包含，发给别人不会丢图。
4. **聚合在模型层**：分组 / 失败原因 / 慢用例 / marker 这些统计都放在这里，
   渲染层只负责画，避免 HTML 里出现统计逻辑（也方便单测直接断言数字）。
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

# 用例状态（与 pytest 的 outcome 对齐）
PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"
ERROR = "error"

#: 报告里的排序权重：失败优先，第一眼就能看到问题
_STATUS_ORDER = {FAILED: 0, ERROR: 1, SKIPPED: 2, PASSED: 3}

#: 收集器用全角空格做嵌套缩进（见 StepCollector.step）
INDENT_CHAR = "\u3000"


@dataclass
class StepRecord:
    """一个业务步骤（如「打开淘宝首页」）。"""

    name: str
    started_at: float = 0.0
    duration: float = 0.0
    status: str = PASSED
    error: str = ""
    #: 该步骤失败时的截图（文件路径，渲染时再转 base64）
    screenshot: Path | None = None

    @property
    def display_name(self) -> str:
        """去掉缩进前缀后的步骤名（JSON/统计里用干净的名字）。"""
        return self.name.strip()

    @property
    def level(self) -> int:
        """嵌套层级，用于渲染缩进。"""
        return len(self.name) - len(self.name.lstrip(INDENT_CHAR))

    @property
    def icon(self) -> str:
        return {PASSED: "PASS", FAILED: "FAIL", SKIPPED: "SKIP"}.get(self.status, "info")


@dataclass
class CaseRecord:
    """一条用例的完整轨迹。"""

    nodeid: str
    name: str
    status: str = PASSED
    started_at: float = 0.0
    duration: float = 0.0
    steps: list[StepRecord] = field(default_factory=list)
    error_type: str = ""
    error_message: str = ""
    traceback_text: str = ""
    #: 失败截图（可能多张：步骤内一张、用例失败钩子再补一张）
    screenshots: list[Path] = field(default_factory=list)
    #: 用例自带的说明 / 业务上下文（如搜索词、商品标题）
    context: dict[str, str] = field(default_factory=dict)
    #: 所属测试文件（相对路径，如 tests/test_taobao.py），用于分组
    file: str = ""
    #: 分组名（默认取测试文件名，如 test_taobao）
    group: str = ""
    #: pytest markers（device / unit / slow …）
    markers: list[str] = field(default_factory=list)
    #: 用例 docstring 全文，展示在用例详情里当业务说明
    description: str = ""

    @property
    def failed_steps(self) -> list[StepRecord]:
        return [s for s in self.steps if s.status == FAILED]

    @property
    def first_failed_step(self) -> StepRecord | None:
        """最先失败的步骤 —— 失败摘要里直接点名「炸在哪一步」。"""
        failed = self.failed_steps
        return failed[0] if failed else None

    @property
    def is_failure(self) -> bool:
        return self.status in {FAILED, ERROR}


@dataclass
class GroupStat:
    """按测试文件聚合的分组统计。"""

    name: str
    cases: list[CaseRecord] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.cases)

    def count(self, status: str) -> int:
        return sum(1 for c in self.cases if c.status == status)

    @property
    def passed(self) -> int:
        return self.count(PASSED)

    @property
    def failed(self) -> int:
        return self.count(FAILED) + self.count(ERROR)

    @property
    def skipped(self) -> int:
        return self.count(SKIPPED)

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.cases)

    @property
    def pass_rate(self) -> float:
        executed = self.total - self.skipped
        return (self.passed / executed * 100.0) if executed else 0.0

    @property
    def has_failure(self) -> bool:
        return self.failed > 0

    def sorted_cases(self) -> list[CaseRecord]:
        return sorted(self.cases, key=lambda c: (_STATUS_ORDER.get(c.status, 9), c.started_at))


@dataclass
class ErrorStat:
    """一类失败原因的聚合（同一异常类型合并成一条）。"""

    error_type: str
    cases: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.cases)


@dataclass
class EnvironmentInfo:
    """运行环境快照，贴进报告头部，便于复现问题。"""

    device_model: str = ""
    device_udid: str = ""
    android_version: str = ""
    screen_size: str = ""
    app_package: str = ""
    app_version: str = ""
    appium_version: str = ""
    appium_url: str = ""
    python_version: str = ""
    host: str = ""
    platform: str = ""

    def as_rows(self) -> list[tuple[str, str]]:
        """转成 (标签, 值) 列表，跳过空值。"""
        table = [
            ("设备型号", self.device_model),
            ("设备序列号", self.device_udid),
            ("Android 版本", self.android_version),
            ("屏幕分辨率", self.screen_size),
            ("被测应用", self.app_package),
            ("应用版本", self.app_version),
            ("Appium 版本", self.appium_version),
            ("Appium 地址", self.appium_url),
            ("Python 版本", self.python_version),
            ("执行主机", self.host),
            ("操作系统", self.platform),
        ]
        return [(k, v) for k, v in table if v]


@dataclass
class RunRecord:
    """一次完整测试运行。"""

    started_at: float = 0.0
    finished_at: float = 0.0
    cases: list[CaseRecord] = field(default_factory=list)
    env: EnvironmentInfo = field(default_factory=EnvironmentInfo)
    title: str = "淘宝 UI 自动化测试报告"
    #: 命令行摘要（可复现的 pytest 命令）
    command: str = ""
    #: 本次运行的关键参数（等待倍率、关键词、报告参数…），报告里单独一块展示
    options: dict[str, str] = field(default_factory=dict)
    #: pytest 退出码与含义
    exit_status: int = 0
    exit_status_text: str = ""
    #: pytest 版本
    pytest_version: str = ""

    # ---------------------------------------------------------------- #
    # 统计
    # ---------------------------------------------------------------- #
    @property
    def total(self) -> int:
        return len(self.cases)

    def count(self, status: str) -> int:
        return sum(1 for c in self.cases if c.status == status)

    @property
    def passed(self) -> int:
        return self.count(PASSED)

    @property
    def failed(self) -> int:
        return self.count(FAILED) + self.count(ERROR)

    @property
    def errors(self) -> int:
        """其中「夹具/环境错误」的数量（与断言失败区分）。"""
        return self.count(ERROR)

    @property
    def skipped(self) -> int:
        return self.count(SKIPPED)

    @property
    def executed(self) -> int:
        return self.total - self.skipped

    @property
    def duration(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    @property
    def avg_case_duration(self) -> float:
        """平均用例耗时。跳过的不算 —— 它们的「耗时」其实是夹具建会话的时间。"""
        durations = [c.duration for c in self.cases if c.duration > 0 and c.status != SKIPPED]
        return sum(durations) / len(durations) if durations else 0.0

    @property
    def pass_rate(self) -> float:
        """通过率按「执行过的用例」算，跳过的不计入分母。"""
        return (self.passed / self.executed * 100.0) if self.executed else 0.0

    @property
    def is_all_green(self) -> bool:
        return self.failed == 0 and self.total > 0

    # ---------------------------------------------------------------- #
    # 聚合
    # ---------------------------------------------------------------- #
    def sorted_cases(self) -> list[CaseRecord]:
        """失败优先，便于第一眼看到问题。"""
        return sorted(self.cases, key=lambda c: (_STATUS_ORDER.get(c.status, 9), c.started_at))

    def groups(self) -> list[GroupStat]:
        """按测试文件分组（保持用例首次出现的顺序）。"""
        buckets: dict[str, GroupStat] = {}
        for case in self.cases:
            key = case.group or case.file or "未分组"
            buckets.setdefault(key, GroupStat(name=key)).cases.append(case)
        return list(buckets.values())

    def error_stats(self) -> list[ErrorStat]:
        """失败原因聚合：同一异常类型合并，按出现次数降序。"""
        buckets: dict[str, ErrorStat] = {}
        for case in self.cases:
            if not case.is_failure:
                continue
            key = case.error_type or _guess_error_type(case.error_message) or "未分类错误"
            buckets.setdefault(key, ErrorStat(error_type=key)).cases.append(case.name)
        return sorted(buckets.values(), key=lambda e: (-e.count, e.error_type))

    def slowest(self, limit: int = 5) -> list[CaseRecord]:
        """最慢的 N 条用例。

        跳过的不参与排行：设备不在线时 10 条用例会在 setup 阶段瞬间 skip，
        但每条都记着「等夹具建会话」的几十秒，全塞进慢用例榜会完全误导。
        """
        candidates = (c for c in self.cases if c.duration > 0 and c.status != SKIPPED)
        return sorted(candidates, key=lambda c: -c.duration)[:limit]

    def marker_stats(self) -> list[tuple[str, int, int]]:
        """marker 维度统计：[(marker, 用例数, 通过数)]，按用例数降序。"""
        totals: dict[str, int] = {}
        passed: dict[str, int] = {}
        for case in self.cases:
            for marker in case.markers:
                totals[marker] = totals.get(marker, 0) + 1
                if case.status == PASSED:
                    passed[marker] = passed.get(marker, 0) + 1
        return sorted(
            ((m, n, passed.get(m, 0)) for m, n in totals.items()),
            key=lambda item: (-item[1], item[0]),
        )


def _guess_error_type(message: str) -> str:
    """从错误文本里猜异常类型（longrepr 只有文本时的兜底）。"""
    head = (message or "").strip().splitlines()[0] if message else ""
    if ":" in head:
        head = head.split(":", 1)[0]
    return head.strip() if head and len(head) < 60 else ""


# ---------------------------------------------------------------------- #
# 工具
# ---------------------------------------------------------------------- #
def image_to_data_uri(path: Path | None) -> str:
    """把图片文件转成可直接写进 <img src> 的 data URI；失败返回空串。"""
    if path is None:
        return ""
    try:
        if not path.exists() or path.stat().st_size == 0:
            return ""
        raw = path.read_bytes()
    except OSError:
        return ""
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in {"jpg", "jpeg"} else suffix
    return f"data:image/{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def human_duration(seconds: float) -> str:
    """把秒数格式化成 1m23s / 4.2s 这种易读形式。"""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)}m{rest:.0f}s"
