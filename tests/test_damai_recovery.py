"""失败取证：三件套落盘、截图兜底、取证本身绝不抛异常。

取证函数总在 ``except`` 块里被调用，它一抛就会把原始异常盖掉，现场和病因
一起丢。所以这里测的重点不是「成功时写对了」，而是**各种失败路径下仍能落盘**。

（源项目另有一个 RecoveryHelper 读错键的 bug —— ``probe["page"]`` vs ``"state"``，
导致每轮白按 8 次返回键退到首页。重写后恢复逻辑已并入页面状态判定，
不再需要那个对象，对应回归护栏在 ``test_damai_logic.py`` 的状态映射用例里。）
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from core.android import evidence
from core.android.evidence import capture_failure

pytestmark = pytest.mark.unit


class FakeDriver:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok

    def save_screenshot(self, path: str) -> bool:
        if not self.ok:
            raise RuntimeError("驱动截图失败：instrumentation process is not running")
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n fake")
        return True


class FakeConfig:
    udid = "10.20.0.209:39911"
    app_package = "cn.damai"


class FakeDevice:
    def __init__(self, xml: str = "<hierarchy />", *, shot_ok: bool = True) -> None:
        self._xml = xml
        self.driver = FakeDriver(ok=shot_ok)
        self.cfg = FakeConfig()

    def page_source(self) -> str:
        return self._xml

    @property
    def current_activity(self) -> str:
        return "cn.damai/.project.detail.ProjectDetailActivity"


@pytest.fixture
def no_adb(monkeypatch: pytest.MonkeyPatch) -> None:
    """块掉一切 adb 调用：单测既不该依赖真机，也不该因为没 adb 而失真。"""
    monkeypatch.setattr(evidence, "app_version", lambda package, serial="": "9.0.37")
    monkeypatch.setattr("core.android.adb.find_adb", lambda: "")


def test_capture_writes_all_three(tmp_path: Path, no_adb: None):
    written = capture_failure(FakeDevice("<hierarchy><node text='停在确认页'/></hierarchy>"), "run", root=tmp_path)

    assert set(written) == {"xml", "screenshot", "meta"}
    assert Path(written["xml"]).read_text(encoding="utf-8").count("停在确认页") == 1
    assert Path(written["screenshot"]).stat().st_size > 0

    meta = json.loads(Path(written["meta"]).read_text(encoding="utf-8"))
    assert meta["scene"] == "run"
    assert meta["device"] == "10.20.0.209:39911"
    assert meta["app_version"] == "9.0.37"
    assert meta["xml_path"] == written["xml"]


def test_capture_marks_screenshot_failure(tmp_path: Path, no_adb: None):
    """驱动截图挂了要标出来，而不是让整次取证失败。"""
    written = capture_failure(FakeDevice(shot_ok=False), "run", root=tmp_path)

    assert "screenshot" not in written
    assert {"xml", "meta"} <= set(written)
    meta = json.loads(Path(written["meta"]).read_text(encoding="utf-8"))
    assert meta["screenshot_failed"] is True


def test_capture_survives_adb_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """adb 不可用时取证仍要落盘，只是版本号标 unknown。"""

    def _boom(package: Any, serial: Any = "") -> str:
        raise FileNotFoundError("adb 不在 PATH 里")

    monkeypatch.setattr(evidence, "app_version", _boom)
    monkeypatch.setattr("core.android.adb.find_adb", lambda: "")

    written = capture_failure(FakeDevice(), "adb_dead", root=tmp_path)
    assert "meta" in written
    assert "xml" in written


def test_capture_records_exception_detail(tmp_path: Path, no_adb: None):
    """异常类型、消息、栈要进元数据 —— 事后复盘全靠它。"""
    error = RuntimeError("票档面板没出现")
    written = capture_failure(FakeDevice(), "submit_unverified", error=error, root=tmp_path)
    meta = json.loads(Path(written["meta"]).read_text(encoding="utf-8"))

    assert meta["error_type"] == "RuntimeError"
    assert "票档面板没出现" in meta["error"]
    assert "RuntimeError" in meta["traceback"]
    assert "票档面板没出现" in meta["traceback"]


def test_capture_merges_extra_fields(tmp_path: Path, no_adb: None):
    written = capture_failure(
        FakeDevice(), "attempt", extra={"attempt": 2, "mode": "submit"}, root=tmp_path
    )
    meta = json.loads(Path(written["meta"]).read_text(encoding="utf-8"))
    assert meta["extra"] == {"attempt": 2, "mode": "submit"}


def test_capture_filename_carries_scene(tmp_path: Path, no_adb: None):
    """文件名带场景标签，几十份取证摆在一起也能一眼分辨。"""
    written = capture_failure(FakeDevice(), "submit/unverified again", root=tmp_path)
    assert "/" not in Path(written["meta"]).name
    assert "submit_unverified_again" in Path(written["meta"]).name


def test_capture_empty_xml_still_writes_meta(tmp_path: Path, no_adb: None):
    """dump 失败（返回空）时只缺 xml，元数据照写。"""
    written = capture_failure(FakeDevice(""), "dump_failed", root=tmp_path)
    assert "xml" not in written
    assert "meta" in written
