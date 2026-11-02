"""设备无关的判定逻辑：CTA 白名单、页面状态映射、票档面板状态、售罄识别。

这些判断直接决定「点不点、点哪里」，判错的后果是抢错流程或误报售罄，
所以每一条都用真机结构摘录（tests/fixtures/damai/）钉死。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from damai.constants import BLOCKED_TEXTS, SAFE_TEXTS
from damai.guard import BuyButtonGuard, is_safe_to_click
from damai.probe import (
    PageProbe,
    PageState,
    detect_price_panel_state,
    is_buy_button_sold_out,
    state_from_activity,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "damai"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------- #
# CTA 白名单
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("text", sorted(SAFE_TEXTS))
def test_safe_texts_are_clickable(text: str):
    assert is_safe_to_click(text) is True


@pytest.mark.parametrize("text", sorted(BLOCKED_TEXTS))
def test_blocked_texts_are_rejected(text: str):
    assert is_safe_to_click(text) is False


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "   ",
        "  立即购票  ",  # 带空格 → 不算精确相等，必须拒绝
        "立即购票进行中",
        "未知文案",
        "立即购票\n",
    ],
)
def test_unknown_or_padded_texts_rejected(text: str | None):
    """锁定「精确相等」语义：不做包含匹配、不自动 strip。"""
    assert is_safe_to_click(text) is False


def test_safe_and_blocked_sets_do_not_overlap():
    assert not SAFE_TEXTS & BLOCKED_TEXTS


def test_reservation_text_is_blocked_not_safe():
    """「预约抢票」是大麦开售前的同一位置按钮，点错就进预约流程。"""
    assert "预约抢票" in BLOCKED_TEXTS
    assert is_safe_to_click("预约抢票") is False


# ---------------------------------------------------------------------- #
# Activity → 状态
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("activity", "expected"),
    [
        ("cn.damai/.project.detail.ProjectDetailActivity", PageState.DETAIL_PAGE.value),
        ("cn.damai/.trade.NcovSkuActivity", PageState.SKU_PAGE.value),
        ("cn.damai/.homepage.MainActivity", PageState.HOMEPAGE.value),
        ("cn.damai/.search.SearchActivity", PageState.SEARCH_PAGE.value),
    ],
)
def test_state_from_activity(activity: str, expected: str):
    assert state_from_activity(activity) == expected


def test_state_from_activity_splash_counts_as_homepage():
    """SplashMainActivity 含 MainActivity 子串 → 会被认成首页。

    这是刻意的：启动闪屏阶段不该去点任何东西，交给上层 wait_for_home_ready
    去等真实内容加载完。
    """
    assert state_from_activity("cn.damai/.launcher.splash.SplashMainActivity") == (
        PageState.HOMEPAGE.value
    )


@pytest.mark.parametrize("activity", ["", "com.android.settings/.Settings", "cn.damai/.webview.WebActivity"])
def test_state_from_activity_unknown(activity: str):
    assert state_from_activity(activity) is None


def test_page_state_str_comparison():
    assert PageState.SKU_PAGE == "sku_page"
    assert PageState("detail_page") is PageState.DETAIL_PAGE


# ---------------------------------------------------------------------- #
# 售罄识别
# ---------------------------------------------------------------------- #
def test_sold_out_detected_from_visible_button_text():
    """v8.x：btn_buy_view 有文案，直接读文案判断。"""
    assert is_buy_button_sold_out(_fixture("detail_v8_soldout.xml")) is True


def test_canvas_cta_is_not_reported_sold_out():
    """≥9.0.2x：Canvas 自绘按钮读不到文案 → 判不了，不能误报售罄。"""
    assert is_buy_button_sold_out(_fixture("detail_canvas_empty.xml")) is False


def test_empty_ancestor_container_does_not_short_circuit():
    """回归检查：购买栏容器是 btn_buy_view 的**祖先**且出现在文档序前面。

    若实现「首个命中容器就返回」，v8.x 带文案的按钮会被空文案容器短路，
    漏检缺货 —— 这正是 detail_v8_soldout.xml 的结构。
    """
    xml = _fixture("detail_v8_soldout.xml")
    # 先剥注释再取位置：fixture 顶部的说明文字里也提到了容器 ID，
    # 直接 xml.index() 会先命中注释，断言就变成"注释在按钮之前"，测不到本意。
    body = re.sub(r"<!--.*?-->", "", xml, flags=re.S)
    container_pos = body.index('resource-id="cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"')
    button_pos = body.index('resource-id="cn.damai:id/btn_buy_view"')
    assert container_pos < button_pos, "fixture 必须让容器出现在按钮之前，否则测不到这个 bug"
    assert is_buy_button_sold_out(xml) is True


def test_no_relevant_nodes_means_not_sold_out():
    xml = '<hierarchy><node resource-id="cn.damai:id/title_tv" text="许嵩演唱会" /></hierarchy>'
    assert is_buy_button_sold_out(xml) is False


@pytest.mark.parametrize("xml", [None, "", "   ", "<not-xml", "<a><b></a>"])
def test_broken_xml_never_raises(xml: str | None):
    assert is_buy_button_sold_out(xml) is False


def test_other_tier_sold_out_text_does_not_count():
    """别的票档写「缺货登记」不代表当前按钮不可点 —— 只看按钮节点。"""
    xml = (
        '<hierarchy><node class="android.widget.TextView" resource-id="cn.damai:id/tv_price_name"'
        ' text="看台618 缺货登记" bounds="[0,0][100,100]" /></hierarchy>'
    )
    assert is_buy_button_sold_out(xml) is False


# ---------------------------------------------------------------------- #
# 票档面板状态
# ---------------------------------------------------------------------- #
class _FakeSelector:
    def __init__(self, exists: bool) -> None:
        self.exists = exists


def _fake_device(present: set[str]) -> object:
    """构造只认 resourceId / text / textContains 关键字的桩设备。"""

    def device(**kwargs: str) -> _FakeSelector:
        for key in ("resourceId", "text", "textContains"):
            value = kwargs.get(key)
            if value is not None:
                return _FakeSelector(value in present)
        return _FakeSelector(False)

    return device


def test_price_panel_loading_wins_over_everything():
    device = _fake_device(
        {
            "cn.damai:id/sku_loading",
            "cn.damai:id/loading_progress",
            "已售罄",
            "cn.damai:id/layout_price",
        }
    )
    assert detect_price_panel_state(device) == "loading"


def test_price_panel_sold_out_before_ready():
    """面板既显示售罄又有容器时判定为售罄 —— 空卡片不能点。"""
    device = _fake_device({"已售罄", "cn.damai:id/layout_price"})
    assert detect_price_panel_state(device) == "sold_out"


def test_price_panel_ready():
    device = _fake_device({"cn.damai:id/project_detail_perform_price_flowlayout"})
    assert detect_price_panel_state(device) == "ready"


def test_price_panel_unknown():
    assert detect_price_panel_state(_fake_device(set())) == "unknown"


def test_price_panel_survives_device_errors():
    def broken(**_: str) -> _FakeSelector:
        raise RuntimeError("device gone")

    assert detect_price_panel_state(broken) == "unknown"


# ---------------------------------------------------------------------- #
# PageProbe 缓存与强制状态
# ---------------------------------------------------------------------- #
class _CountingDevice:
    """统计 app_current 调用次数的桩设备。"""

    def __init__(self, activity: str) -> None:
        self.activity = activity
        self.calls = 0

    def app_current(self) -> dict[str, str]:
        self.calls += 1
        return {"activity": self.activity}

    def __call__(self, **_: str) -> _FakeSelector:
        return _FakeSelector(False)


def test_probe_caches_within_ttl():
    device = _CountingDevice("cn.damai/.homepage.MainActivity")
    probe = PageProbe(device, cache_ttl_s=10.0)
    probe.probe(fast=True)
    probe.probe(fast=True)
    probe.probe(fast=True)
    assert device.calls == 1


def test_probe_invalidate_forces_refresh():
    device = _CountingDevice("cn.damai/.homepage.MainActivity")
    probe = PageProbe(device, cache_ttl_s=10.0)
    probe.probe(fast=True)
    probe.invalidate()
    probe.probe(fast=True)
    assert device.calls == 2


def test_probe_force_state_skips_device():
    device = _CountingDevice("cn.damai/.homepage.MainActivity")
    probe = PageProbe(device, cache_ttl_s=0.0)
    probe.force_state(PageState.ORDER_CONFIRM_PAGE)
    assert probe.probe()["state"] == PageState.ORDER_CONFIRM_PAGE.value
    assert device.calls == 0
    probe.force_state(None)
    probe.probe(fast=True)
    assert device.calls == 1


def test_probe_current_page_alias_still_works():
    """移植过来的调用点用的是 probe_current_page，别名不能丢。"""
    probe = PageProbe(_CountingDevice("cn.damai/.trade.NcovSkuActivity"), cache_ttl_s=0.0)
    assert probe.probe_current_page(fast=True)["state"] == PageState.SKU_PAGE.value


# ---------------------------------------------------------------------- #
# Guard 元素读取
# ---------------------------------------------------------------------- #
class _FakeButton:
    def __init__(self, text: str, bounds: dict[str, int] | None = None) -> None:
        self.exists = True
        self._text = text
        self.info = {"bounds": bounds} if bounds else {}

    def get_text(self) -> str:
        return self._text


class _FakeGuardDevice:
    def __init__(self, button: _FakeButton | None) -> None:
        self._button = button

    def __call__(self, **_: str) -> _FakeButton:
        if self._button is None:
            raise RuntimeError("not found")
        return self._button


def test_guard_reads_cta_text():
    guard = BuyButtonGuard(_FakeGuardDevice(_FakeButton("立即购票")))
    assert guard.read_cta_text() == "立即购票"
    assert guard.is_safe_to_click("立即购票") is True


def test_guard_returns_none_when_button_absent():
    guard = BuyButtonGuard(_FakeGuardDevice(None))
    assert guard.read_cta_text() is None
    assert guard.cta_center() is None


def test_guard_computes_cta_center():
    bounds = {"left": 0, "top": 2250, "right": 1080, "bottom": 2400}
    guard = BuyButtonGuard(_FakeGuardDevice(_FakeButton("", bounds)))
    assert guard.cta_center() == (540, 2325)
