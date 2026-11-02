"""确认页观演人组件：勾选指定观演人并复核。

两个必须踩过的坑：

1. **``checked`` 属性会骗人**。点完勾选框立刻读属性经常还是 0（大麦的勾选态
   刷新有几百毫秒延迟），但界面已经勾上了。反之也有「属性是 true 但实际没勾」。
   所以判断依据是**选中数变多了**，不是单看某个盒子的属性；读到 0 时再 dump 一次
   用 XML 复核。

2. **姓名和勾选框隔着好几层**。姓名在 ``text_name`` 的 TextView 上，勾选框在
   旁边兄弟子树里，两者没有直接的父子关系。只能从 checkbox 往上找几层容器，
   在容器里找姓名节点。

另外，观演人区块有时是折叠的（要先点「选择观演人」展开）。这里不主动展开 ——
展开动作交给流程层，因为它属于页面导航而不是「勾人」。
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

from core.android import ui_tree
from core.logger import get_logger, log_event
from data.constant.damai import ATTENDEE_CHECKBOX_ID, ATTENDEE_NAME_ID, ATTENDEE_REQUIRED_RE
from data.entity.damai import AttendeeRow
from pages.base_damai import DamaiBasePage

logger = get_logger(__name__)

_CHECKED_ATTR_RE = re.compile(r'\bchecked="(true|false)"')

#: 从 checkbox 往上找姓名的层数上限。实测 3 层足够，留 5 层容错。
_NAME_SEARCH_DEPTH = 5


class AttendeeNotSelectedError(RuntimeError):
    """观演人没能勾上。"""


# --------------------------------------------------------------------------- #
# 解析（纯函数）
# --------------------------------------------------------------------------- #
def _name_near(parent_map: dict[int, ET.Element], box: ET.Element) -> str:
    """从勾选框往上找最近的姓名文案。"""
    current: ET.Element | None = box
    for _ in range(_NAME_SEARCH_DEPTH):
        if current is None:
            return ""
        for node in ui_tree.find_by_resource_id(current, ATTENDEE_NAME_ID):
            text = ui_tree.node_text(node)
            if text:
                return text
        current = parent_map.get(id(current))
    return ""


def _is_checked(box: ET.Element) -> bool:
    return (box.get("checked") or "").lower() == "true"


def parse_attendees(root: ET.Element | None) -> list[AttendeeRow]:
    """枚举确认页上的全部观演人行。"""
    if root is None:
        return []
    parent_map = ui_tree.build_parent_map(root)
    rows: list[AttendeeRow] = []
    for box in ui_tree.find_by_resource_id(root, ATTENDEE_CHECKBOX_ID):
        name = _name_near(parent_map, box)
        if not name:
            # 没有姓名的勾选框是「全选」之类的操作项，不是真人
            continue
        rows.append(
            AttendeeRow(
                name=name,
                checked=_is_checked(box),
                coords=ui_tree.node_center(box),
            )
        )
    return rows


def count_checked(root: ET.Element | None) -> int:
    """当前已勾选数量（以 XML 的 ``checked`` 属性为准）。"""
    return sum(1 for row in parse_attendees(root) if row.checked)


def required_count(root: ET.Element | None) -> int:
    """页面要求选几位观演人（「仅需选择 2 位」），读不到返回 0。"""
    if root is None:
        return 0
    for node in ui_tree.iter_nodes(root):
        text = ui_tree.node_text(node)
        if not text:
            continue
        match = ATTENDEE_REQUIRED_RE.search(text)
        if match:
            return int(match.group(1))
    return 0


# --------------------------------------------------------------------------- #
# 页面组件
# --------------------------------------------------------------------------- #
class DamaiAttendeePanel(DamaiBasePage):
    """确认页的观演人区块。"""

    name = "damai_attendee"

    def rows(self) -> list[AttendeeRow]:
        return parse_attendees(self.xml_root(ttl=0.2))

    def names(self) -> list[str]:
        return [row.name for row in self.rows()]

    def selected_count(self) -> int:
        """已勾选人数。属性读到 0 时强制重新 dump 复核一次。

        为什么需要复核：勾选后的属性刷新有延迟，刚点完就读会稳定拿到 0，
        导致上层误判「没勾上」而重复点击 —— 重复点会把已经勾上的取消掉。
        """
        root = self.xml_root(ttl=0.2)
        count = count_checked(root)
        if count > 0:
            return count
        return count_checked(self.refresh_xml())

    def required(self) -> int:
        return required_count(self.xml_root(ttl=0.2))

    def tap_row(self, row: AttendeeRow) -> bool:
        """点一个观演人的勾选框。"""
        if row.coords is None:
            logger.warning("观演人 %r 没有可点坐标", row.name)
            return False
        logger.info("勾选观演人 %r @ %s", row.name, row.coords)
        self.dev.tap_xy(*row.coords)
        self.dev.invalidate_ui()
        return True

    def select_by_name(self, name: str) -> bool:
        """按姓名勾选（已勾选的不会重复点）。"""
        rows = self.rows()
        exact = [row for row in rows if row.name == name]
        candidates = exact or [row for row in rows if name and name in row.name]
        if not candidates:
            log_event(
                logger, "attendee_not_found", level=logging.WARNING,
                target=name, available=[row.name for row in rows],
            )
            return False
        row = candidates[0]
        if row.checked:
            logger.info("观演人 %r 已是勾选状态", row.name)
            return True
        return self.tap_row(row)

    def ensure_selected(self, names: list[str]) -> list[str]:
        """确保配置里的观演人都被勾上，返回实际勾上的姓名列表。

        逐个勾，每勾一个就复核一次总数 —— 大麦在多选场景下会把超出数量的
        勾自动去掉，一次性连点会互相顶掉。

        Raises:
            AttendeeNotSelectedError: 有姓名找不到或勾不上。
        """
        wanted = [name for name in names if name]
        if not wanted:
            raise AttendeeNotSelectedError("配置里没有任何观演人姓名")

        missing: list[str] = []
        for name in wanted:
            before = self.selected_count()
            if not self.select_by_name(name):
                missing.append(name)
                continue
            # 等勾选态落定，最多 1.5s（大麦刷新有延迟）
            for _ in range(15):
                if self.selected_count() > before:
                    break
                self.dev.wait_short("等勾选态刷新")
            else:
                # 复核不到增长 —— 可能本来就勾着（before 已含它），不算失败
                if name not in {row.name for row in self.rows() if row.checked}:
                    missing.append(name)

        if missing:
            raise AttendeeNotSelectedError(
                f"观演人勾选失败: {missing}（页面上有 {self.names()}）"
            )

        selected = [row.name for row in self.rows() if row.checked]
        log_event(logger, "attendee_selected", requested=wanted, selected=selected)
        return selected

    def verify(self, names: list[str]) -> bool:
        """只检查不操作：配置里的观演人是否都已勾上。"""
        checked = {row.name for row in self.rows() if row.checked}
        return all(
            name in checked or any(name in item for item in checked) for name in names if name
        )
