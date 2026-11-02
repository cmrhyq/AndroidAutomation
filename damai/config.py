"""抢票配置：JSONC 读取 + 校验 + 占位符防护 + 稳定指纹。

源项目同文件 940 行，其中约 400 行是「保留注释的定点写回」机制 —— 存在的唯一
理由是它会把 ``probe_only`` / ``if_commit_order`` 写回配置文件。本引擎的模式只由
命令行决定（见 :mod:`damai.mode`），写回机制整体不移植，配置因此变成只读。

配置分工：
- 设备序列号 → ``.env`` 的 ``DEVICE_UDID`` 或 ``--serial``
- 执行模式   → 命令行 ``--probe/--validate/--submit``
- 业务意图   → 本模块读取的 ``damai/config.jsonc``
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from damai.date_utils import normalize_date
from damai.mode import GrabMode
from damai.text_utils import normalize_text

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEPRECATED_CONFIG_KEYS",
    "KNOWN_CONFIG_KEYS",
    "PLACEHOLDER_LITERALS",
    "ConfigError",
    "GrabConfig",
    "config_hash",
    "load_jsonc",
    "resolve_config_path",
    "strip_jsonc_comments",
    "validate_payload",
]

logger = logging.getLogger("androidauto.damai.config")

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.jsonc")
CONFIG_OVERRIDE_ENV_VAR = "DAMAI_CONFIG_PATH"

# price_index 过大基本意味着配错了（票档页不会有 50 个可选项）
PRICE_INDEX_LARGE_WARNING_THRESHOLD = 50

# 模板占位符黑名单：strip 后精确全等匹配。
# keyword 的示例值（如「许嵩 演唱会」）是合法搜索词，刻意不列入。
PLACEHOLDER_LITERALS: dict[str, frozenset[str]] = {
    "users": frozenset({"你的真实观演人姓名"}),
    "city": frozenset({"演出城市"}),
    "date": frozenset({"场次日期"}),
    "price": frozenset({"票档原文"}),
    "target_title": frozenset({"演出全名，用于校验没抢错场"}),
    "target_venue": frozenset({"场馆名，可选"}),
}

# 权威 schema：与 GrabConfig 的配置字段一一对应（mode / serial 只来自 CLI 与环境）
KNOWN_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "keyword",
        "target_title",
        "target_venue",
        "users",
        "city",
        "date",
        "price",
        "price_index",
        "app_package",
        "app_activity",
        "sell_start_time",
        "countdown_lead_ms",
        "wait_cta_ready_timeout_ms",
        "fast_retry_count",
        "fast_retry_interval_ms",
        "auto_navigate",
        "clock_offset_ms",
    }
)

DEPRECATED_CONFIG_KEYS: dict[str, str] = {
    "probe_only": "已废弃：执行模式改由命令行 --probe/--validate/--submit 指定",
    "if_commit_order": "已废弃：执行模式改由命令行 --probe/--validate/--submit 指定",
    "rush_mode": "已废弃：极速管道（FastPipeline）未移植，该开关无效",
    "rush_skip_session": "已废弃：极速管道未移植，该开关无效",
    "rush_skip_price_dump": "已废弃：极速管道未移植，该开关无效",
    "rush_aggressive_retry": "已废弃：极速管道未移植，该开关无效",
    "serial": "已废弃：设备序列号请用 .env 的 DEVICE_UDID 或 --serial 指定",
    "udid": "已废弃：设备序列号请用 .env 的 DEVICE_UDID 或 --serial 指定",
    "item_url": "已废弃：请改用 keyword / target_title 配合 auto_navigate",
    "device_name": "Appium 时代字段，u2 模式下无意义",
    "server_url": "Appium 时代字段，u2 模式下无意义",
    "platform_version": "Appium 时代字段，u2 模式下无意义",
    "driver_backend": "Appium 时代字段，u2 模式下无意义",
}


class ConfigError(ValueError):
    """配置加载或校验失败。继承 ``ValueError`` 以保持 ``except ValueError`` 兼容。"""


# --------------------------------------------------------------------------- #
# JSONC 解析
# --------------------------------------------------------------------------- #
def strip_jsonc_comments(text: str) -> str:
    """删除 ``//`` 与 ``/* */`` 注释，并容忍尾随逗号。

    用状态机而非正则：正则写法（如 ``(?<!:)//``）在 ``"地址是 http://x"``
    这类字符串值上会误剥，而配置里出现 URL 是很正常的事。
    """
    out: list[str] = []
    in_string = False
    pending_comma = False
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            if pending_comma:
                out.append(",")
                pending_comma = False
            in_string = True
            out.append(char)
            index += 1
            continue

        if char == "/" and index + 1 < length:
            nxt = text[index + 1]
            if nxt == "/":  # 单行注释
                index += 2
                while index < length and text[index] != "\n":
                    index += 1
                continue
            if nxt == "*":  # 块注释
                index += 2
                while index + 1 < length and not (text[index] == "*" and text[index + 1] == "/"):
                    index += 1
                index += 2
                continue

        if char == ",":
            pending_comma = True
            index += 1
            continue

        if char in "}]":  # 尾随逗号：直接丢弃
            pending_comma = False
            out.append(char)
            index += 1
            continue

        if char.isspace():
            index += 1  # 空白无需输出，也让缓冲中的逗号继续等待
            continue

        if pending_comma:
            out.append(",")
            pending_comma = False
        out.append(char)
        index += 1

    return "".join(out)


def load_jsonc(path: str | Path) -> dict[str, object]:
    """读取 JSONC 文件为 dict。文件缺失或语法错误抛 :class:`ConfigError`。"""
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(
            f"配置文件不存在: {config_path}\n"
            f"→ 先复制模板：cp damai/config.example.jsonc damai/config.jsonc"
        )
    try:
        raw = config_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ConfigError(f"配置文件无法读取: {config_path}（{exc}）") from exc

    try:
        payload = json.loads(strip_jsonc_comments(raw))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件格式错误: {config_path}\n  {exc}") from exc

    if not isinstance(payload, dict):
        raise ConfigError(f"配置根节点必须是 JSON 对象: {config_path}")
    return payload


def resolve_config_path(explicit: str | Path | None = None) -> Path:
    """``--config`` > ``DAMAI_CONFIG_PATH`` > ``damai/config.jsonc``。"""
    if explicit is not None:
        return Path(explicit)
    env_value = os.environ.get(CONFIG_OVERRIDE_ENV_VAR, "").strip()
    if env_value:
        return Path(env_value)
    return DEFAULT_CONFIG_PATH


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #
def _placeholder_violations(payload: Mapping[str, object]) -> list[tuple[str, str]]:
    """返回 ``[(key, 占位符值), ...]``；``users`` 逐元素检查。"""
    hits: list[tuple[str, str]] = []
    for key, literals in PLACEHOLDER_LITERALS.items():
        value = payload.get(key)
        if key == "users" and isinstance(value, (list, tuple)):
            hits.extend(
                (key, item) for item in value if isinstance(item, str) and item.strip() in literals
            )
        elif isinstance(value, str) and value.strip() in literals:
            hits.append((key, value))
    return hits


def unknown_keys(payload: Mapping[str, object]) -> list[str]:
    """既不在 KNOWN 也不在 DEPRECATED 的键；``_`` 前缀视为用户注释键，跳过。"""
    return sorted(
        key
        for key in payload
        if key not in KNOWN_CONFIG_KEYS
        and key not in DEPRECATED_CONFIG_KEYS
        and not key.startswith("_")
    )


def validate_payload(
    payload: Mapping[str, object],
    source: str = "damai/config.jsonc",
    *,
    strict_placeholders: bool = True,
) -> None:
    """启动期防护。

    - 废弃字段 / 未识别字段 → 只警告，永不失败（避免旧配置直接跑不起来）
    - 占位符命中 → 收集全部违规后一次性抛 :class:`ConfigError`
    """
    for key in payload:
        if key in DEPRECATED_CONFIG_KEYS:
            logger.warning("字段 %s %s", key, DEPRECATED_CONFIG_KEYS[key])
    for key in unknown_keys(payload):
        logger.warning("字段 %s 未识别，将被忽略；请对照 damai/config.example.jsonc 检查拼写", key)

    violations = _placeholder_violations(payload)
    if not violations:
        return

    lines = [f"{source} 中仍有模板占位符，尚未填入真实值："]
    lines += [f'  - {key} = "{value}"' for key, value in violations]
    lines += [
        "请编辑 damai/config.jsonc：",
        "  users → 填已在大麦 App「观演人」里添加并保存的真实姓名",
        "  city / date / price → 填大麦 App 演出页面上的原文",
    ]
    message = "\n".join(lines)
    if strict_placeholders:
        raise ConfigError(message)
    logger.warning(message)


def config_hash(payload: Mapping[str, object]) -> str:
    """配置内容的稳定短哈希（12 位），用于失败取证与报告里「这次跑的是哪份配置」。"""
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# 配置对象
# --------------------------------------------------------------------------- #
def _require_str(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} 必须是非空字符串，实际值: {value!r}")
    return value.strip()


def _require_int(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} 必须是整数，实际值: {value!r}")
    if value < minimum:
        raise ConfigError(f"{name} 必须 >= {minimum}，实际值: {value!r}")
    return value


def _coerce_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{name} 必须是布尔值（true/false），实际值: {value!r}")
    return value


def _optional_str(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{name} 必须是字符串或 null，实际值: {value!r}")
    text = value.strip()
    return text or None


@dataclass(frozen=True)
class GrabConfig:
    """一次抢票运行所需的全部输入。构造后不可变。"""

    keyword: str
    users: tuple[str, ...]
    date: str = ""
    price: str = ""
    city: str | None = None
    price_index: int = 0
    app_package: str = "cn.damai"
    app_activity: str = ".launcher.splash.SplashMainActivity"
    sell_start_time: str | None = None
    countdown_lead_ms: int = 3000
    wait_cta_ready_timeout_ms: int = 0
    fast_retry_count: int = 8
    fast_retry_interval_ms: int = 120
    auto_navigate: bool = True
    target_title: str | None = None
    target_venue: str | None = None
    clock_offset_ms: int = 0
    mode: GrabMode = GrabMode.PROBE
    serial: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "keyword", _require_str(self.keyword, "keyword"))

        users = self.users
        if isinstance(users, str):
            users = (users,)
        if not isinstance(users, (list, tuple)) or not users:
            raise ConfigError("users 必须是非空数组，元素为观演人真实姓名")
        cleaned_users: list[str] = []
        for item in users:
            cleaned_users.append(_require_str(item, "users[]"))
        object.__setattr__(self, "users", tuple(cleaned_users))

        raw_date = self.date or ""
        if raw_date.strip():
            normalized = normalize_date(raw_date)
            if normalized is None:
                raise ConfigError(f"date 无法解析为日期（支持 4月6号 / 4/6 / 2026-04-06）: {raw_date!r}")
            object.__setattr__(self, "date", normalized)
        else:
            object.__setattr__(self, "date", "")

        if not isinstance(self.price, str):
            raise ConfigError(f"price 必须是字符串（填票档页原文），实际值: {self.price!r}")
        object.__setattr__(self, "price", self.price.strip())

        object.__setattr__(self, "city", _optional_str(self.city, "city"))
        object.__setattr__(self, "target_title", _optional_str(self.target_title, "target_title"))
        object.__setattr__(self, "target_venue", _optional_str(self.target_venue, "target_venue"))
        object.__setattr__(self, "serial", _optional_str(self.serial, "serial"))
        object.__setattr__(
            self, "app_package", _require_str(self.app_package, "app_package")
        )
        object.__setattr__(self, "app_activity", _require_str(self.app_activity, "app_activity"))

        object.__setattr__(
            self, "price_index", _require_int(self.price_index, "price_index", minimum=0)
        )
        if self.price_index > PRICE_INDEX_LARGE_WARNING_THRESHOLD:
            logger.warning(
                "price_index=%d 偏大（票档页通常不超过 %d 项），请确认是否配错",
                self.price_index,
                PRICE_INDEX_LARGE_WARNING_THRESHOLD,
            )

        for name in (
            "countdown_lead_ms",
            "wait_cta_ready_timeout_ms",
            "fast_retry_count",
            "fast_retry_interval_ms",
        ):
            object.__setattr__(
                self, name, _require_int(getattr(self, name), name, minimum=0)
            )

        if isinstance(self.clock_offset_ms, bool) or not isinstance(self.clock_offset_ms, int):
            raise ConfigError(f"clock_offset_ms 必须是整数，实际值: {self.clock_offset_ms!r}")
        if abs(self.clock_offset_ms) > 5000:
            raise ConfigError(
                f"clock_offset_ms 超出合理范围（±5000ms），实际值: {self.clock_offset_ms}"
            )

        object.__setattr__(self, "auto_navigate", _coerce_bool(self.auto_navigate, "auto_navigate"))

        if self.sell_start_time is not None:
            raw_sell = _optional_str(self.sell_start_time, "sell_start_time")
            if raw_sell is not None:
                try:
                    datetime.fromisoformat(raw_sell)
                except ValueError as exc:
                    raise ConfigError(
                        f"sell_start_time 不是合法 ISO 时间: {raw_sell!r}"
                        f"（示例 2026-04-06T12:00:00+08:00）"
                    ) from exc
            object.__setattr__(self, "sell_start_time", raw_sell)

        if not isinstance(self.mode, GrabMode):
            object.__setattr__(self, "mode", GrabMode(str(self.mode)))

    # -- 兼容访问器：让移植过来的代码里 probe_only / if_commit_order 调用点零改动 --
    @property
    def probe_only(self) -> bool:
        return self.mode is GrabMode.PROBE

    @property
    def if_commit_order(self) -> bool:
        return self.mode is GrabMode.SUBMIT

    @property
    def rush_mode(self) -> bool:
        """是否处于「会真的点购票入口」的抢票状态。

        对应的正是源项目那个独立配置项 ``rush_mode``，但这里不再单独可配：
        既然 PROBE 模式承诺不点购票，那所有「省掉复核、按缓存坐标直点」的
        极速路径就都该跟着模式走，而不是让用户再开一个开关去和模式对赌。
        """
        return self.mode.clicks_purchase

    # -- 序列化 --
    def to_dict(self) -> dict[str, object]:
        """稳定序列化（不含 serial，避免机器差异污染指纹）。"""
        return {
            "keyword": self.keyword,
            "users": list(self.users),
            "date": self.date,
            "price": self.price,
            "city": self.city,
            "price_index": self.price_index,
            "app_package": self.app_package,
            "app_activity": self.app_activity,
            "sell_start_time": self.sell_start_time,
            "countdown_lead_ms": self.countdown_lead_ms,
            "wait_cta_ready_timeout_ms": self.wait_cta_ready_timeout_ms,
            "fast_retry_count": self.fast_retry_count,
            "fast_retry_interval_ms": self.fast_retry_interval_ms,
            "auto_navigate": self.auto_navigate,
            "target_title": self.target_title,
            "target_venue": self.target_venue,
            "clock_offset_ms": self.clock_offset_ms,
            "mode": self.mode.value,
        }

    def fingerprint(self) -> str:
        """配置指纹；改动任一项（含 mode）都会变。"""
        return config_hash(self.to_dict())

    def copy_with(self, **changes: object) -> GrabConfig:
        """返回替换若干字段后的新实例（frozen 语义下唯一改法）。"""
        return replace(self, **changes)

    def keywords(self) -> tuple[str, ...]:
        """搜索候选词：优先 keyword，其次 target_title。"""
        candidates = [self.keyword, self.target_title or ""]
        seen: list[str] = []
        for candidate in candidates:
            text = normalize_text(candidate)
            if text and text not in {normalize_text(item) for item in seen}:
                seen.append(candidate.strip())
        return tuple(seen)

    @classmethod
    def load(
        cls,
        path: str | Path | None = None,
        *,
        mode: GrabMode | None = None,
        serial: str | None = None,
        strict_placeholders: bool = True,
    ) -> GrabConfig:
        """从 JSONC 加载并校验配置。"""
        config_path = resolve_config_path(path)
        payload = load_jsonc(config_path)
        validate_payload(payload, str(config_path), strict_placeholders=strict_placeholders)
        known = {key: value for key, value in payload.items() if key in KNOWN_CONFIG_KEYS}

        if "keyword" not in known:
            raise ConfigError(f"{config_path} 缺少必填字段 keyword（演出搜索关键词）")
        if "users" not in known:
            raise ConfigError(f"{config_path} 缺少必填字段 users（观演人姓名数组）")

        return cls(
            mode=mode or GrabMode.PROBE,
            serial=serial,
            **known,  # type: ignore[arg-type]
        )
