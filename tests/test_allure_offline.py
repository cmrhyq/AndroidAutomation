"""报告基建的离线用例：不碰设备、不建 Appium 会话，秒级回归。

为什么要有这组用例
------------------
报告是测试的基础设施 —— 它坏掉时最容易被误判成「被测应用有问题」。
Allure 的 HTML 由官方 CLI 生成，我们真正该守住的是**喂给它的原料**：

- ``environment.properties`` 的编码转义（中文关键词写错就是一堆问号）
- ``categories.json`` 的结构（错一个字整个分类面板都不生效）
- ``collector`` 的 no-op 语义与上下文聚合（业务流在脚本里单独跑不能崩）

全部标记 ``unit``，CI 里可单独快跑::

    .venv/Scripts/python.exe -m pytest tests/test_allure_offline.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.report import collector
from core.report.environment import collect, write_properties

pytestmark = pytest.mark.unit

#: 报告模块所在目录（categories.json 等静态资源）
REPORT_DIR = Path(__file__).resolve().parent.parent / "core" / "report"


@pytest.fixture(autouse=True)
def _restore_collector():
    """每条用例跑完把埋点开关恢复原状 —— 否则会关掉同进程里后续用例的步骤记录。"""
    was_enabled = collector.is_enabled()
    collector.clear_notes()
    yield
    collector.clear_notes()
    if was_enabled:
        collector.enable()
    else:
        collector.disable()


# ---------------------------------------------------------------------- #
# 环境信息
# ---------------------------------------------------------------------- #
def test_write_properties_escapes_non_ascii(tmp_path: Path):
    """中文必须转成 \\uXXXX —— Allure 用 Java Properties 解析，直接写会乱码。"""
    path = write_properties(
        tmp_path / "environment.properties",
        {"Device.Model": "PFGM00", "Run.Keyword": "蓝牙耳机"},
    )

    body = path.read_text(encoding="utf-8")
    assert "Device.Model=PFGM00" in body
    assert "蓝牙耳机" not in body
    assert "\\u84dd\\u7259\\u8033\\u673a" in body


def test_write_properties_follows_field_order(tmp_path: Path):
    """环境表按固定字段顺序展示，别被 dict 插入顺序打乱。"""
    path = write_properties(
        tmp_path / "environment.properties",
        {"Host": "pc", "App.Package": "com.taobao.taobao", "Device.Model": "PFGM00"},
    )
    lines = [line.split("=")[0] for line in path.read_text(encoding="utf-8").splitlines() if line]

    assert lines.index("Device.Model") < lines.index("App.Package") < lines.index("Host")


def test_write_properties_skips_empty_values(tmp_path: Path):
    path = write_properties(tmp_path / "environment.properties", {"Device.Model": "", "Host": "pc"})

    body = path.read_text(encoding="utf-8")
    assert "Device.Model" not in body
    assert "Host=pc" in body


def test_collect_without_device_still_reports_basics():
    """没有设备也要能采集（CI 里没插手机不能崩）。"""
    info = collect()

    assert info["Python.Version"]
    assert info["Host"]
    assert all(isinstance(v, str) for v in info.values())


# ---------------------------------------------------------------------- #
# 分类规则
# ---------------------------------------------------------------------- #
def test_categories_json_is_valid():
    payload = json.loads((REPORT_DIR / "categories.json").read_text(encoding="utf-8"))

    assert len(payload) >= 3
    for item in payload:
        assert item["name"], "分类必须有名字"
        assert item["matchedStatuses"], f"{item['name']} 缺少 matchedStatuses"
        assert item["messageRegex"].startswith(".*"), "Java regex 是全匹配，必须用 .* 包住"


def test_categories_put_device_failures_first():
    """设备异常必须排在断言之前 —— Allure 只取第一条命中的分类。"""
    payload = json.loads((REPORT_DIR / "categories.json").read_text(encoding="utf-8"))
    names = [item["name"] for item in payload]

    assert "设备" in names[0], "会话类问题最容易被误判成业务失败，必须优先归类"
    assert any("选择器" in name for name in names)


# ---------------------------------------------------------------------- #
# 埋点
# ---------------------------------------------------------------------- #
def test_step_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """没挂插件时（脚本里单独跑业务流）step 必须透明，不能去碰 Allure。"""

    def _boom(*args, **kwargs):
        raise AssertionError("disabled 状态不该调用 allure.step")

    monkeypatch.setattr(collector.allure, "step", _boom)
    collector.disable()

    with collector.step("随便一步", screenshot_on_error=object()):
        pass

    assert not collector.is_enabled()


def test_step_reraises_business_error(monkeypatch: pytest.MonkeyPatch):
    """步骤里的异常要原样抛出去，不能被埋点吞掉；失败截图失败也不得掩盖原错误。"""

    class BrokenDevice:
        def screenshot(self, tag: str):
            raise OSError("driver 已断开")

    collector.enable()
    with pytest.raises(RuntimeError, match="boom"):
        with collector.step("加入购物车", screenshot_on_error=BrokenDevice()):
            raise RuntimeError("boom")


def test_note_collects_and_drains():
    """note 攒到用例结束一次性出表，drain 后要清空（避免串到下一条用例）。"""
    collector.enable()

    collector.note(keyword="蓝牙耳机", 角标="0 → 1")
    collector.note(空值="", 新增="x")

    notes = collector.drain_notes()
    assert notes["keyword"] == "蓝牙耳机"
    assert notes["角标"] == "0 → 1"
    assert "空值" not in notes, "空值不该出现在上下文表里"
    assert collector.drain_notes() == {}


def test_note_ignored_when_disabled():
    collector.disable()

    collector.note(keyword="蓝牙耳机")

    assert collector.drain_notes() == {}


def test_attach_helpers_tolerate_missing_files(tmp_path: Path):
    """附件失败绝不能影响用例结论：路径不存在时静默跳过。"""
    collector.enable()

    collector.attach_image(tmp_path / "不存在的截图.png")
    collector.attach_image(None)
    collector.attach_text("", "空文本")
    collector.attach_table({})


def test_attach_table_aligns_keys_by_display_width(monkeypatch: pytest.MonkeyPatch):
    """上下文表用等宽文本展示：中文占两列，「角标」要和「keyword」对齐同一列。"""
    captured: dict[str, str] = {}

    def _capture(body: str, name: str) -> None:
        captured[name] = body

    monkeypatch.setattr(collector, "attach_text", _capture)
    collector.attach_table({"keyword": "蓝牙耳机", "角标": "0 → 1"}, name="业务上下文")

    first, second = captured["业务上下文"].splitlines()
    assert first.startswith("keyword : ")
    assert second.startswith("角标    : "), "全角字符要按 2 列宽补齐"
    # 冒号必须落在同一显示列（字符索引不同，是因为中文占两列）
    assert collector._display_width(first.split(":")[0]) == collector._display_width(second.split(":")[0])
