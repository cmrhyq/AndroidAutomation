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
