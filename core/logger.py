"""统一日志：控制台 + 文件双输出，UTF-8 安全（兼容 Windows GBK 控制台）。

另提供结构化事件日志 ``log_event()``：抢票等需要事后按字段检索的场景，
用单行 ``event=xxx k=v`` 取代自由文本，便于 grep 与机器解析。
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from pathlib import Path

from core.config.config import LOG_DIR

_CONFIGURED = False
_FMT = "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s"
_DATEFMT = "%H:%M:%S"


def _ensure_stdout_utf8() -> None:
    """Windows 默认 GBK 控制台打印中文/emoji 会抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def setup_logging(level: int = logging.INFO, log_file: str | Path | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _ensure_stdout_utf8()

    root = logging.getLogger("androidauto")
    root.setLevel(level)
    root.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    root.addHandler(console)

    if log_file is None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_file = LOG_DIR / "androidauto.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    root.addHandler(file_handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"androidauto.{name}")


# --------------------------------------------------------------------------- #
# 结构化事件日志
# --------------------------------------------------------------------------- #
def format_event_value(value: object) -> str:
    """把字段值渲染成单行无损形式（无空白、引号安全）。

    - ``None`` → ``none``，布尔 → ``true``/``false``，数字原样
    - 其余转字符串后折叠所有空白（保证一行仍是 ``k=v`` 序列）
    - 含空格 / ``=`` / 引号时加双引号并转义反斜杠与引号
    """
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = " ".join(str(value).split())
    if not text:
        return '""'
    if any(c in text for c in (" ", "\t", "=", '"')):
        text = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{text}"'
    return text


def format_event(event: str, fields: Mapping[str, object] | None = None) -> str:
    """拼出 ``event=<name> k1=v1 k2=v2`` 单行文本。"""
    parts = [f"event={format_event_value(event)}"]
    for key, value in (fields or {}).items():
        parts.append(f"{key}={format_event_value(value)}")
    return " ".join(parts)


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """输出一行结构化事件日志。

    形如 ``event=sale_ready cta_text="立即购票" polls=12 duration_ms=480``。
    含空白的值会加引号，保证整行可用 ``split(" ")`` 还原成键值对。
    """
    logger.log(level, format_event(event, fields))
