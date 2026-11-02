"""文本与日期归一化：大麦标题匹配、城市冲突判定、场次日期解析的纯函数底座。

集中放在 core 下（而不是塞进某个页面对象）的理由：这几个函数被搜索页、详情页、
流程层同时用到，且全部无副作用，是最适合单测的一层。

标题匹配为什么要做模糊：大麦同一演出在不同页面写成「【】许嵩 演唱会」「许嵩·演唱会」
「许嵩巡回演唱会-福州站」三种形态，精确比较必然失败；但模糊又会把「周杰伦 演唱会」
误判成「许嵩 演唱会」（共同后缀「演唱会」会把相似度抬到 0.47），所以相似度只作为
加分项，真正的否决靠城市/年份这类**结构化证据**。
"""

from __future__ import annotations

import re
from collections import Counter

__all__ = [
    "KNOWN_CITY_TOKENS",
    "city_keyword",
    "find_conflicting_city",
    "normalize_date",
    "normalize_text",
    "title_similarity",
]


# 已知城市全集：用于「标题里出现了别的城市」时否决候选（错城市 veto）
KNOWN_CITY_TOKENS: tuple[str, ...] = (
    "北京",
    "上海",
    "深圳",
    "广州",
    "杭州",
    "成都",
    "重庆",
    "武汉",
    "南京",
    "西安",
    "苏州",
    "天津",
    "长沙",
    "郑州",
    "青岛",
    "宁波",
    "福州",
    "厦门",
    "南昌",
    "沈阳",
    "大连",
    "合肥",
    "无锡",
    "佛山",
    "东莞",
    "珠海",
    "昆明",
    "贵阳",
    "南宁",
    "长春",
    "哈尔滨",
    "太原",
    "石家庄",
    "济南",
    "兰州",
    "海口",
    "三亚",
    "乌鲁木齐",
    "呼和浩特",
)


def normalize_text(text: str | None) -> str:
    """宽松归一：去空白与常见分隔符，转小写。

    大麦同一演出在不同页面会写成「【】许嵩 演唱会」「许嵩·演唱会」，
    直接比较必然失败，故先抹平空白与标点。
    """
    if not text:
        return ""
    normalized = re.sub(r"[\s\u00a0]+", "", text)
    normalized = re.sub(r"[【】\[\]（）()·•,:：\-—_~]+", "", normalized)
    return normalized.lower()


def city_keyword(city_name: str | None) -> str:
    """去掉城市名尾部的行政级别后缀（``福州市`` → ``福州``）。"""
    if not city_name:
        return ""
    return re.sub(r"(特别行政区|自治州|地区|盟|市)$", "", city_name.strip())


def _char_bigram_counts(text: str) -> Counter[str]:
    """字符 bigram 多重集（重复字符按次数计）。"""
    return Counter(text[i : i + 2] for i in range(len(text) - 1))


def title_similarity(candidate: str, title: str) -> float:
    """候选串与标题的模糊相似度，取值 ``0.0``~``1.0``。

    规则：
    1. 双方归一化后任一为空 → ``0.0``；
    2. 互为连续子串 → ``1.0``（与精确匹配语义一致）；
    3. 否则 ``0.5 * 字符覆盖率 + 0.5 * 字符 bigram Dice 系数``（均按 multiset）。

    校准参考：同事件词序颠倒 0.870、官方词序变体 0.823、省略号截断 0.854、
    无关演出 ≤ 0.31。
    """
    if not isinstance(candidate, str) or not isinstance(title, str):
        return 0.0

    normalized_candidate = normalize_text(candidate)
    normalized_title = normalize_text(title)
    if not normalized_candidate or not normalized_title:
        return 0.0
    if normalized_candidate in normalized_title or normalized_title in normalized_candidate:
        return 1.0

    title_chars = Counter(normalized_title)
    covered = sum(
        min(count, title_chars.get(char, 0)) for char, count in Counter(normalized_candidate).items()
    )
    coverage = covered / len(normalized_candidate)

    candidate_bigrams = _char_bigram_counts(normalized_candidate)
    title_bigrams = _char_bigram_counts(normalized_title)
    overlap = sum(
        min(count, title_bigrams.get(bigram, 0)) for bigram, count in candidate_bigrams.items()
    )
    total = sum(candidate_bigrams.values()) + sum(title_bigrams.values())
    dice = (2 * overlap / total) if total else 0.0

    return 0.5 * coverage + 0.5 * dice


def find_conflicting_city(text: str | None, target_city: str | None) -> str | None:
    """返回 ``text`` 里第一个不等于 ``target_city`` 的已知城市。

    ``target_city`` 自身也出现在 ``text`` 中时返回 ``None`` —— 避免
    「北京·上海联演」这类多城市文案被误伤。任一入参为空则返回 ``None``。
    """
    if not isinstance(text, str) or not isinstance(target_city, str):
        return None

    normalized_text_value = normalize_text(text)
    normalized_target = normalize_text(city_keyword(target_city))
    if not normalized_text_value or not normalized_target:
        return None
    if normalized_target in normalized_text_value:
        return None

    for city in KNOWN_CITY_TOKENS:
        normalized_city = normalize_text(city)
        if normalized_city == normalized_target:
            continue
        if normalized_city in normalized_text_value:
            return city
    return None


#: 场次日期解析优先级：先认中文「x月x日」，再认带年份的完整日期，最后认裸 MM.DD
_DATE_PATTERNS = (
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

    for pattern in _DATE_PATTERNS:
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
