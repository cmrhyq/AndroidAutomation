"""大麦配置层的离线用例：JSONC 解析、类型校验、占位符防护、指纹稳定性。

全部不碰设备，毫秒级。"""

from __future__ import annotations

from pathlib import Path

import pytest

from damai.config import (
    DEFAULT_CONFIG_PATH,
    DEPRECATED_CONFIG_KEYS,
    KNOWN_CONFIG_KEYS,
    ConfigError,
    GrabConfig,
    config_hash,
    load_jsonc,
    strip_jsonc_comments,
)
from damai.mode import GrabMode

pytestmark = pytest.mark.unit

EXAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "damai" / "config.example.jsonc"


def _config(**overrides: object) -> GrabConfig:
    payload: dict[str, object] = {
        "keyword": "许嵩 演唱会",
        "users": ["黄昱桥"],
        "city": "福州",
        "date": "2026-12-26",
        "price": "看台318",
    }
    payload.update(overrides)
    return GrabConfig(**payload)  # type: ignore[arg-type]


# ---------------------------------------------------------------------- #
# JSONC 解析
# ---------------------------------------------------------------------- #
def test_strip_line_and_block_comments():
    text = '{\n  // 行注释\n  "a": 1, /* 块注释 */\n  "b": 2\n}'
    assert '"a":1' in strip_jsonc_comments(text).replace("\n", "").replace(" ", "")
    assert "注释" not in strip_jsonc_comments(text)


def test_strip_keeps_double_slash_inside_string():
    """字符串值里的 // 不能被当成注释 —— 正则写法在这里必错。"""
    text = '{"url": "http://example.com//x", "n": 1}'
    stripped = strip_jsonc_comments(text)
    assert '"http://example.com//x"' in stripped


def test_strip_drops_trailing_comma():
    assert strip_jsonc_comments('{"a": 1, "b": [1, 2,], }').replace(" ", "").replace("\n", "") == (
        '{"a":1,"b":[1,2]}'
    )


def test_strip_handles_escaped_quote():
    text = '{"a": "he said \\"hi\\" // not comment"}'
    assert "// not comment" in strip_jsonc_comments(text)


def test_load_jsonc_reports_missing_file(tmp_path: Path):
    with pytest.raises(ConfigError, match="配置文件不存在"):
        load_jsonc(tmp_path / "nope.jsonc")


def test_load_jsonc_reports_syntax_error(tmp_path: Path):
    bad = tmp_path / "bad.jsonc"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="格式错误"):
        load_jsonc(bad)


# ---------------------------------------------------------------------- #
# 占位符防护
# ---------------------------------------------------------------------- #
def test_example_template_is_blocked_by_placeholders():
    """模板必须跑不起来 —— 忘了填真实姓名是最常见的事故。"""
    with pytest.raises(ConfigError) as excinfo:
        GrabConfig.load(EXAMPLE_CONFIG)
    message = str(excinfo.value)
    for key in ("users", "city", "date", "price"):
        assert key in message


def test_placeholder_guard_can_be_softened(tmp_path: Path):
    """软模式只放过占位符本身；值本身不合法（如日期）仍会报错。"""
    path = tmp_path / "config.jsonc"
    path.write_text(
        '{"keyword": "许嵩 演唱会", "users": ["你的真实观演人姓名"], "date": "2026-12-26"}',
        encoding="utf-8",
    )
    config = GrabConfig.load(path, strict_placeholders=False)
    assert config.users == ("你的真实观演人姓名",)


def test_softened_placeholders_still_fail_on_bad_values():
    """关掉占位符硬失败后，'场次日期' 仍会因不是合法日期而报错 —— 软模式不是万能放行。"""
    with pytest.raises(ConfigError, match="date"):
        GrabConfig.load(EXAMPLE_CONFIG, strict_placeholders=False)


