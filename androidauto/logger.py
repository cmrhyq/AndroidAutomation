"""统一日志：控制台 + 文件双输出，UTF-8 安全（兼容 Windows GBK 控制台）。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import LOG_DIR

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
