"""大麦链路纯逻辑单测：CTA 白名单、页面状态映射、UI 树解析、票档面板判定。

这些函数都是「吃字符串/XML，吐布尔或结构」的纯函数，不需要设备 ——
抢票链路上最容易出错、也最该被锁死的正是它们：点错按钮、认错页面、
把预售票档当成可买，任何一个都会让整轮报废。

夹具全部取自真机结构摘录（tests/fixtures/damai/）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.android import ui_tree
from data.constant.damai import BLOCKED_TEXTS, SAFE_TEXTS
from data.entity.damai import PageState, state_from_activity
from pages.components.damai_buy_bar import is_safe_to_click
from pages.components.damai_price_panel import (
    collapse_card_texts,
    detect_panel_state,
    extract_price_digits,
    parse_price_cards,
    price_matches,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "damai"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# CTA 白名单
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", sorted(SAFE_TEXTS))
def test_every_safe_text_is_clickable(text: str):
    assert is_safe_to_click(text) is True


@pytest.mark.parametrize("text", sorted(BLOCKED_TEXTS))
def test_every_blocked_text_is_rejected(text: str):
    """预约/预售文案一个都不能放过 —— 点了就进完全不同的流程。"""
    assert is_safe_to_click(text) is False


@pytest.mark.parametrize("text", [None, "", "   ", "立即购买2", "未知按钮", "立即购票吧"])
def test_unknown_or_empty_text_is_rejected(text):
    """fail-closed：只有精确等于白名单才放行。"""
    assert is_safe_to_click(text) is False


def test_blocked_text_containing_safe_word_is_still_rejected():
    """「预约抢票」里含「抢票」，但语义完全不同，必须拒绝。"""
    assert "抢票" in "预约抢票"
    assert is_safe_to_click("预约抢票") is False


# --------------------------------------------------------------------------- #
# 页面状态
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("activity", "expected"),
    [
        ("cn.damai/.project.detail.ProjectDetailActivity", PageState.DETAIL_PAGE),
        ("cn.damai/.trade.NcovSkuActivity", PageState.SKU_PAGE),
        ("cn.damai/.launcher.splash.SplashMainActivity", PageState.HOMEPAGE),
        ("cn.damai/.search.SearchActivity", PageState.SEARCH_PAGE),
    ],
)
def test_state_from_activity(activity: str, expected: PageState):
    assert state_from_activity(activity) == expected.value


def test_search_activity_is_not_swallowed_by_main_activity():
    """映射表顺序有讲究：具体页必须排在兜底的 MainActivity 之前。

    搜索页也是从 MainActivity 起的子页面，顺序错了会被误判成首页，
    于是一整轮导航都会走错分支。
    """
    assert state_from_activity("cn.damai/.search.SearchActivity") == PageState.SEARCH_PAGE.value


@pytest.mark.parametrize("activity", [None, "", "com.other/.Foo"])
def test_state_from_activity_unknown(activity):
    assert state_from_activity(activity) is None


# --------------------------------------------------------------------------- #
# UI 树解析
# --------------------------------------------------------------------------- #
def test_parse_bounds():
    assert ui_tree.parse_bounds("[0,0][1080,2400]") == (0, 0, 1080, 2400)
    assert ui_tree.parse_bounds("bad") is None
    assert ui_tree.parse_bounds(None) is None


def test_node_center_rejects_degenerate_bounds():
    """宽或高为 0 的元素点不到，取中心必须返回 None 而不是同一个点。"""
    root = ui_tree.parse_xml('<hierarchy><node bounds="[10,10][10,20]" /></hierarchy>')
    node = ui_tree.iter_nodes(root)[0]
    assert ui_tree.node_center(node) is None


def test_parse_xml_tolerates_truncated_dump():
    """页面正在动画时 dump 会被截断，此时返回 None 让上层重试而不是抛异常。"""
    assert ui_tree.parse_xml("<hierarchy><node") is None


def test_descendant_texts_dedupes():
    """同一段文案常在外层容器和内层 TextView 上各出现一次。"""
    root = ui_tree.parse_xml(
        '<hierarchy><node text="立即购买"><node text="立即购买"/>'
        '<node text="¥318"/></node></hierarchy>'
    )
    node = ui_tree.iter_nodes(root)[0]
    assert ui_tree.descendant_texts(node) == ["立即购买", "¥318"]


def test_clickable_ancestor_finds_container():
    """日期的 tv_date 自身不可点，真正接点击的是祖先容器。"""
    root = ui_tree.parse_xml(
        '<hierarchy><node clickable="true"><node><node resource-id="cn.damai:id/tv_date" '
        'text="12.26 周六"/></node></node></hierarchy>'
    )
    parents = ui_tree.build_parent_map(root)
    date_node = ui_tree.find_by_resource_id(root, "cn.damai:id/tv_date")[0]
    ancestor = ui_tree.clickable_ancestor(parents, date_node)
    assert ancestor is not None
    assert ui_tree.is_clickable(ancestor)


def test_clickable_ancestor_returns_none_when_no_clickable_parent():
    """祖先链上没有可点节点时返回 None。

    调用方必须显式判 None：ElementTree 的空元素布尔值为 False，
    用 ``or date_node`` 会在这种合法情形下静默取错 bounds。
    """
    root = ui_tree.parse_xml(
        '<hierarchy><node><node><node resource-id="cn.damai:id/tv_date" text="12.26"/></node></node></hierarchy>'
    )
    parents = ui_tree.build_parent_map(root)
    date_node = ui_tree.find_by_resource_id(root, "cn.damai:id/tv_date")[0]
    assert ui_tree.clickable_ancestor(parents, date_node) is None


# --------------------------------------------------------------------------- #
# 详情页购买栏
# --------------------------------------------------------------------------- #
def test_v8_buy_button_text_is_readable():
    """v8.x 的 btn_buy_view 带文案，读出来才知道能不能点。"""
    root = ui_tree.parse_xml(_fixture("detail_v8_soldout.xml"))
    nodes = ui_tree.find_by_resource_id(root, "cn.damai:id/btn_buy_view")
    assert nodes
    assert ui_tree.descendant_texts(nodes[0]) == ["缺货登记"]
    assert is_safe_to_click("缺货登记") is False


def test_canvas_buy_bar_has_no_text():
    """≥9.0.2x 的 CTA 是 Canvas 自绘，整棵子树读不到文案。

    这条是「读不到 ≠ 没开售」的证据：只能靠坐标 + 时间闸门判定，
    绝不能因为没有文案就认定不能买。
    """
    root = ui_tree.parse_xml(_fixture("detail_canvas_empty.xml"))
    nodes = ui_tree.find_by_resource_id(
        root, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
    )
    assert nodes
    assert ui_tree.descendant_texts(nodes[0]) == []


@pytest.mark.parametrize("fixture", ["detail_v8_soldout.xml", "detail_canvas_empty.xml"])
def test_buy_bar_container_center_is_stable(fixture: str):
    """容器坐标兜底点击必须落在购买栏内，而不是屏幕别处。"""
    root = ui_tree.parse_xml(_fixture(fixture))
    nodes = ui_tree.find_by_resource_id(
        root, "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"
    )
    assert ui_tree.node_center(nodes[0]) == (540, 2325)


# --------------------------------------------------------------------------- #
# 票档
# --------------------------------------------------------------------------- #
def test_price_cards_only_count_clickable_direct_children():
    """卡片判据是「价格容器的直接子节点 + FrameLayout + clickable」。

    卡 3 的 FrameLayout clickable=false，必须被排除；卡内的 tv_price
    clickable=false，也不能被算成独立卡片。
    """
    root = ui_tree.parse_xml(_fixture("sku_price_cards.xml"))
    cards = parse_price_cards(root)
    assert len(cards) == 3
    assert [card.index for card in cards] == [0, 1, 2]


def test_price_card_tags_mark_unavailable():
    """「缺货登记」「预售」都不可点 —— 后者看着能点，点下去进预约流。"""
    cards = parse_price_cards(ui_tree.parse_xml(_fixture("sku_price_cards.xml")))
    assert cards[0].tag == ""
    assert cards[0].available is True
    assert cards[1].tag == "缺货登记"
    assert cards[1].available is False
    assert cards[2].tag == "预售"
    assert cards[2].available is False


def test_collapse_card_texts_drops_decorative_tags():
    """价格被拆成 ¥/318/起 三段，「惠」是营销角标要丢掉。"""
    assert collapse_card_texts(["¥", "318", "起", "惠"]) == "¥318起"
    assert price_matches(collapse_card_texts(["¥", "318", "起"]), "318")


def test_collapse_keeps_price_when_only_tag_exists():
    """只剩角标时拼出来是空串，此时该卡片必须被判为不可用。"""
    assert collapse_card_texts(["惠", ""]) == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("看台318", 318),
        ("¥318 起", 318),
        ("318元", 318),
        ("￥1,280", 280),  # 千分位只抓到后三位，交给归一化包含比较兜底
        ("", None),
        (None, None),
    ],
)
def test_extract_price_digits(text, expected):
    assert extract_price_digits(text) == expected


@pytest.mark.parametrize(
    ("card_text", "target", "expected"),
    [
        ("¥318起", "318", True),
        ("看台318", "318", True),
        ("318元", "看台318", True),
        ("¥618", "318", False),
        ("", "318", False),
    ],
)
def test_price_matches(card_text: str, target: str, expected: bool):
    assert price_matches(card_text, target) is expected


def test_panel_state_ready_when_any_card_available():
    """还有一档能买就不是售罄 —— 早判售罄会白白放弃一轮机会。"""
    cards_root = ui_tree.parse_xml(_fixture("sku_price_cards.xml"))
    assert detect_panel_state(cards_root) == "ready"


def test_panel_state_loading_beats_sold_out():
    """面板刚弹出时列表为空，必须判成 loading 而不是售罄。"""
    root = ui_tree.parse_xml(
        '<hierarchy><node resource-id="cn.damai:id/project_detail_perform_price_flowlayout"/>'
        '<node text="加载中，请稍候"/></hierarchy>'
    )
    assert detect_panel_state(root) == "loading"


def test_panel_state_unknown_when_container_missing():
    assert detect_panel_state(ui_tree.parse_xml("<hierarchy />")) == "unknown"
