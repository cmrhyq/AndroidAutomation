"""三级模式闸门的离线用例 —— 抢票链路上唯一的安全开关。

模式一旦判断错，后果是真实下单，因此真值表逐项锁定。"""

from __future__ import annotations

import pytest

from damai.mode import GrabMode

pytestmark = pytest.mark.unit


def test_mode_is_str_subclass():
    """继承 str，移植过来的代码里 ``config.mode == "sku_page"`` 这类比较才不会断。"""
    assert GrabMode.PROBE == "probe"
    assert GrabMode.VALIDATION == "validation"
    assert GrabMode.SUBMIT == "submit"
    assert GrabMode("probe") is GrabMode.PROBE


@pytest.mark.parametrize(
    ("mode", "clicks", "submits"),
    [
        (GrabMode.PROBE, False, False),
        (GrabMode.VALIDATION, True, False),
        (GrabMode.SUBMIT, True, True),
    ],
)
def test_truth_table(mode: GrabMode, clicks: bool, submits: bool):
    assert mode.clicks_purchase is clicks
    assert mode.submits_order is submits


@pytest.mark.parametrize(
    ("probe_only", "if_commit_order", "expected"),
    [
        (True, False, GrabMode.PROBE),
        (True, True, GrabMode.PROBE),  # probe 优先级最高，绝不下单
        (False, False, GrabMode.VALIDATION),
        (False, True, GrabMode.SUBMIT),
    ],
)
def test_from_flags(probe_only: bool, if_commit_order: bool, expected: GrabMode):
    assert GrabMode.from_flags(probe_only, if_commit_order) is expected


def test_only_submit_submits():
    """冗余保险：任何新增模式都不该意外获得提交权。"""
    assert [m.value for m in GrabMode if m.submits_order] == ["submit"]


def test_labels_are_non_empty():
    for mode in GrabMode:
        assert mode.label
        assert mode.description


def test_submit_description_warns_about_payment():
    """提交模式的说明必须点明「不碰支付」，避免用户以为会自动付款。"""
    assert "支付" in GrabMode.SUBMIT.description


def test_probe_description_states_no_purchase_click():
    assert "不会点击" in GrabMode.PROBE.description
