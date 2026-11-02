"""恢复策略：分层返回详情页、导航起点恢复、失败取证。

第一个用例是**回归护栏**：源项目 ``RecoveryHelper`` 读
``probe["page"]``，而探测结果里键名是 ``"state"``，导致「已经在目标页」
这一层永远不命中，每轮都要白按 8 次返回键，从详情页一路退到首页再重新搜。
在抢票重试链路里这是每轮十几秒的纯浪费，所以这里把它钉死。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from damai.config import GrabConfig
from damai.mode import GrabMode
from damai.recovery import (
    MAX_BACK_STEPS,
    RecoveryHelper,
    capture_failure_artifacts,
    exit_non_target_event_context,
    recover_to_detail_page_for_local_retry,
    recover_to_navigation_start,
)

pytestmark = pytest.mark.unit


def _config(**overrides: Any) -> GrabConfig:
    payload: dict[str, Any] = {
        "keyword": "许嵩 演唱会",
        "users": ("黄昱桥",),
        "city": "福州",
        "date": "2026-12-26",
        "price": "看台318",
        "mode": GrabMode.VALIDATION,
    }
    payload.update(overrides)
    return GrabConfig(**payload)


class FakeProbe:
    """按序列返回状态；序列剩一个时一直返回它。"""

    def __init__(self, states: list[str]) -> None:
        self._states = list(states)
        self.invalidations = 0

    def probe(self, fast: bool = False) -> dict[str, Any]:
        state = self._states[0] if len(self._states) == 1 else self._states.pop(0)
        return {"state": state}

    def invalidate(self) -> None:
        self.invalidations += 1


class FakeDevice:
    def __init__(self, xml: str = "") -> None:
        self.xml = xml
        self.screenshots = 0
        self.app_starts = 0

    def dump_hierarchy(self) -> str:
        return self.xml

    def screenshot(self) -> None:
        self.screenshots += 1
        return None

    def app_start(self, package: str, stop: bool = False) -> None:
        self.app_starts += 1


class FakeHost:
    def __init__(self, states: list[str], xml: str = "") -> None:
        self.probe = FakeProbe(states)
        self.d = FakeDevice(xml)
        self.config = _config()
        self.backs = 0

    def probe_current_page(self, fast: bool = False) -> dict[str, Any]:
        return self.probe.probe(fast=fast)

    def _press_keycode_safe(self, keycode: int, context: str = "") -> bool:
        self.backs += 1
        return True

    def dismiss_startup_popups(self) -> bool:
        return False


class FakeNavigator:
    def __init__(self, matches: bool = True) -> None:
        self.matches = matches
        self.navigate_calls = 0

    def navigate_to_target_event(self, initial_probe: Any = None) -> bool:
        self.navigate_calls += 1
        return True

    def current_page_matches_target(
        self, page_probe: dict[str, Any], clicked_title: str | None = None
    ) -> bool:
        return self.matches


# ---------------------------------------------------------------------- #
# 回归护栏
# ---------------------------------------------------------------------- #
def test_already_on_target_page_presses_back_zero_times():
    """已经在详情页时必须立刻返回，一次返回键都不按。

    源项目这里读的是 ``probe["page"]``（键不存在，永远是 unknown），
    于是这一层从不命中，白按 8 次返回键退到首页再重新搜索。
    """
    host = FakeHost(["detail_page"])
    navigator = FakeNavigator()

    result = RecoveryHelper(host, navigator).recover_to_detail_page()

    assert result["state"] == "detail_page"
    assert host.backs == 0, "已经在目标页却按了返回键 —— 说明读错了探测结果的键名"
    assert navigator.navigate_calls == 0


def test_sku_page_also_counts_as_target():
    host = FakeHost(["sku_page"])
    result = RecoveryHelper(host, FakeNavigator()).recover_to_detail_page()
    assert result["state"] == "sku_page"
    assert host.backs == 0


# ---------------------------------------------------------------------- #
# 分层策略
# ---------------------------------------------------------------------- #
def test_deep_back_reaches_target_and_stops():
    """逐级返回，一到达目标页就停，不多按。"""
    host = FakeHost(["order_list", "order_detail", "detail_page"])
    host.probe._states = ["order_list", "order_detail", "detail_page"]
    result = RecoveryHelper(host, FakeNavigator()).recover_to_detail_page()
    assert result["state"] == "detail_page"
    assert host.backs == 2


def test_homepage_midway_switches_to_forward_navigation():
    host = FakeHost(["order_list", "homepage", "detail_page"])
    navigator = FakeNavigator()
    result = RecoveryHelper(host, navigator).recover_to_detail_page()
    assert result["state"] == "detail_page"
    assert navigator.navigate_calls == 1
    assert host.backs == 1


def test_back_exhaustion_falls_through_to_forward_nav():
    """一路都是无关页面时不要无限退下去，退满上限就转正向导航。"""
    host = FakeHost(["settings"])
    navigator = FakeNavigator()
    result = RecoveryHelper(host, navigator).recover_to_detail_page()
    assert host.backs == MAX_BACK_STEPS
    assert navigator.navigate_calls == 1
    assert result["state"] == "settings"


def test_all_strategies_failed_returns_last_state_without_raising():
    host = FakeHost(["settings"])
    result = RecoveryHelper(host, FakeNavigator()).recover_to_detail_page()
    assert result["state"] == "settings"


# ---------------------------------------------------------------------- #
# 导航起点与上下文退出
# ---------------------------------------------------------------------- #
def test_recover_to_navigation_start_short_circuits_on_navigable():
    host = FakeHost(["homepage"])
    result = recover_to_navigation_start(host, {"state": "homepage"})
    assert result["state"] == "homepage"
    assert host.backs == 0


def test_recover_to_navigation_start_backs_out_then_restarts_app():
    """深层页面退不出来时重启 App 是唯一可靠兜底。"""
    host = FakeHost(["cart"])
    result = recover_to_navigation_start(host, {"state": "cart"}, max_back_steps=2)
    assert host.backs == 2
    assert host.d.app_starts == 1
    assert result["state"] == "cart"


def test_exit_non_target_event_context_stops_when_leaving_target_states():
    host = FakeHost(["search_page"])
    result = exit_non_target_event_context(host, {"state": "detail_page"})
    assert result["state"] == "search_page"
    assert host.backs == 1


def test_local_retry_keeps_current_page_when_it_matches():
    host = FakeHost(["detail_page"])
    navigator = FakeNavigator(matches=True)
    result = recover_to_detail_page_for_local_retry(host, navigator)
    assert result["state"] == "detail_page"
    assert host.backs == 0


def test_local_retry_navigates_forward_from_homepage():
    host = FakeHost(["homepage"])
    navigator = FakeNavigator(matches=False)
    recover_to_detail_page_for_local_retry(host, navigator, max_back_steps=1)
    assert navigator.navigate_calls == 1


# ---------------------------------------------------------------------- #
# 失败取证
# ---------------------------------------------------------------------- #
@pytest.fixture
def no_adb(monkeypatch: pytest.MonkeyPatch) -> None:
    """取证会调 adb 读大麦版本——单测里必须挡掉，否则既慢又依赖真机。"""
    monkeypatch.setattr("damai.recovery.detect_app_version", lambda serial=None: "9.0.37")


def test_capture_failure_artifacts_writes_all_three(tmp_path: Path, no_adb: None):
    host = FakeHost(["detail_page"], xml="<hierarchy><node text='停在确认页' /></hierarchy>")
    written = capture_failure_artifacts(host, "run_ticket", error=RuntimeError("boom"), root=tmp_path)

    assert written["xml"] and Path(written["xml"]).exists()
    assert "停在确认页" in Path(written["xml"]).read_text(encoding="utf-8")
    assert written["json"] and Path(written["json"]).exists()

    metadata = __import__("json").loads(Path(written["json"]).read_text(encoding="utf-8"))
    assert metadata["scene"] == "run_ticket"
    assert metadata["damai_version"] == "9.0.37"
    assert metadata["error_type"] == "RuntimeError"
    assert metadata["error_message"] == "boom"


def test_capture_failure_artifacts_marks_screenshot_failure(tmp_path: Path, no_adb: None):
    """设备截图返回 None 时要如实标注，而不是假装有截图。"""
    host = FakeHost(["detail_page"], xml="<hierarchy />")
    written = capture_failure_artifacts(host, "shot_fail", root=tmp_path)
    assert written["png"] is None
    metadata = __import__("json").loads(Path(written["json"]).read_text(encoding="utf-8"))
    assert metadata["screenshot_failed"] is True


def test_capture_survives_adb_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """adb 挂掉时取证仍要落盘，只是版本号标 unknown。"""

    def _boom(serial: Any = None) -> str:
        raise FileNotFoundError("adb 不在 PATH 里")

    monkeypatch.setattr("damai.recovery.detect_app_version", _boom)
    host = FakeHost(["detail_page"], xml="<hierarchy />")
    written = capture_failure_artifacts(host, "adb_dead", root=tmp_path)
    metadata = __import__("json").loads(Path(written["json"]).read_text(encoding="utf-8"))
    assert metadata["damai_version"] == "unknown"
    assert metadata["xml_path"]
