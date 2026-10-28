"""测试报告数据模型。

设计原则
--------
1. **纯数据**：模型只负责承载，不做 IO，方便序列化为 JSON 供二次消费。
2. **步骤粒度**：一个用例由多个步骤（StepRecord）组成，失败时能直接看出
   是哪一步炸的 —— 这正是 UI 自动化的排障刚需。
3. **截图内嵌**：截图以 base64 存进模型，报告单文件自包含，发给别人不会丢图。
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

    @property
    def failed_steps(self) -> list[StepRecord]:
        return [s for s in self.steps if s.status == FAILED]


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
    #: 命令行摘要（pytest -q 的尾部统计），原样贴进报告
    command: str = ""

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
    def skipped(self) -> int:
        return self.count(SKIPPED)

    @property
    def duration(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    @property
    def pass_rate(self) -> float:
        """通过率按「执行过的用例」算，跳过的不计入分母。"""
        executed = self.total - self.skipped
        return (self.passed / executed * 100.0) if executed else 0.0

    @property
    def is_all_green(self) -> bool:
        return self.failed == 0 and self.total > 0

    def sorted_cases(self) -> list[CaseRecord]:
        """失败优先，便于第一眼看到问题。"""
        order = {FAILED: 0, ERROR: 1, SKIPPED: 2, PASSED: 3}
        return sorted(self.cases, key=lambda c: (order.get(c.status, 9), c.started_at))


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
