"""脚本公共引导：保证从任意目录运行都能 import 到项目包。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def bootstrap(level: str = "INFO") -> None:
    """注入 sys.path + 初始化日志（含 Windows UTF-8 控制台修正）。"""
    import logging

    from androidauto.logger import setup_logging

    setup_logging(getattr(logging, level.upper(), logging.INFO))


def hr(char: str = "-", width: int = 78) -> str:
    return char * width
