"""大麦业务数据实体：页面状态、搜索结果卡、场次、票档、观演人与运行结局。

只放数据结构与由其自身的只读派生属性，
不放业务动作。动作在 pages / flows 里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

__all__ = [
    "ACTIVITY_STATE_MAP",
    "AttendeeRow",
    "GrabResult",
    "PageState",
    "PriceCard",
    "SearchCard",
    "SessionCard",
    "state_from_activity",
]


# --------------------------------------------------------------------------- #
# 页面状态
# --------------------------------------------------------------------------- #
class PageState(str, Enum):
    """当前在哪一页。继承 ``str``，让 ``state == "sku_page"`` 这类比较继续成立。"""

    HOMEPAGE = "homepage"
    SEARCH_PAGE = "search_page"
    DETAIL_PAGE = "detail_page"
    SKU_PAGE = "sku_page"
    SESSION_PICKER = "session_picker"
    ORDER_CONFIRM_PAGE = "order_confirm_page"
    CONSENT_DIALOG = "consent_dialog"
    PENDING_ORDER_DIALOG = "pending_order_dialog"
    UNKNOWN = "unknown"


#: Activity 子串 → 页面状态。**顺序有意义**：先匹配具体页，最后才是兜底的
#: ``MainActivity``（大麦的搜索页、个人中心都是从 MainActivity 起的子页面，
#: 把它放前面会把详情页也识别成首页）。
ACTIVITY_STATE_MAP: tuple[tuple[str, str], ...] = (
    ("ProjectDetail", PageState.DETAIL_PAGE.value),
    ("NcovSku", PageState.SKU_PAGE.value),
    ("SearchActivity", PageState.SEARCH_PAGE.value),
    ("MainActivity", PageState.HOMEPAGE.value),
)


def state_from_activity(activity: str | None) -> str | None:
    """按 Activity 子串推断页面状态，推断不出返回 ``None``。

    纯函数，单测覆盖；真实探测还会看元素特征，Activity 只是最先可用的信号。
    """
    if not activity:
        return None
    for token, state in ACTIVITY_STATE_MAP:
        if token in activity:
            return state
    return None


# --------------------------------------------------------------------------- #
# 搜索 / 详情 / 场次
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SearchCard:
    """一张搜索结果卡。

    ``score`` 由页面对象打出来，低于阈值不点。``coords`` 是卡片中心坐标 ——
    点卡片时用坐标而不是元素点击，因为卡片常有多层嵌套，元素点击容易命中外层
    容器（点击落空或跳错页）。
    """

    index: int
    title: str = ""
    venue: str = ""
    city: str = ""
    show_time: str = ""
    coords: tuple[int, int] | None = None
    score: int = 0

    @property
    def clickable(self) -> bool:
        return self.coords is not None


@dataclass(frozen=True)
class SessionCard:
    """一个场次（日期 + 时间）。"""

    index: int
    date_text: str = ""
    #: 归一化后的 ``MM.DD``，解析不出为空串
    date_key: str = ""
    coords: tuple[int, int] | None = None
    #: 卡片上出现「无票/缺货」等标记
    sold_out: bool = False

    @property
    def selectable(self) -> bool:
        return self.coords is not None and not self.sold_out


# --------------------------------------------------------------------------- #
# 票档
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PriceCard:
    """一张可见票档卡片。"""

    index: int
    price_text: str = ""
    coords: tuple[int, int] | None = None
    #: 卡片上的状态角标（如「预售」「无票」），空串表示没有
    tag: str = ""
    raw_texts: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        from data.constant.damai import UNAVAILABLE_PRICE_TAGS

        return bool(self.price_text) and self.coords is not None and self.tag not in (
            UNAVAILABLE_PRICE_TAGS
        )


# --------------------------------------------------------------------------- #
# 观演人
# --------------------------------------------------------------------------- #
@dataclass
class AttendeeRow:
    """确认页上的一行观演人。

    ``checked`` 会被写回 —— 勾选动作要更新它，因为「勾上了没」只能靠复查
    属性来确认（大麦的勾选状态刷新有几百毫秒延迟）。
    """

    name: str
    checked: bool = False
    coords: tuple[int, int] | None = None
    #: 该行是不是「选择观演人」之类的入口而不是真人
    is_action: bool = False

    @property
    def clickable(self) -> bool:
        return self.coords is not None and not self.is_action


# --------------------------------------------------------------------------- #
# 运行结局
# --------------------------------------------------------------------------- #
_TZ_SHANGHAI = timezone(timedelta(hours=8))


@dataclass
class GrabResult:
    """一轮抢票（含重试）的完整结局。

    这个对象是脚本与定时任务的唯一出口：``exit_code`` 决定 shell 怎么做，
    ``terminal_reason`` 决定要不要人工介入，``artifacts`` 指向现场证据。
    """

    success: bool = False
    outcome: str = "unknown"
    exit_code: int = 10
    attempts: int = 0
    duration_ms: int = 0
    terminal_reason: str | None = None
    mode: str = "probe"
    #: 现场取证文件（截图 / UI 层级 / 元数据）
    artifacts: dict[str, str] = field(default_factory=dict)
    summary_path: Path | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "success": self.success,
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "attempts": self.attempts,
            "duration_ms": self.duration_ms,
            "terminal_reason": self.terminal_reason,
            "mode": self.mode,
            "artifacts": dict(self.artifacts),
            "summary_path": str(self.summary_path) if self.summary_path else None,
            "finished_at": datetime.now(tz=_TZ_SHANGHAI).isoformat(timespec="seconds"),
        }
