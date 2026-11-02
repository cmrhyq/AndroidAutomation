"""票档选择的决策行为：按文案选、按索引兜底、售罄与越界要报错。

纯解析逻辑（卡片枚举、标签判定、价格匹配）在 ``test_damai_logic.py`` 里；
这里测的是**选哪一张、点哪里、什么时候必须放弃**，用假设备喂真机结构夹具。

为什么专门测「放弃」：抢票链路上最糟的不是失败，而是「明明没票还硬点」——
重复点一个售罄档位会把好不容易弹出来的面板点掉，整轮重来。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pages.components.damai_price_panel import (
    DamaiPricePanel,
    PricePanelMissingError,
    PriceUnavailableError,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "damai"


def _xml(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeDevice:
    """只实现票档面板真正会用到的那几个方法。"""

    def __init__(self, xml: str = "") -> None:
        self._xml = xml
        self.taps: list[tuple[int, int]] = []

    def ui_xml(self, *, ttl: float = 0.0) -> str:
        return self._xml

    def invalidate_ui(self) -> None:
        return None

    def tap_xy(self, x: int, y: int) -> None:
        self.taps.append((x, y))

    def wait_short(self, reason: str = "") -> float:
        return 0.0

    def page_source(self) -> str:
        return self._xml

    @property
    def current_activity(self) -> str:
        return "cn.damai/.project.detail.ProjectDetailActivity"


def _panel(xml_file: str = "sku_price_cards.xml") -> tuple[DamaiPricePanel, FakeDevice]:
    dev = FakeDevice(_xml(xml_file))
    return DamaiPricePanel(dev, None), dev


# --------------------------------------------------------------------------- #
# 选档
# --------------------------------------------------------------------------- #
def test_select_by_price_text():
    """目标 318 只有卡 0 有，且它是可售的。"""
    panel, dev = _panel()
    card = panel.select("318")
    assert card.index == 0
    # 卡 0 容器 bounds [40,1820][540,1960] → 中心 (290, 1890)
    assert dev.taps == [(290, 1890)]


def test_select_by_index_counts_available_only():
    """索引在**可用卡片列表**里数，不是原始卡片序号。

    中间那张写着「缺货登记」，index=1 必须跳过它落到第三张可售卡 ——
    否则配了索引就会点到售罄档。
    """
    xml = (
        '<hierarchy><node resource-id="cn.damai:id/project_detail_perform_price_flowlayout">'
        '<node class="android.widget.FrameLayout" clickable="true" bounds="[0,0][10,10]">'
        '<node text="318"/></node>'
        '<node class="android.widget.FrameLayout" clickable="true" bounds="[20,0][30,10]">'
        '<node text="618"/><node text="缺货登记"/></node>'
        '<node class="android.widget.FrameLayout" clickable="true" bounds="[40,0][50,10]">'
        '<node text="918"/></node>'
        "</node></hierarchy>"
    )
    dev = FakeDevice(xml)
    panel = DamaiPricePanel(dev, None)
    card = panel.select(target_price=None, index=1)
    assert card.price_text == "918"
    assert dev.taps == [(45, 5)]


def test_select_prefers_text_over_index():
    """文案匹配可验证，索引只是兜底：文案能匹配时就不该碰索引。"""
    panel, dev = _panel()
    # 索引 5 其实越界，但文案能匹配到卡 0，所以不会走到索引分支
    card = panel.select("318", index=5)
    assert card.price_text == "¥318起"
    assert dev.taps == [(290, 1890)]


def test_same_price_prefers_available_card():
    """同一价格既有可售又有售罄档时，必须挑可售那张。"""
    xml = (
        '<hierarchy><node resource-id="cn.damai:id/project_detail_perform_price_flowlayout">'
        '<node class="android.widget.FrameLayout" clickable="true" bounds="[0,0][10,10]">'
        '<node text="318"/><node text="缺货登记"/></node>'
        '<node class="android.widget.FrameLayout" clickable="true" bounds="[20,0][30,10]">'
        '<node text="318"/></node>'
        "</node></hierarchy>"
    )
    panel = DamaiPricePanel(FakeDevice(xml), None)
    card = panel.select("318")
    assert card.available is True
    assert card.index == 1


# --------------------------------------------------------------------------- #
# 必须放弃的几种情况
# --------------------------------------------------------------------------- #
def test_all_unavailable_raises():
    """全部不可售时抛错，让上层停手，而不是死循环点。"""
    xml = (
        '<hierarchy><node resource-id="cn.damai:id/project_detail_perform_price_flowlayout">'
        '<node class="android.widget.FrameLayout" clickable="true">'
        '<node text="318"/><node text="缺货登记"/></node>'
        '<node class="android.widget.FrameLayout" clickable="true">'
        '<node text="618"/><node text="预售"/></node>'
        "</node></hierarchy>"
    )
    panel = DamaiPricePanel(FakeDevice(xml), None)
    with pytest.raises(PriceUnavailableError, match="均不可选"):
        panel.select("318")


def test_missing_panel_raises():
    """面板没弹出来和「面板里没票」是两回事，必须分开报。"""
    panel = DamaiPricePanel(FakeDevice("<hierarchy />"), None)
    with pytest.raises(PricePanelMissingError):
        panel.select("318")


def test_index_out_of_range_raises():
    panel, dev = _panel()
    with pytest.raises(PriceUnavailableError, match="越界"):
        panel.select(target_price=None, index=9)
    assert dev.taps == []  # 报错前一个点击都不该发生


def test_unmatched_price_raises_with_candidates():
    """匹配不上时报错要带可选项，方便直接改配置。"""
    panel, dev = _panel()
    with pytest.raises(PriceUnavailableError, match="没有匹配"):
        panel.select("999")
    assert dev.taps == []


def test_no_target_and_no_index_raises():
    panel, dev = _panel()
    with pytest.raises(PriceUnavailableError):
        panel.select(None)
    assert dev.taps == []
