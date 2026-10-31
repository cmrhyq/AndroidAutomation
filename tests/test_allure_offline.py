"""报告基建的离线用例：不碰设备、不建 Appium 会话，秒级回归。

覆盖 environment.properties 编码、categories.json 结构与 collector 的 no-op 语义。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.report import AllureHelper, allure_step, collector
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
        pattern = item["messageRegex"]
        # Allure 用 Java 正则做全匹配：两端必须 .* 兜住，且要 (?s) 让 . 跨行，
        # 否则多行的异常 message 一律匹配不上。
        assert pattern.startswith("(?s)"), f"{item['name']} 需要 (?s) 前缀（. 默认不跨行，多行异常会漏匹配）"
        body = pattern[len("(?s)") :]
        assert body.startswith(".*") and body.endswith(".*"), f"{item['name']} 的 messageRegex 两端必须用 .* 包住"


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


# ---------------------------------------------------------------------- #
# 附件形态（截图 / JSON / HTML / 文件）
# ---------------------------------------------------------------------- #
class _AttachSpy:
    """替身：记录 ``allure.attach`` 与 ``allure.attach.file`` 的调用，不真的落盘。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, body, name=None, attachment_type=None, extension=None):
        self.calls.append({"body": body, "name": name, "type": attachment_type, "ext": extension})

    def file(self, source, name=None, attachment_type=None, extension=None):
        self.calls.append({"body": source, "name": name, "type": attachment_type, "ext": extension})

    @property
    def last(self) -> dict[str, object]:
        assert self.calls, "期望至少挂了一个附件"
        return self.calls[-1]


@pytest.fixture
def attach_spy(monkeypatch: pytest.MonkeyPatch) -> _AttachSpy:
    spy = _AttachSpy()
    monkeypatch.setattr(collector.allure, "attach", spy)
    return spy


class _FakeDevice:
    """最小设备替身：只实现 ``screenshot``，验证截图确实走框架既有方法。"""

    def __init__(self, target: Path, fail: bool = False) -> None:
        self.target = target
        self.fail = fail
        self.tags: list[str] = []

    def screenshot(self, tag: str = "screenshot") -> str:
        self.tags.append(tag)
        if self.fail:
            raise OSError("instrumentation process is not running")
        path = self.target / f"{tag}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return str(path)


def test_attach_json_keeps_chinese_readable(attach_spy: _AttachSpy):
    """JSON 附件要能直接看懂：缩进 + 中文不转义，类型标成 application/json。"""
    ok = collector.attach_json({"关键词": "蓝牙耳机", "卡片数": 20}, name="搜索结果")

    assert ok is True
    assert attach_spy.last["type"] is collector.allure.attachment_type.JSON
    body = attach_spy.last["body"]
    assert "蓝牙耳机" in body, "ensure_ascii=False 才不会被转成 \\uXXXX"
    assert json.loads(body) == {"关键词": "蓝牙耳机", "卡片数": 20}
    assert "\n  " in body, "要缩进，单行 JSON 在报告里没法看"


def test_attach_json_degrades_on_circular_reference(attach_spy: _AttachSpy):
    """序列化失败必须降级成 False，不能把用例带崩。"""
    loop: dict[str, object] = {}
    loop["self"] = loop

    assert collector.attach_json(loop) is False
    assert attach_spy.calls == []


def test_attach_file_infers_type_from_suffix(attach_spy: _AttachSpy, tmp_path: Path):
    """后缀决定报告里的展示方式：.json 高亮、未知后缀退回纯文本、缺文件返回 False。"""
    payload = tmp_path / "result.json"
    payload.write_text('{"ok": true}', encoding="utf-8")
    unknown = tmp_path / "dump.dmp"
    unknown.write_bytes(b"\x00\x01")

    assert collector.attach_file(payload) is True
    assert attach_spy.last["type"] is collector.allure.attachment_type.JSON
    assert attach_spy.last["name"] == "result.json", "不传 name 时用文件名"

    assert collector.attach_file(unknown, name="原始 dump") is True
    assert attach_spy.last["type"] is collector.allure.attachment_type.TEXT

    assert collector.attach_file(tmp_path / "不存在.txt") is False


def test_attach_image_accepts_bytes_and_device(attach_spy: _AttachSpy, tmp_path: Path):
    """截图三种形态都要能挂：字节流、本地文件、设备对象（走 dev.screenshot）。"""
    assert collector.attach_image(b"\x89PNG\r\n\x1a\n", name="字节流截图") is True
    assert attach_spy.last["type"] is collector.allure.attachment_type.PNG
    assert attach_spy.last["ext"] == "png"

    dev = _FakeDevice(tmp_path)
    assert collector.attach_image(dev, name="金币中心") is True
    assert dev.tags == ["金币中心"], "设备截图必须复用 AndroidDevice.screenshot，不另造轮子"
    assert str(tmp_path / "金币中心.png") in str(attach_spy.last["body"])

    assert collector.attach_image(_FakeDevice(tmp_path, fail=True)) is False, "截不到图只能返回 False"
    assert collector.attach_image(None) is False


def test_helper_attach_screenshot_delegates_to_collector(attach_spy: _AttachSpy, tmp_path: Path, monkeypatch):
    """AllureHelper 只是签名适配层：实现必须落在 collector（避免两套逻辑分叉）。"""
    seen: list[tuple[object, str]] = []
    monkeypatch.setattr(
        collector, "attach_image", lambda source, name="截图": seen.append((source, name)) or True
    )
    dev = _FakeDevice(tmp_path)

    assert AllureHelper.attach_screenshot(dev, "登录页") is True
    assert seen == [(dev, "登录页")]


def test_helper_severity_validates_value():
    """Allure 对写错的严重度是静默忽略的，这里必须挡住，否则报告少一档颜色还不知道。"""
    assert AllureHelper.add_severity("critical") is True
    assert AllureHelper.add_severity(collector.allure.severity_level.BLOCKER) is True
    assert AllureHelper.add_severity("urgent") is False


def test_helper_step_reraises_and_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch):
    """helper.step 与 collector.step 同源：未启用时透明，启用时异常照常上抛。"""
    collector.disable()
    with allure_step("随便一步"):
        pass

    collector.enable()

    class BrokenDevice:
        def screenshot(self, tag: str):
            raise OSError("driver 已断开")

    with pytest.raises(RuntimeError, match="boom"):
        with AllureHelper.step("加入购物车", screenshot_on_error=BrokenDevice()):
            raise RuntimeError("boom")
