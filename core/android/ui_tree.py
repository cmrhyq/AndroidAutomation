"""UI 层级 XML 的工具函数：解析、定位节点、取子树文本、算坐标、上溯可点祖先。

为什么单独成模块：大麦详情页带视频/动画，``dump_hierarchy`` 实测 2s 以上，
所以抢票链路里同一份 XML 会被反复查询（数票档、读购买栏文案、找场次日期）。
把这些查询做成吃 ``ET.Element`` 的纯函数，既不会重复 dump，也能离线单测。

不碰设备、不写文件、不读配置 —— 纯函数越好测，越不容易在开售那几秒里出幺蛾子。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

__all__ = [
    "build_parent_map",
    "clickable_ancestor",
    "descendant_texts",
    "find_by_resource_id",
    "find_by_text",
    "is_clickable",
    "iter_nodes",
    "node_bounds",
    "node_center",
    "node_text",
    "parse_bounds",
    "parse_xml",
    "resource_id_of",
]

_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def parse_xml(xml: str | ET.Element | None) -> ET.Element | None:
    """接受 XML 字符串或已解析的根节点，统一返回根节点；拿不到返回 ``None``。"""
    if xml is None:
        return None
    if isinstance(xml, ET.Element):
        return xml
    if not isinstance(xml, str) or not xml.strip():
        return None
    try:
        return ET.fromstring(xml)
    except ET.ParseError:
        # dump 偶尔被截断（页面正在动画），这时宁可返回 None 让上层重试，
        # 也不要抛出去把整轮抢票打断
        return None


def iter_nodes(root: ET.Element | None) -> list[ET.Element]:
    """全部节点（含 root）。"""
    if root is None:
        return []
    return list(root.iter("node"))


# --------------------------------------------------------------------------- #
# 基本属性
# --------------------------------------------------------------------------- #
def resource_id_of(node: ET.Element) -> str:
    return node.get("resource-id", "") or ""


def node_text(node: ET.Element) -> str:
    """节点的 ``text``，没有就退到 ``content-desc``（按钮文案常在 desc 上）。"""
    return (node.get("text") or node.get("content-desc") or "").strip()


def parse_bounds(text: str | None) -> tuple[int, int, int, int] | None:
    """``"[0,0][1080,2400]"`` → ``(0, 0, 1080, 2400)``。"""
    if not isinstance(text, str):
        return None
    match = _BOUNDS_RE.search(text)
    if not match:
        return None
    return tuple(int(value) for value in match.groups())  # type: ignore[return-value]


def node_bounds(node: ET.Element | None) -> tuple[int, int, int, int] | None:
    if node is None:
        return None
    return parse_bounds(node.get("bounds"))


def node_center(node: ET.Element | None) -> tuple[int, int] | None:
    """节点中心坐标；bounds 缺失或退化（宽高为 0）返回 ``None``。"""
    bounds = node_bounds(node)
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    if right <= left or bottom <= top:
        return None
    return (left + right) // 2, (top + bottom) // 2


def is_clickable(node: ET.Element) -> bool:
    return (node.get("clickable") or "").lower() == "true"


# --------------------------------------------------------------------------- #
# 查找
# --------------------------------------------------------------------------- #
def find_by_resource_id(root: ET.Element | None, resource_id: str) -> list[ET.Element]:
    """按 resource-id 精确匹配全部节点（顺序与 XML 阅读顺序一致）。"""
    if not resource_id:
        return []
    return [node for node in iter_nodes(root) if node.get("resource-id") == resource_id]


def find_by_text(
    root: ET.Element | None, text: str, *, contains: bool = False
) -> list[ET.Element]:
    """按文案查找：``contains=False`` 精确相等，``True`` 为包含。"""
    if not isinstance(text, str) or not text:
        return []
    hits: list[ET.Element] = []
    for node in iter_nodes(root):
        value = node_text(node)
        if not value:
            continue
        if (text in value) if contains else (value == text):
            hits.append(node)
    return hits


# --------------------------------------------------------------------------- #
# 子树与祖先
# --------------------------------------------------------------------------- #
def descendant_texts(node: ET.Element | None) -> list[str]:
    """节点自身及其全部后代的文案（去空、保序、去重）。

    去重是必要的：大麦一个按钮常有外层容器 + 内层 TextView 都带同一段文案，
    不去重会让「读到的文案数」虚高，干扰按文案判定状态的逻辑。
    """
    if node is None:
        return []
    seen: set[str] = set()
    texts: list[str] = []
    for item in node.iter("node"):
        value = node_text(item)
        if value and value not in seen:
            seen.add(value)
            texts.append(value)
    return texts


def build_parent_map(root: ET.Element | None) -> dict[int, ET.Element]:
    """子节点 ``id()`` → 父节点。用于向上找可点祖先。

    不用 ``node`` 自身做 key：``ET.Element`` 没有实现 hash，且同名节点会被判等，
    用 ``id()`` 才能唯一区分。
    """
    parent_map: dict[int, ET.Element] = {}
    if root is None:
        return parent_map
    for parent in root.iter():
        for child in parent:
            parent_map[id(child)] = parent
    return parent_map


def clickable_ancestor(
    parent_map: dict[int, ET.Element],
    node: ET.Element | None,
    *,
    max_up: int = 4,
) -> ET.Element | None:
    """从 node 向上最多 ``max_up`` 层找可点祖先，找不到返回 ``None``。

    为什么需要它：大麦的场次日期 ``tv_date`` 自身 ``clickable=false``，
    真正接点击的是外面几层的容器；直接点日期节点会静默无效。
    """
    current = node
    for _ in range(max_up):
        if current is None:
            return None
        parent = parent_map.get(id(current))
        if parent is None:
            return None
        if is_clickable(parent):
            return parent
        current = parent
    return None
