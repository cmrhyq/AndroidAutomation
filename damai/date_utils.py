"""日期归一化：``4月6号`` / ``4/6`` / ``2026-04-06`` 统一成 ``MM.DD``。

大麦 UI 上显示的场次日期是 ``04.06``，而用户手写的配置五花八门。
全链路只认 ``MM.DD`` 一种格式，归一在配置加载时一次完成。
"""

from __future__ import annotations

import re

__all__ = ["normalize_date"]

_PATTERNS = (
    # 4月6号 / 4 月 6 日 / 04月06日
    re.compile(r"(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*[号日好]?"),
    # 2026-04-06 / 2026/04/06 / 2026.04.06
    re.compile(r"\d{4}[./-](?P<m>\d{1,2})[./-](?P<d>\d{1,2})"),
    # 04-06 / 4/6 / 04.06
    re.compile(r"(?P<m>\d{1,2})[./-](?P<d>\d{1,2})"),
)


def normalize_date(raw: str | None) -> str | None:
    """统一为 ``MM.DD``；无法解析返回 ``None``。"""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None

    for pattern in _PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            month = int(match.group("m"))
            day = int(match.group("d"))
        except (TypeError, ValueError):
            continue
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month:02d}.{day:02d}"
    return None
