"""导航层：多场次选择、演出标题匹配、搜索卡评分、页面辅助函数。

这一段是「宁可慢也不能错」的区域，所以测试重点全在**否决**上：
城市冲突要能压过所有正向信号，年份冲突要把同名跨年巡演压到阈值以下。
这些判据一旦放松，抢票链路会在开售瞬间高兴地买到错误城市的票。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from damai.config import GrabConfig
from damai.mode import GrabMode
from damai.navigator import (
    CLICK_SCORE_THRESHOLD,
    EventNavigator,
    SessionNotFoundError,
    enumerate_sessions_from_xml,
    select_session,
)
from damai.page_helpers import (
    HomeNotReadyError,
    SearchAmbiguousError,
    SearchEmptyError,
    enumerate_search_results_from_xml,
    read_detail_title,
    read_detail_venue,
    select_search_result,
    wait_for_home_ready,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "damai"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------- #
# 测试替身
# ---------------------------------------------------------------------- #
class FakeDriver:
    def __init__(self, xml: str = "") -> None:
        self.xml = xml
        self.clicks: list[tuple[int, int]] = []

    def dump_hierarchy(self) -> str:
        return self.xml

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))


class FakeProbe:
    """按预设序列返回页面状态，序列用尽后一直返回最后一个。"""

    def __init__(self, states: list[str]) -> None:
        self._states = list(states)
        self.invalidations = 0

    def probe(self, fast: bool = False) -> dict[str, Any]:
        state = self._states[0] if len(self._states) == 1 else self._states.pop(0)
        return {"state": state}

    def invalidate(self) -> None:
        self.invalidations += 1


class FakeNavigationHost:
    """导航/恢复测试用宿主：只实现被测路径真正会碰的方法。"""

    def __init__(self, states: list[str], xml: str = "", config: Any = None) -> None:
        self.probe = FakeProbe(states)
        self.d = FakeDriver(xml)
        self.config = config or _config()
        self._xml_root: ET.Element | None = ET.fromstring(xml) if xml else None
        self.backs = 0
        self.navigate_calls = 0

    def probe_current_page(self, fast: bool = False) -> dict[str, Any]:
        return self.probe.probe(fast=fast)

    def _press_keycode_safe(self, keycode: int, context: str = "") -> bool:
        self.backs += 1
        return True

    def _dump_hierarchy_xml(self) -> ET.Element | None:
        return self._xml_root

    def dismiss_startup_popups(self) -> bool:
        return False


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


# ---------------------------------------------------------------------- #
# 场次选择
# ---------------------------------------------------------------------- #
def test_enumerate_sessions_uses_clickable_ancestor_bounds():
    """tv_date 自己不可点，bounds 必须取自可点祖先，否则点不动。"""
    cards = enumerate_sessions_from_xml(_fixture("sku_panel_sessions.xml"))
    assert len(cards) == 3
    assert cards[0]["date"] == "12.26 周六 19:30"
    # 祖先卡 bounds [40,1420][540,1580]，不是 tv_date 的 [60,1440][520,1520]
    assert cards[0]["bounds"] == "[40,1420][540,1580]"
    # 卡片文本要带上同卡内的价格，city 判据才有东西可匹配
    assert "看台318起" in cards[0]["text"]


def test_select_session_by_date_and_city_uses_city_to_break_tie():
    """两条 12.26 场次靠卡片内文案区分。"""
    driver = FakeDriver(_fixture("sku_panel_sessions.xml"))
    index = select_session(driver, date="2026-12-26", city="看台618")
    assert index == 1
    assert driver.clicks == [(790, 1500)]


def test_select_session_date_only_ambiguous_raises():
    """两条同日场次只给 date → 必须报错，绝不随便挑一条。"""
    driver = FakeDriver(_fixture("sku_panel_sessions.xml"))
    with pytest.raises(SessionNotFoundError, match="命中 2 条"):
        select_session(driver, date="2026-12-26")
    assert driver.clicks == []


def test_select_session_unique_date_succeeds():
    driver = FakeDriver(_fixture("sku_panel_sessions.xml"))
    assert select_session(driver, date="2026-12-27") == 2
    assert driver.clicks == [(1270, 1500)]


def test_select_session_fallback_index():
    driver = FakeDriver(_fixture("sku_panel_sessions.xml"))
    assert select_session(driver, fallback_index=0) == 0


def test_select_session_fallback_out_of_range_raises():
    driver = FakeDriver(_fixture("sku_panel_sessions.xml"))
    with pytest.raises(SessionNotFoundError, match="越界"):
        select_session(driver, fallback_index=9)


def test_select_session_without_criteria_raises():
    driver = FakeDriver(_fixture("sku_panel_sessions.xml"))
    with pytest.raises(SessionNotFoundError, match="无法选择场次"):
        select_session(driver)


def test_select_session_no_panel_raises():
    driver = FakeDriver('<hierarchy><node text="无场次面板" /></hierarchy>')
    with pytest.raises(SessionNotFoundError, match="未发现可选场次"):
        select_session(driver, date="2026-12-26")


# ---------------------------------------------------------------------- #
# 标题匹配与评分
# ---------------------------------------------------------------------- #
@pytest.fixture
def navigator() -> EventNavigator:
    host = FakeNavigationHost(["detail_page"], xml=_fixture("detail_v8_soldout.xml"))
    return EventNavigator(host)


def test_title_matches_target_via_keyword(navigator: EventNavigator):
    assert navigator.title_matches_target("许嵩「呼吸之野」巡回演唱会-福州站") is True


def test_city_conflict_vetoes_even_when_keyword_matches(navigator: EventNavigator):
    """标题含完整 keyword，但城市写成福州以外 → 一票否决。

    这是整套匹配逻辑的地基：正向信号再多，也不能推翻「城市不对」。
    """
    assert navigator.title_matches_target("许嵩「呼吸之野」巡回演唱会-上海站") is False


def test_title_matches_target_rejects_unrelated(navigator: EventNavigator):
    assert navigator.title_matches_target("周杰伦嘉年华世界巡回演唱会-福州站") is False


def test_score_prefers_correct_city_card(navigator: EventNavigator):
    """三张卡里，城市字段说谎的那张必须被否决，真目标必须过线。"""
    cards = enumerate_search_results_from_xml(_fixture("search_results_multi.xml"))
    assert len(cards) == 3

    node = ET.fromstring(_fixture("search_results_multi.xml"))
    scored = []
    for card_node in node.iter("node"):
        if card_node.get("resource-id") != "cn.damai:id/ll_search_item":
            continue
        fields = {}
        for desc in card_node.iter("node"):
            fields[desc.get("resource-id")] = (desc.get("text") or "").strip()
        scored.append(
            navigator.score_search_result(
                fields["cn.damai:id/tv_project_name"],
                fields["cn.damai:id/tv_project_venueName"],
                fields["cn.damai:id/tv_project_city"],
            )
        )

    # 卡1 标题写福州、城市字段写厦门 —— 字段冲突是硬否决，必须掉到阈值以下。
    # 注意这里标题能拿满 +100+50+词元分（实测 222 分），扣 80 分根本压不住，
    # 所以罚则必须是「否决」而不是「扣分」，这条断言就是那个修法的护栏。
    assert scored[0] < CLICK_SCORE_THRESHOLD
    # 卡3 是真目标
    assert scored[2] >= CLICK_SCORE_THRESHOLD


def test_year_conflict_vetoes_wrong_year_card():
    """keyword 带年份时，同名不同年的巡演必须被否决。

    年份否决只在「keyword 与标题**都**写明四位年份」时生效——标题没写年份
    不构成冲突证据，罚了反而误伤。
    """
    nav = EventNavigator(
        FakeNavigationHost(["detail_page"], config=_config(keyword="许嵩 2026 演唱会"))
    )
    node = ET.fromstring(_fixture("search_results_multi.xml"))
    scored = []
    for card_node in node.iter("node"):
        if card_node.get("resource-id") != "cn.damai:id/ll_search_item":
            continue
        fields = {}
        for desc in card_node.iter("node"):
            fields[desc.get("resource-id")] = (desc.get("text") or "").strip()
        scored.append(
            nav.score_search_result(
                fields["cn.damai:id/tv_project_name"],
                fields["cn.damai:id/tv_project_venueName"],
                fields["cn.damai:id/tv_project_city"],
            )
        )
    # 卡2 是 2024 年的同名巡演：直接否决，而不是扣分了事
    assert scored[1] < 0
    assert scored[1] < CLICK_SCORE_THRESHOLD
    # 卡3 标题不含年份 → 不构成冲突，仍可正常打分
    assert scored[2] >= CLICK_SCORE_THRESHOLD


def test_year_veto_skipped_when_title_has_no_year():
    """标题不含年份时不能否决，否则正常标题会被误伤。"""
    nav = EventNavigator(
        FakeNavigationHost(["detail_page"], config=_config(keyword="许嵩 2026 演唱会"))
    )
    no_year = nav.score_search_result("许嵩巡回演唱会-福州站", "福州海峡奥体中心", "福州")
    wrong_year = nav.score_search_result("许嵩2024演唱会-福州站", "福州海峡奥体中心", "福州")
    assert no_year > 0
    assert wrong_year < 0


def test_city_field_conflict_penalty_is_large(navigator: EventNavigator):
    """同城无关演出不能靠城市加分爬到阈值上 —— 所以加分只有 +10。"""
    same_city_unrelated = navigator.score_search_result(
        "五月天跨年演唱会", "福州海峡奥体中心", "福州"
    )
    assert same_city_unrelated < CLICK_SCORE_THRESHOLD


# ---------------------------------------------------------------------- #
# 搜索结果选择（0 / 1 / N 规则）
# ---------------------------------------------------------------------- #
def test_search_results_enumerated_with_titles():
    cards = enumerate_search_results_from_xml(_fixture("search_results_multi.xml"))
    assert [card["index"] for card in cards] == [0, 1, 2]
    assert cards[2]["title"] == "许嵩「呼吸之野」巡回演唱会-福州站"


def test_select_search_result_empty_raises(tmp_path: Path):
    driver = FakeDriver("<hierarchy><node /></hierarchy>")
    with pytest.raises(SearchEmptyError):
        select_search_result(driver, keyword="许嵩", timeout=0.1, dump_dir=tmp_path)


def test_select_search_result_single_clicks_it():
    driver = FakeDriver(_fixture("detail_v8_soldout.xml").replace("bricks", "x"))
    driver.xml = (
        '<hierarchy><node resource-id="cn.damai:id/ll_search_item" bounds="[0,100][1000,300]">'
        '<node resource-id="cn.damai:id/tv_project_name" text="唯一结果" /></node></hierarchy>'
    )
    chosen = select_search_result(driver, keyword="x")
    assert chosen["title"] == "唯一结果"
    assert driver.clicks == [(500, 200)]


def test_select_search_result_multi_fuzzy_match():
    driver = FakeDriver(_fixture("search_results_multi.xml"))
    chosen = select_search_result(driver, keyword="许嵩", target_title="呼吸之野-福州站")
    assert chosen["index"] == 0  # 模糊匹配命中的是第一张（包含关系）

    driver2 = FakeDriver(_fixture("search_results_multi.xml"))
    chosen2 = select_search_result(driver2, keyword="许嵩", target_title="2024")
    assert chosen2["index"] == 1


def test_select_search_result_multi_strict_raises(tmp_path: Path):
    driver = FakeDriver(_fixture("search_results_multi.xml"))
    with pytest.raises(SearchAmbiguousError):
        select_search_result(
            driver, keyword="许嵩", target_title="不存在的标题", strict=True, dump_dir=tmp_path
        )


def test_select_search_result_multi_non_strict_falls_back_to_first():
    """非严格模式退回第一条是有意的：宁可点错再退回来，也不犹豫错过开售。"""
    driver = FakeDriver(_fixture("search_results_multi.xml"))
    chosen = select_search_result(driver, keyword="许嵩", target_title="不存在的标题")
    assert chosen["index"] == 0


# ---------------------------------------------------------------------- #
# 首页就绪与详情页文案
# ---------------------------------------------------------------------- #
def test_wait_for_home_ready_returns_homepage():
    probe = FakeProbe(["search_page", "homepage"])
    driver = FakeDriver("")
    assert wait_for_home_ready(driver, probe)["state"] == "homepage"


def test_wait_for_home_ready_timeout_raises_and_dumps(tmp_path: Path):
    probe = FakeProbe(["search_page"])
    driver = FakeDriver("<hierarchy><node text='白屏' /></hierarchy>")
    with pytest.raises(HomeNotReadyError):
        wait_for_home_ready(driver, probe, timeout=0.2, dump_dir=tmp_path)
    dumps = list(tmp_path.glob("home_probe_*.xml"))
    assert len(dumps) == 1
    assert "白屏" in dumps[0].read_text(encoding="utf-8")


def test_read_detail_title_from_fixture():
    root = ET.fromstring(_fixture("detail_v8_soldout.xml"))
    assert read_detail_title(root) == "许嵩「呼吸之野」巡回演唱会-福州站"


def test_read_detail_title_empty_when_missing():
    root = ET.fromstring('<hierarchy><node text="x" /></hierarchy>')
    assert read_detail_title(root) == ""


def test_read_detail_venue_empty_when_missing():
    root = ET.fromstring(_fixture("detail_v8_soldout.xml"))
    assert read_detail_venue(root) == ""
