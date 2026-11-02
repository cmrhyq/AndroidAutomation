"""三级执行模式 —— 抢票链路的唯一安全闸门。

源项目把 ``probe_only`` / ``if_commit_order`` 两个布尔写回 ``config.jsonc``，
于是「上次跑完留在 submit 模式，下次定时任务直接下单」成为真实事故源。
这里改为**只由命令行决定**、构造后不可变、且配置文件里出现旧字段会被警告移除。

    安全探测（probe）      只检查详情页控件是否就绪，绝不下单
    开发验证（validation） 一路走到确认页并勾完观演人，停在「立即提交」前
    正式抢票（submit）     点击提交，检测到收银台即判成功并停止（不碰支付）
"""

from __future__ import annotations

from enum import Enum

__all__ = ["GrabMode"]


class GrabMode(str, Enum):
    """执行模式。继承 ``str`` 以便与 ``"probe"`` 直接比较、便于日志序列化。"""

    PROBE = "probe"
    VALIDATION = "validation"
    SUBMIT = "submit"

    @property
    def label(self) -> str:
        """面向人的短标签。"""
        return {
            GrabMode.PROBE: "安全探测",
            GrabMode.VALIDATION: "开发验证",
            GrabMode.SUBMIT: "正式抢票",
        }[self]

    @property
    def description(self) -> str:
        """这一轮到底会做什么，一句话说清。"""
        return {
            GrabMode.PROBE: "只检查目标演出详情页，不会点击「立即购票」",
            GrabMode.VALIDATION: "会进入确认页并勾选观演人，但不会点击「立即提交」",
            GrabMode.SUBMIT: "会点击提交订单；检测到收银台即停止，不进行任何支付操作",
        }[self]

    @property
    def clicks_purchase(self) -> bool:
        """是否会去点详情页的购买入口。"""
        return self is not GrabMode.PROBE

    @property
    def submits_order(self) -> bool:
        """是否会点击「立即提交」。"""
        return self is GrabMode.SUBMIT

    @classmethod
    def from_flags(cls, probe_only: bool, if_commit_order: bool) -> GrabMode:
        """由旧的两个布尔反推模式，供读取旧配置/测试使用。"""
        if probe_only:
            return cls.PROBE
        return cls.SUBMIT if if_commit_order else cls.VALIDATION
