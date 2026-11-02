"""文本归一 / 标题相似度 / 日期归一的离线用例。

这三个纯函数决定了「搜索结果里点哪一个」，以及「场次日期是否匹配」，
判错就等于抢错演出，所以边界逐个钉死。"""

from __future__ import annotations

import pytest

from core.text import (
    KNOWN_CITY_TOKENS,
    city_keyword,
    find_conflicting_city,
    normalize_date,
    normalize_text,
    title_similarity,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------- #
# normalize_date
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw",
    ["4月6号", "4 月 6 日", "04月06日", "4/6", "04-06", "04.06", "2026-04-06", "2026/04/06", "2026.04.06"],
)
def test_normalize_date_variants(raw: str):
    assert normalize_date(raw) == "04.06"


@pytest.mark.parametrize("raw", ["", "   ", "下个月", "13/40", None])
def test_normalize_date_rejects_garbage(raw: object):
    assert normalize_date(raw) is None  # type: ignore[arg-type]


def test_normalize_date_pads_single_digits():
    assert normalize_date("12月6日") == "12.06"


# ---------------------------------------------------------------------- #
# normalize_text
# ---------------------------------------------------------------------- #
def test_normalize_text_strips_whitespace_and_punctuation():
    assert normalize_text("【许嵩】 演唱会") == "许嵩演唱会"
    assert normalize_text("许嵩·演唱会") == normalize_text("许嵩 演唱会")


def test_normalize_text_lowercases():
    assert normalize_text("Book Now") == "booknow"


@pytest.mark.parametrize("value", ["", None])
def test_normalize_text_empty_input(value: str | None):
    assert normalize_text(value) == ""


# ---------------------------------------------------------------------- #
# city_keyword / find_conflicting_city
# ---------------------------------------------------------------------- #
def test_city_keyword_drops_admin_suffix():
    assert city_keyword("福州市") == "福州"
    assert city_keyword("呼和浩特市") == "呼和浩特"
    assert city_keyword(None) == ""


def test_conflicting_city_detected():
    """标题里写着别的城市 → 返回那个城市，调用方据此否决候选。"""
    assert find_conflicting_city("许嵩演唱会 上海站", "福州") == "上海"


def test_target_city_present_is_not_a_conflict():
    assert find_conflicting_city("许嵩演唱会 福州站", "福州") is None


def test_multi_city_billing_is_not_a_conflict():
    """「北京·上海联演」这类文案不该被目标城市以外的城市误伤。"""
    assert find_conflicting_city("北京·福州联合演出", "福州") is None


@pytest.mark.parametrize(("text", "target"), [("", "福州"), ("许嵩演唱会", ""), (None, "福州")])
def test_conflicting_city_empty_input(text: str | None, target: str | None):
    assert find_conflicting_city(text, target) is None


def test_known_city_tokens_are_normalized_and_unique():
    assert len(set(KNOWN_CITY_TOKENS)) == len(KNOWN_CITY_TOKENS)
    assert all(city == city.strip() for city in KNOWN_CITY_TOKENS)


# ---------------------------------------------------------------------- #
# title_similarity
# ---------------------------------------------------------------------- #
def test_similarity_identical_is_one():
    assert title_similarity("许嵩 演唱会", "许嵩 演唱会") == 1.0


def test_similarity_substring_is_one():
    assert title_similarity("许嵩演唱会", "许嵩演唱会 福州站 2026") == 1.0


def test_similarity_is_symmetric_for_substring():
    assert title_similarity("许嵩演唱会 福州站 2026", "许嵩演唱会") == 1.0


def test_similarity_unrelated_is_not_high():
    """不同演出的相似度不该接近 1。

    注意「演唱会」这个共同后缀会把分数抬到 0.47 左右 —— 这正是导航层除了
    相似度还要用城市冲突、年份冲突做否决的原因（单看相似度会误判）。
    """
    score = title_similarity("周杰伦 演唱会", "许嵩 演唱会")
    assert score < 0.6
    assert score > 0.0


def test_similarity_word_order_swapped_still_high():
    """同事件词序颠倒：官方标题常把城市/年份换个位置。"""
    score = title_similarity("许嵩 呼吸之野 演唱会", "呼吸之野 许嵩 演唱会")
    assert score > 0.6


def test_similarity_truncated_title_is_high():
    score = title_similarity("许嵩「呼吸之野」巡回演唱会", "许嵩「呼吸之野」巡回演唱会 福州站")
    assert score == 1.0


@pytest.mark.parametrize(("a", "b"), [("", "许嵩"), ("许嵩", ""), (None, "许嵩")])
def test_similarity_empty_input(a: str | None, b: str | None):
    assert title_similarity(a, b) == 0.0  # type: ignore[arg-type]