def test_placeholder_guard_accepts_real_values(tmp_path: Path):
    path = tmp_path / "config.jsonc"
    path.write_text(
        '{"keyword": "许嵩 演唱会", "users": ["张三"], "city": "福州", '
        '"date": "12月26日", "price": "看台318"}',
        encoding="utf-8",
    )
    config = GrabConfig.load(path)
    assert config.users == ("张三",)
    assert config.date == "12.26"


# ---------------------------------------------------------------------- #
# 类型校验
# ---------------------------------------------------------------------- #
def test_empty_users_rejected():
    with pytest.raises(ConfigError, match="users"):
        _config(users=[])


def test_blank_keyword_rejected():
    with pytest.raises(ConfigError, match="keyword"):
        _config(keyword="   ")


def test_negative_price_index_rejected():
    with pytest.raises(ConfigError, match="price_index"):
        _config(price_index=-1)


def test_bool_price_index_rejected():
    with pytest.raises(ConfigError, match="price_index"):
        _config(price_index=True)


def test_unparsable_date_rejected():
    with pytest.raises(ConfigError, match="date"):
        _config(date="下个月")


def test_bad_sell_start_time_rejected():
    with pytest.raises(ConfigError, match="sell_start_time"):
        _config(sell_start_time="明晚八点")


def test_clock_offset_range_enforced():
    with pytest.raises(ConfigError, match="clock_offset_ms"):
        _config(clock_offset_ms=99999)


def test_unknown_and_deprecated_keys_are_not_in_schema():
    """schema 与实际字段不能漂移，且废弃键不能被误当成有效键。"""
    assert not KNOWN_CONFIG_KEYS & set(DEPRECATED_CONFIG_KEYS)
    assert "probe_only" in DEPRECATED_CONFIG_KEYS
    assert "if_commit_order" in DEPRECATED_CONFIG_KEYS
    assert "rush_mode" in DEPRECATED_CONFIG_KEYS


def test_deprecated_mode_flags_do_not_affect_mode(tmp_path: Path):
    """旧配置里残留 probe_only=false 也不能把模式顶成 submit —— 模式只认 CLI。"""
    path = tmp_path / "config.jsonc"
    path.write_text(
        '{"keyword": "许嵩 演唱会", "users": ["张三"], "probe_only": false, '
        '"if_commit_order": true}',
        encoding="utf-8",
    )
    config = GrabConfig.load(path, mode=GrabMode.PROBE)
    assert config.mode is GrabMode.PROBE
    assert config.probe_only is True
    assert config.if_commit_order is False


# ---------------------------------------------------------------------- #
# 模式与指纹
# ---------------------------------------------------------------------- #
def test_frozen_config_cannot_be_mutated():
    config = _config()
    with pytest.raises((AttributeError, TypeError)):
        config.keyword = "别的演出"  # type: ignore[misc]


def test_fingerprint_stable_and_mode_sensitive():
    base = _config()
    assert base.fingerprint() == _config().fingerprint()
    assert base.fingerprint() != base.copy_with(mode=GrabMode.SUBMIT).fingerprint()
    assert base.fingerprint() != base.copy_with(price="看台618").fingerprint()


def test_fingerprint_ignores_serial():
    """序列号是机器差异，不该让「同一份配置」看起来变了两份。"""
    assert _config(serial="A").fingerprint() == _config(serial="B").fingerprint()


def test_config_hash_is_short_and_deterministic():
    assert config_hash({"a": 1, "b": 2}) == config_hash({"b": 2, "a": 1})
    assert len(config_hash({"a": 1})) == 12


def test_default_config_path_points_into_package():
    assert DEFAULT_CONFIG_PATH.name == "config.jsonc"
    assert DEFAULT_CONFIG_PATH.parent.name == "damai"


def test_keywords_dedupes_and_skips_blank():
    config = _config(target_title="许嵩「呼吸之野」巡回演唱会 福州站")
    assert config.keywords() == ("许嵩 演唱会", "许嵩「呼吸之野」巡回演唱会 福州站")
    assert _config(target_title="许嵩 演唱会").keywords() == ("许嵩 演唱会",)
