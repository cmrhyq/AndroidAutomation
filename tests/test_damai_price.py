"""票档选择：卡片枚举、文案归一、索引选取、购买按钮坐标。

重点是「不可选」判定——大麦把「缺货登记」「预售」这类标记塞在票档卡片里，
看着像可点的档位。点错要么白跑一趟，要么进预约流程而不是下单。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from damai.config import GrabConfig
from damai.mode import GrabMode
from damai.price import (
    PriceCard,
    PriceSelector,
    PriceSelectorError,
    SoldOutError,
    bounds_center,
    collapse_card_texts,
    extract_price_digits,
    price_cards_from_xml,
    price_text_matches_target,
    select_card_by_index,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "damai"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _config(**overrides: Any) -> GrabConfig:
    payload: dict[str, Any] = {
        "keyword": "许嵩 演唱会",
        "users": ("黄昱桥",),
        "city": "福州",
        "date": "2026-12-26",
        "price": "看台318",
        "price_index": 0,
        "mode": GrabMode.VALIDATION,
    }
    payload.update(overrides)
    return GrabConfig(**payload)


class FakeHost:
    """PriceSelector 只用这几样东西。"""

    def __init__(self, xml: str = "", config: Any = None) -> None:
        self.config = config or _config()
        self._xml = xml
        self.clicks: list[tuple[int, int]] = []
        self.burst_clicks: list[tuple[int, int]] = []
        self._cached_hot_path_coords: dict[str, Any] = {}
        self.d = None

    def _dump_hierarchy_xml(self) -> ET.Element | None:
        return ET.fromstring(self._xml) if self._xml else None

    def _click_coordinates(self, x: int, y: int, duration: int = 50) -> None:
        self.clicks.append((x, y))

    def _burst_click_coordinates(
        self, x: int, y: int, count: int = 2, interval_ms: int = 25, duration: int = 25
    ) -> None:
        self.burst_clicks.append((x, y))

    def ultra_fast_click(self, by: str, value: str, timeout: float = 0.5) -> bool:
        return False


# ---------------------------------------------------------------------- #
# 纯函数
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("看台318", 318),
        ("318", 318),
        ("内场1218", 1218),
        ("¥598 起", 598),
        ("无票", None),
        ("", None),
        (None, None),
    ],
)
def test_extract_price_digits(text: Any, expected: int | None):
    assert extract_price_digits(text) == expected


@pytest.mark.parametrize(
    ("text", "target", "expected"),
    [
        ("看台318", "看台318", True),
        ("318元", "318", True),
        ("¥318 起", "318", True),
        ("内场618", "看台318", False),
        ("", "318", False),
        ("318", "", False),
    ],
)
def test_price_text_matches_target(text: str, target: str, expected: bool):
    assert price_text_matches_target(text, target) is expected


def test_collapse_card_texts_drops_decorative_tags():
    """价格被拆成 ¥/318/起 三段，角标「惠」要丢掉，其余原样拼。"""
    assert collapse_card_texts(["¥", "318", "起", "惠"]) == "¥318起"
    # 拼出来的标签仍要能被目标票价匹配上，否则等于白拼
    assert price_text_matches_target(collapse_card_texts(["¥", "318", "起"]), "318")


def test_collapse_card_texts_keeps_compound_form():
    assert collapse_card_texts(["看台318", "缺货登记"]) == "看台318缺货登记"


def test_collapse_card_texts_empty():
    assert collapse_card_texts([]) == ""
    assert collapse_card_texts(["惠", "荐"]) == ""


def test_bounds_center():
    assert bounds_center("[40,1820][540,1960]") == (290, 1890)
    assert bounds_center("") is None
    assert bounds_center("garbage") is None


# ---------------------------------------------------------------------- #
# 卡片枚举
# ---------------------------------------------------------------------- #
def test_cards_enumerated_only_for_clickable_direct_children():
    """只有「直接子节点 + clickable」才是卡片，卡内 TextView 不算。"""
    cards = price_cards_from_xml(ET.fromstring(_fixture("sku_price_cards.xml")))
    assert [card.index for card in cards] == [0, 1, 2]
    # 第四个子节点 clickable=false，不是卡片
    assert len(cards) == 3


def test_card_text_and_availability():
    cards = price_cards_from_xml(ET.fromstring(_fixture("sku_price_cards.xml")))
    assert cards[0].available is True
    assert "318" in cards[0].price_text
    assert cards[1].available is False
    assert cards[1].tag == "缺货登记"
    # 「预售」也算不可选：点了会进预约流程
    assert cards[2].available is False
    assert cards[2].tag == "预售"


def test_cards_empty_without_container():
    assert price_cards_from_xml(ET.fromstring("<hierarchy><node /></hierarchy>")) == []
    assert price_cards_from_xml(None) == []


# ---------------------------------------------------------------------- #
# 索引选取
# ---------------------------------------------------------------------- #
def test_select_card_by_index_ok():
    cards = [PriceCard(index=0, price_text="318元"), PriceCard(index=1, price_text="618元")]
    assert select_card_by_index(cards, 1).price_text == "618元"


def test_select_card_by_index_lists_available_on_overflow():
    """越界时必须把可用档位全列出来——配置错了只看异常信息就能改对。"""
    cards = [PriceCard(index=0, price_text="318元"), PriceCard(index=1, price_text="618元", tag="缺货")]
    with pytest.raises(PriceSelectorError) as excinfo:
        select_card_by_index(cards, 5)
    message = str(excinfo.value)
    assert "越界" in message
    assert "[0] 318元" in message
    assert "缺货" in message


def test_select_card_by_index_empty_lists_causes():
    with pytest.raises(PriceSelectorError, match="未发现可点击票档卡片"):
        select_card_by_index([], 0)


# ---------------------------------------------------------------------- #
# 购买按钮坐标
# ---------------------------------------------------------------------- #
def test_buy_button_coords_prefers_button_id():
    host = FakeHost(_fixture("sku_price_cards.xml"))
    assert PriceSelector(host).buy_button_coords() == (540, 2325)


def test_buy_button_coords_falls_back_to_bar_container():
    """Canvas 自绘版本没有 btn_buy_view，只能取底栏容器中心。"""
    host = FakeHost(_fixture("detail_canvas_empty.xml"))
    coords = PriceSelector(host).buy_button_coords()
    assert coords is not None
    # 容器 bounds 是 [0,2250][1080,2400]，中心落在底栏上
    assert coords == (540, 2325)


def test_buy_button_coords_none_when_absent():
    host = FakeHost("<hierarchy><node /></hierarchy>")
    assert PriceSelector(host).buy_button_coords() is None


# ---------------------------------------------------------------------- #
# 主入口 select()
# ---------------------------------------------------------------------- #
def test_select_clicks_configured_index_by_coordinate(monkeypatch: pytest.MonkeyPatch):
    host = FakeHost(_fixture("sku_price_cards.xml"), config=_config(price_index=1))
    monkeypatch.setattr("damai.price.detect_price_panel_state", lambda device: "ready")
    assert PriceSelector(host).select() is True
    # 卡1 bounds [540,1820][1040,1960] → 中心 (790, 1890)
    assert host.clicks == [(790, 1890)]


def test_select_burst_clicks_in_submit_mode(monkeypatch: pytest.MonkeyPatch):
    """SUBMIT 模式走连点，抢在列表刷新间隙落地。"""
    host = FakeHost(
        _fixture("sku_price_cards.xml"),
        config=_config(price_index=0, mode=GrabMode.SUBMIT),
    )
    monkeypatch.setattr("damai.price.detect_price_panel_state", lambda device: "ready")
    assert PriceSelector(host).select(burst=True) is True
    assert host.burst_clicks == [(290, 1890)]
    assert host.clicks == []


def test_select_returns_false_when_index_points_at_unavailable(monkeypatch: pytest.MonkeyPatch):
    """索引落到「缺货登记」档时要停手，不能点。"""
    host = FakeHost(_fixture("sku_price_cards.xml"), config=_config(price_index=1))
    monkeypatch.setattr("damai.price.detect_price_panel_state", lambda device: "ready")

    selector = PriceSelector(host)
    # 只在 XML 枚举路径下才会读到卡片文案；这里直接替换 cards 验证分支
    cards = price_cards_from_xml(ET.fromstring(_fixture("sku_price_cards.xml")))
    monkeypatch.setattr(selector, "cards", lambda xml_root=None: cards)
    monkeypatch.setattr(selector, "selected_coords", lambda xml_root=None: None)

    # 索引 1 恰是「缺货登记」档
    assert selector.select() is False


def test_await_price_panel_raises_on_sold_out(monkeypatch: pytest.MonkeyPatch):
    host = FakeHost(_fixture("sku_price_cards.xml"))
    monkeypatch.setattr("damai.price.detect_price_panel_state", lambda device: "sold_out")
    with pytest.raises(SoldOutError):
        PriceSelector(host).await_price_panel()


def test_await_price_panel_raises_on_persistent_loading(monkeypatch: pytest.MonkeyPatch):
    host = FakeHost(_fixture("sku_price_cards.xml"))
    monkeypatch.setattr("damai.price.detect_price_panel_state", lambda device: "loading")
    with pytest.raises(PriceSelectorError, match="加载超时"):
        PriceSelector(host).await_price_panel(max_wait_s=0.15)


def test_await_price_panel_tolerates_unknown(monkeypatch: pytest.MonkeyPatch):
    """unknown 只是选择器可能过期，不该中断流程。"""
    host = FakeHost(_fixture("sku_price_cards.xml"))
    monkeypatch.setattr("damai.price.detect_price_panel_state", lambda device: "unknown")
    assert PriceSelector(host).await_price_panel() == "unknown"
