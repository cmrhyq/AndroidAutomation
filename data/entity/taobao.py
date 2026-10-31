"""淘宝业务数据实体：金币页快照、任务行与文案节点。"""

from dataclasses import dataclass, field


@dataclass
class CoinSnapshot:
    """一次遍历得到的金币页状态快照。"""

    #: 金币余额（-1 = 读不到）
    balance: int = -1
    #: 今日是否已签到
    signed: bool = False
    #: 任务名列表
    tasks: list[str] = field(default_factory=list)
    #: 本次遍历拿到的有效文案节点数（0 基本等于「整页是渲染层」）
    text_nodes: int = 0

    @property
    def readable(self) -> bool:
        return self.text_nodes > 0


@dataclass
class TextNode:
    """页面上的一个文案节点（带中心坐标，用于按纵坐标配对）。"""

    text: str
    desc: str
    x: int
    y: int


@dataclass
class TaskRow:
    """金币任务行：任务名 + 操作按钮中心坐标。

    ``action`` 为空表示该行只剩「已完成 / 已领取」文案，没有可点的按钮。
    ``aliases`` 是同一行里除标题外的其它文案（副标题、进度），关键词匹配时一并参与，
    因为「好物随心逛」这类任务名在部分版本里是副标题、标题反倒是「浏览 15 秒得 10 金币」。
    """

    name: str
    action: str = ""
    x: int = 0
    y: int = 0
    #: 行内出现「已完成 / 已领取」等文案
    done: bool = False
    #: 同一行的其它文案（参与关键词匹配，不用于展示）
    aliases: tuple[str, ...] = ()
    #: 任务名尾部进度「(已完成/总数)」，没有后缀时都是 0
    done_count: int = 0
    total_count: int = 0

    @property
    def clickable(self) -> bool:
        return bool(self.action) and not self.done

    @property
    def remaining(self) -> int | None:
        """还差几次做完；页面上没写进度时返回 None。"""
        if self.total_count <= 0:
            return None
        return max(self.total_count - self.done_count, 0)

    def matches(self, keyword: str) -> bool:
        return keyword in self.name or any(keyword in alias for alias in self.aliases)
