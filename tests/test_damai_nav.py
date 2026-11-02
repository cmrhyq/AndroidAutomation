"""搜索与场次选择：标题匹配、结果卡打分、多场次唯一命中。

这一段是「宁可慢也不能错」的区域，所以测试重点全在**否决**上：
城市字段冲突、年份冲突必须能压过所有正向信号。这些判据一旦放松，
抢票链路会在开售瞬间高兴地买到错误城市的票。

场次侧的重点是「唯一命中是硬要求」：同日多条必须报错而不是随便挑一条。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from core.android import ui_tree
from data.constant.damai import CLICK_SCORE_THRESHOLD
from data.entity.damai import SearchCard
from pages.damai.detail_page import (
    SessionNotFoundError,
    enumerate_sessions,
    pick_session,
)
from pages.damai.search_page import (
    keyword_tokens,
    parse_cards,
    score_card,
    title_matches_target,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "damai"


def _xml(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _root(name: str) -> ET.Element:
    return ET.fromstring(_xml(name))


# --------------------------------------------------------------------------- #
# 结果卡解析
# --------------------------------------------------------------------------- #
def test_parse_cards_reads_all_fields():
    cards = parse_cards(_root("search_results_multi.xml"))
    assert len(cards) == 3
    assert cards[0].title == "许嵩「呼吸之野」巡回演唱会-福州站"
    assert cards[0].city == "厦门"
    assert cards[0].venue == "厦门奥林匹克体育中心"
    assert cards[0].show_time == "2026.12.26 周六 19:30"


def test_parse_cards_uses_container_center():
    """点卡片必须点容器中心，不能点内部 TextView（会点不中）。"""
    cards = parse_cards(_root("search_results_multi.xml"))
    assert cards[0].coords == (540, 450)   # [0,300][1080,600]
    assert cards[1].coords == (540, 750)   # [0,600][1080,900]


def test_parse_cards_empty_when_no_results():
    assert parse_cards(ui_tree.parse_xml("<hierarchy />")) == []


# --------------------------------------------------------------------------- #
# 关键词与标题匹配
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("keyword", "expected"),
    [
        ("许嵩 演唱会", ["许嵩", "演唱会"]),
        ("  许嵩   演唱会  ", ["许嵩", "演唱会"]),
        ("许嵩 许嵩", ["许嵩"]),
        ("", []),
    ],
)
def test_keyword_tokens(keyword: str, expected: list[str]):
    assert keyword_tokens(keyword) == expected


def test_title_matches_exact_substring():
    assert title_matches_target("许嵩「呼吸之野」巡回演唱会-福州站", "呼吸之野") is True


def test_title_matches_fuzzy_variant():
    """同一演出的官方写法与用户写法常有词序/标点差异。"""
    assert title_matches_target("许嵩呼吸之野巡回演唱会", "许嵩《呼吸之野》巡回演唱会") is True


def test_title_does_not_match_unrelated_artist():
    """「周杰伦 演唱会」不能被当成「许嵩 演唱会」—— 共同后缀不足以判定同一演出。"""
    assert title_matches_target("周杰伦嘉年华演唱会", "许嵩呼吸之野演唱会") is False


def test_title_empty_target_never_matches():
    assert title_matches_target("任意标题", "") is False


# --------------------------------------------------------------------------- #
# 打分：正向
# --------------------------------------------------------------------------- #
def _card(title: str, city: str = "", venue: str = "") -> SearchCard:
    return SearchCard(index=0, title=title, city=city, venue=venue, coords=(1, 1))


def test_correct_card_clears_threshold():
    point = score_card(
        _card("许嵩「呼吸之野」巡回演唱会-福州站", "福州", "福州海峡奥体中心"),
        keyword="许嵩 演唱会",
        target_title="许嵩「呼吸之野」巡回演唱会-福州站",
        target_venue="福州海峡奥体中心",
        city="福州",
    )
    assert point >= CLICK_SCORE_THRESHOLD


def test_unrelated_show_in_same_city_stays_below_threshold():
    """同城的无关演出不能过阈值 —— 否则「搜到什么都点」等于随机买票。"""
    point = score_card(
        _card("某某话剧《雷雨》福州站", "福州"),
        keyword="许嵩 演唱会",
        city="福州",
    )
    assert point < CLICK_SCORE_THRESHOLD


def test_perfect_keyword_match_scores_higher_than_partial():
    exact = score_card(_card("许嵩演唱会", "福州"), keyword="许嵩演唱会", city="福州")
    partial = score_card(_card("许嵩巡回演唱会福州站", "福州"), keyword="许嵩演唱会", city="福州")
    assert exact > partial


# --------------------------------------------------------------------------- #
# 打分：否决（这一段是整个文件存在的理由）
# --------------------------------------------------------------------------- #
def test_city_field_conflict_hard_vetoes():
    """标题写着福州、城市字段写厦门 —— 城市是结构化字段，冲突即否决。

    这条断言防的是「罚分压不住正向加分」：标题路径能拿 +100+50+词元分（130+），
    扣 80 分照样过阈值 60，罚则形同虚设。所以必须是返回负分。
    """
    point = score_card(
        _card("许嵩「呼吸之野」巡回演唱会-福州站", "厦门", "厦门奥林匹克体育中心"),
        keyword="许嵩 演唱会",
        target_title="许嵩「呼吸之野」巡回演唱会-福州站",
        city="福州",
    )
    assert point < 0


def test_year_conflict_hard_vetoes():
    """keyword 写了 2026、标题写 2024 —— 同名跨年巡演，必须否决。"""
    point = score_card(
        _card("许嵩 2024 演唱会-福州站", "福州"),
        keyword="许嵩 2026 演唱会",
        city="福州",
    )
    assert point < 0


def test_year_penalty_skipped_when_title_has_no_year():
    """标题不含年份时不构成冲突证据，不能罚。"""
    point = score_card(
        _card("许嵩巡回演唱会-福州站", "福州"),
        keyword="许嵩 2026 演唱会",
        city="福州",
    )
    assert point > 0


def test_multi_city_title_is_not_vetoed():
    """「北京·上海联演」这类文案里出现多个城市，但目标城市也在其中，不能否决。"""
    point = score_card(
        _card("许嵩演唱会北京上海联演-福州站", "福州"),
        keyword="许嵩 演唱会",
        city="福州",
    )
    assert point > 0


def test_unknown_card_city_does_not_trigger_veto():
    """城市字段为空（大麦部分卡不显示城市）时没有证据，不该否决。"""
    point = score_card(
        _card("许嵩「呼吸之野」巡回演唱会", ""),
        keyword="许嵩 演唱会",
        city="福州",
    )
    assert point > 0


def test_best_card_wins_among_traps():
    """三张陷阱卡放在一起时，只有正确那张能过阈值。"""
    cards = parse_cards(_root("search_results_multi.xml"))
    scored = [
        score_card(
            card,
            keyword="许嵩 演唱会",
            target_title="许嵩「呼吸之野」巡回演唱会-福州站",
            target_venue="福州海峡奥体中心",
            city="福州",
        )
        for card in cards
    ]
    assert scored[0] < 0                       # 城市字段冲突
    assert scored[2] >= CLICK_SCORE_THRESHOLD  # 真目标
    assert scored[2] > scored[1]               # 跨年同名巡演分数更低


# --------------------------------------------------------------------------- #
# 场次
# --------------------------------------------------------------------------- #
def test_enumerate_sessions_uses_clickable_ancestor_bounds():
    """tv_date 自身不可点，坐标必须取祖先日期卡的中心。

    夹具里三张卡的容器 bounds 分别是 [40,1420][540,1580] 等，
    直接取 tv_date 的 bounds 会得到偏移后的错误坐标，点下去可能点到隔壁卡。
    """
    sessions = enumerate_sessions(_root("sku_panel_sessions.xml"))
    assert len(sessions) == 3
    assert sessions[0].coords == (290, 1500)
    assert sessions[1].coords == (790, 1500)
    assert sessions[2].coords == (1270, 1500)


def test_enumerate_sessions_normalizes_date():
    sessions = enumerate_sessions(_root("sku_panel_sessions.xml"))
    assert [item.date_key for item in sessions] == ["12.26", "12.26", "12.27"]


def test_enumerate_sessions_empty_without_panel():
    assert enumerate_sessions(ui_tree.parse_xml("<hierarchy />")) == []


def test_pick_session_unique_date():
    sessions = enumerate_sessions(_root("sku_panel_sessions.xml"))
    chosen = pick_session(sessions, "12.27")
    assert chosen.index == 2


def test_pick_session_ambiguous_date_raises():
    """同日两条必须报错。选错场次买错票，比买不到更糟。"""
    sessions = enumerate_sessions(_root("sku_panel_sessions.xml"))
    with pytest.raises(SessionNotFoundError, match="命中 2 条"):
        pick_session(sessions, "12.26")


def test_pick_session_missing_date_raises():
    sessions = enumerate_sessions(_root("sku_panel_sessions.xml"))
    with pytest.raises(SessionNotFoundError, match="没有日期"):
        pick_session(sessions, "01.01")


def test_pick_session_without_target_date_refuses_to_guess():
    """未配置日期时绝不盲选第一条。"""
    sessions = enumerate_sessions(_root("sku_panel_sessions.xml"))
    with pytest.raises(SessionNotFoundError, match="未配置场次日期"):
        pick_session(sessions, None)


def test_pick_session_accepts_alternative_date_formats():
    """配置里写 12月27日 / 2026-12-27 都应能用。"""
    sessions = enumerate_sessions(_root("sku_panel_sessions.xml"))
    assert pick_session(sessions, "12月27日").index == 2
    assert pick_session(sessions, "2026-12-27").index == 2
