"""报告模块的离线用例：不需要设备、不需要 Appium，纯数据进 HTML/JSON 出。

为什么要有这组用例
------------------
报告是「测试的基础设施」——它自己坏掉时，最不该被误判成「被测应用有问题」。
所以这里用构造好的 RunRecord 做快照式断言：分组、失败聚合、慢用例、步骤截图、
JSON 字段、历史索引都必须稳定产出。全部标记 ``unit``，CI 里可以单独快跑::

    .venv/Scripts/python.exe -m pytest tests/test_report_offline.py -v
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from core.report import PASSED, StepCollector, step
from core.report.collector import unbind
from core.report.html import render_html, render_index, write_html, write_index, write_json
from core.report.models import (
    ERROR,
    FAILED,
    SKIPPED,
    CaseRecord,
    EnvironmentInfo,
    RunRecord,
    StepRecord,
    human_duration,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------- #
# 构造样本
# ---------------------------------------------------------------------- #
def _png(tmp_path: Path, name: str = "shot.png") -> Path:
    """造一个最小 PNG（内容不重要，能读出非空字节即可转 data URI）。"""
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return path


def _sample_run(tmp_path: Path) -> RunRecord:
    run = RunRecord(
        started_at=time.time() - 60,
        finished_at=time.time(),
        command="pytest tests/ -q",
        exit_status=1,
        exit_status_text="有用例失败",
        pytest_version="9.1.1",
        options={"AA_WAIT_SCALE": "1", "TAOBAO_KEYWORD": "蓝牙耳机"},
        env=EnvironmentInfo(
            device_model="PFGM00",
            device_udid="10.20.0.209:44053",
            android_version="12",
            screen_size="1080x2400",
            app_package="com.taobao.taobao",
            app_version="10.60.0",
            appium_version="2.17.1",
            python_version="3.11.9",
        ),
    )

    passed = CaseRecord(
        nodeid="tests/test_taobao.py::test_home_launch",
        name="启动淘宝后能进入首页",
        status=PASSED,
        duration=12.3,
        file="tests/test_taobao.py",
        group="test_taobao",
        markers=["device"],
        description="连通性冒烟",
        context={"频道": "推荐"},
    )
    passed.steps = [
        StepRecord(name="启动淘宝并等待首页就绪", duration=11.9),
        StepRecord(name="\u3000清浮层", duration=0.4),
    ]

    failed = CaseRecord(
        nodeid="tests/test_taobao.py::test_add_to_cart_increases_badge",
        name="加购后购物车角标数量增长",
        status=FAILED,
        duration=45.2,
        file="tests/test_taobao.py",
        group="test_taobao",
        markers=["device", "slow"],
        error_type="AssertionError",
        error_message="加购失败：购物车角标 0 -> 0",
    )
    shot = _png(tmp_path, "step_加入购物车.png")
    failed.steps = [
        StepRecord(
            name="加入购物车",
            duration=40.0,
            status=FAILED,
            error="AssertionError: 角标未增长",
            screenshot=shot,
        )
    ]
    failed.screenshots = [shot]

    errored = CaseRecord(
        nodeid="tests/test_taobao.py::test_cart_page_reachable",
        name="加购后能进入购物车页",
        status=ERROR,
        duration=0.2,
        file="tests/test_taobao.py",
        group="test_taobao",
        error_type="WebDriverException",
        error_message="instrumentation process is not running",
    )

    skipped = CaseRecord(
        nodeid="tests/test_taobao.py::test_claim_coins_flow",
        name="领淘金币链路",
        status=SKIPPED,
        duration=0.05,
        file="tests/test_taobao.py",
        group="test_taobao",
        markers=["device"],
        error_message="推荐页未投放金币入口",
    )

    run.cases = [passed, failed, errored, skipped]
    return run


# ---------------------------------------------------------------------- #
# 模型聚合
# ---------------------------------------------------------------------- #
def test_stats_ignore_skipped_in_pass_rate(tmp_path: Path):
    """通过率分母只算执行过的用例，跳过的不能拉低通过率。"""
    run = _sample_run(tmp_path)

    assert run.total == 4
    assert run.passed == 1
    assert run.failed == 2  # failed + error
    assert run.errors == 1
    assert run.skipped == 1
    assert run.executed == 3
    assert run.pass_rate == pytest.approx(100 / 3, rel=1e-3)


def test_groups_aggregate_by_file(tmp_path: Path):
    """分组按测试文件聚合，耗时与通过率都由用例汇总而来。"""
    run = _sample_run(tmp_path)
    groups = run.groups()

    assert [g.name for g in groups] == ["test_taobao"]
    group = groups[0]
    assert group.total == 4
    assert group.passed == 1
    assert group.failed == 2
    assert group.skipped == 1
    assert group.duration == pytest.approx(57.75)
    assert group.has_failure


def test_error_stats_merge_same_type(tmp_path: Path):
    """同一异常类型合并成一条，避免同一种失败刷满整页。"""
    run = _sample_run(tmp_path)
    stats = {s.error_type: s.count for s in run.error_stats()}

    assert stats == {"AssertionError": 1, "WebDriverException": 1}


def test_slowest_and_markers(tmp_path: Path):
    run = _sample_run(tmp_path)

    assert [c.name for c in run.slowest(2)] == ["加购后购物车角标数量增长", "启动淘宝后能进入首页"]
    markers = {name: (total, passed) for name, total, passed in run.marker_stats()}
    assert markers["device"] == (3, 1)
    assert markers["slow"] == (1, 0)


def test_step_record_indent_and_display_name():
    """嵌套步骤用全角空格缩进，渲染与 JSON 都取去掉缩进的名字。"""
    record = StepRecord(name="\u3000\u3000点击确认")

    assert record.level == 2
    assert record.display_name == "点击确认"


# ---------------------------------------------------------------------- #
# 收集器
# ---------------------------------------------------------------------- #
def test_step_collector_marks_failure_and_screenshots(tmp_path: Path):
    """步骤抛异常时要标记 FAILED、记录错误，并尽力截图留证。"""

    class FakeDevice:
        def screenshot(self, tag: str) -> Path:
            return _png(tmp_path, f"{tag}.png")

    case = CaseRecord(nodeid="x", name="y")
    collector = StepCollector(case)

    with pytest.raises(RuntimeError):
        with collector.step("加入购物车", screenshot_on_error=FakeDevice()):
            raise RuntimeError("boom")

    assert len(case.steps) == 1
    assert case.steps[0].status == FAILED
    assert "RuntimeError: boom" in case.steps[0].error
    assert case.steps[0].screenshot is not None
    assert case.first_failed_step is case.steps[0]


def test_step_is_noop_without_collector():
    """没绑定收集器时 ``step`` 必须透明 —— 脚本单独调用业务流不能因此报错。"""
    unbind()
    with step("随便一步") as record:
        assert record.status == PASSED


# ---------------------------------------------------------------------- #
# HTML 渲染
# ---------------------------------------------------------------------- #
def test_html_contains_all_sections(tmp_path: Path):
    run = _sample_run(tmp_path)
    html = render_html(run, tmp_path / "report.html")

    for chunk in (
        "失败概览",  # 失败原因聚合
        "运行参数",  # pytest 版本 / 退出码 / AA_WAIT_SCALE
        "按文件分组",
        "最慢用例",
        "执行步骤",
        "AA_WAIT_SCALE",
        "有用例失败",
        "group hasfail",  # 含失败的分组要标红
        "@device",  # marker chips
    ):
        assert chunk in html, f"报告缺少片段: {chunk}"


def test_html_embeds_step_screenshot_once(tmp_path: Path):
    """步骤截图内嵌到步骤下方，且不会在底部画廊重复出现。"""
    run = _sample_run(tmp_path)
    html = render_html(run, tmp_path / "report.html")

    assert "class=\"stepshot\"" in html
    assert html.count("data:image/png;base64,") == 1


def test_write_html_and_json(tmp_path: Path):
    run = _sample_run(tmp_path)
    html_path = write_html(run, tmp_path / "out" / "report_x.html")
    json_path = write_json(run, tmp_path / "out" / "report_x.json")

    assert html_path.exists() and html_path.stat().st_size > 5000
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert payload["schema"] == 2
    assert payload["exit_status_text"] == "有用例失败"
    assert payload["summary"]["pass_rate"] == pytest.approx(33.33, abs=0.01)
    assert payload["summary"]["errors"] == 1
    assert payload["groups"][0]["name"] == "test_taobao"
    assert payload["failures"][0]["count"] >= 1
    assert payload["env"]["设备型号"] == "PFGM00"
    assert payload["options"]["AA_WAIT_SCALE"] == "1"

    failed_case = next(c for c in payload["cases"] if c["status"] == FAILED)
    assert failed_case["failed_step"] == "加入购物车"
    assert failed_case["steps"][0]["screenshot"].endswith(".png")


# ---------------------------------------------------------------------- #
# 历史索引
# ---------------------------------------------------------------------- #
def test_index_lists_runs_newest_first(tmp_path: Path):
    """索引页把历次运行按时间倒序列出，并能链到对应报告。"""
    newer = _sample_run(tmp_path)
    write_json(newer, tmp_path / "report_20260101_120000.json")
    write_html(newer, tmp_path / "report_20260101_120000.html")

    older = _sample_run(tmp_path)
    older.started_at = newer.started_at - 3600
    write_json(older, tmp_path / "report_20260101_110000.json")
    write_html(older, tmp_path / "report_20260101_110000.html")

    index_path = write_index(tmp_path)
    html = index_path.read_text(encoding="utf-8")

    assert index_path.name == "index.html"
    assert html.index("report_20260101_120000.html") < html.index("report_20260101_110000.html")
    assert "历史平均" in html


def test_index_handles_empty_dir(tmp_path: Path):
    """没有任何历史记录时也要能生成索引（不能抛异常）。"""
    html = render_index([])

    assert "还没有历史运行记录" in html


def test_human_duration_formats():
    assert human_duration(0.42) == "420ms"
    assert human_duration(4.21) == "4.2s"
    assert human_duration(83) == "1m23s"
