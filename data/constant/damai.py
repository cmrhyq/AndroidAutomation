"""大麦 App 常量：包名、Activity、resource-id、按钮文案、执行模式与退出码。

页面对象不写死文案与 id，一律从这里取；真机校准时只改这一个文件。
真机校准（大麦改版）时通常只动本文件 —— 例如 9.0.2x 把购票按钮从
``btn_buy_view`` 换成 Canvas 自绘的底部购买栏，改一处即全链路生效。

文案池语义要小心区分：
- :data:`SAFE_TEXTS`    开售后才出现、点了能进购票流程的入口
- :data:`BLOCKED_TEXTS` 开售前的预约/预售入口，误点会进入完全不同的流程

真机基线：大麦 9.0.37 / PFGM00 / Android 12（2026-11 校准）。
"""

from __future__ import annotations

import re
from enum import Enum

__all__ = [
    "ATTENDEE_CHECKBOX_ID",
    "ATTENDEE_NAME_ID",
    "ATTENDEE_REQUIRED_RE",
    "BLOCKED_TEXTS",
    "BUY_BAR_CONTAINER_ID",
    "BUY_BUTTON_ID",
    "BUY_BUTTON_RESOURCE_IDS",
    "CLICK_SCORE_THRESHOLD",
    "CONFIRM_SUBMIT_TEXTS",
    "CTA_BLOCKED_KEYWORDS",
    "CTA_READY_KEYWORDS",
    "DAMAI_MAIN_ACTIVITY",
    "DAMAI_PACKAGE",
    "DETAIL_TITLE_IDS",
    "DETAIL_TITLE_PART_IDS",
    "DETAIL_VENUE_IDS",
    "EXIT_CONFIG_OR_DEVICE_ERROR",
    "EXIT_INTERRUPTED",
    "EXIT_LOCK_CONFLICT",
    "EXIT_RETRIES_EXHAUSTED",
    "EXIT_SUCCESS",
    "EXIT_TERMINAL_FAILURE",
    "GrabMode",
    "HOME_READY_RESOURCE_IDS",
    "POPUP_CLOSE_TEXTS",
    "PRICE_CONTAINER_IDS",
    "PRICE_UNIT_TEXTS",
    "SAFE_TEXTS",
    "SALE_READY_TEXTS",
    "SEARCH_INPUT_CLEAR_ID",
    "SEARCH_INPUT_RESOURCE_IDS",
    "SEARCH_RESULT_CITY_ID",
    "SEARCH_RESULT_RESOURCE_ID",
    "SEARCH_RESULT_TIME_ID",
    "SEARCH_RESULT_TITLE_ID",
    "SEARCH_RESULT_VENUE_ID",
    "SESSION_DATE_RESOURCE_ID",
    "SESSION_PANEL_RESOURCE_ID",
    "SOLD_OUT_RE",
    "SIMILARITY_BONUS_MIN",
    "TERMINAL_REASONS",
    "TITLE_FUZZY_THRESHOLD",
    "UNAVAILABLE_PRICE_TAGS",
    "YEAR_PATTERN",
]


# --------------------------------------------------------------------------- #
# 应用标识
# --------------------------------------------------------------------------- #
DAMAI_PACKAGE = "cn.damai"
DAMAI_MAIN_ACTIVITY = ".launcher.splash.SplashMainActivity"


# --------------------------------------------------------------------------- #
# 执行模式 —— 抢票链路唯一的安全闸门
# --------------------------------------------------------------------------- #
class GrabMode(str, Enum):
    """这一轮到底走多远。

    模式**只由命令行决定**，不写进配置文件。源项目把 ``probe_only`` /
    ``if_commit_order`` 写回 jsonc，于是「上次跑完停在 submit，下次定时任务
    直接下单」成了真实事故源。这里不可变、不落盘，读旧字段会被告知已废弃。

    继承 ``str`` 以便和 ``"probe"`` 直接比较、也方便日志序列化。
    """

    PROBE = "probe"
    VALIDATION = "validation"
    SUBMIT = "submit"

    @property
    def label(self) -> str:
        return {
            GrabMode.PROBE: "安全探测",
            GrabMode.VALIDATION: "开发验证",
            GrabMode.SUBMIT: "正式抢票",
        }[self]

    @property
    def description(self) -> str:
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
        """是否会点「立即提交」。"""
        return self is GrabMode.SUBMIT

    @classmethod
    def from_flags(cls, probe_only: bool, if_commit_order: bool) -> GrabMode:
        """由旧的两个布尔反推模式，供读旧配置或测试用。"""
        if probe_only:
            return cls.PROBE
        return cls.SUBMIT if if_commit_order else cls.VALIDATION


# --------------------------------------------------------------------------- #
# 退出码契约 —— 定时任务/CI 唯一可靠的判据
# --------------------------------------------------------------------------- #
EXIT_SUCCESS = 0
EXIT_RETRIES_EXHAUSTED = 10
#: 不可重试的终态。``submit_unverified``（已提交但验不到结果）尤其关键：
#: 宁可停下让人工检查，也绝不能重试造成重复下单。
EXIT_TERMINAL_FAILURE = 11
EXIT_CONFIG_OR_DEVICE_ERROR = 12
EXIT_LOCK_CONFLICT = 13
EXIT_INTERRUPTED = 130

#: 出现即停止重试的终态原因。
TERMINAL_REASONS: frozenset[str] = frozenset(
    {
        "submit_unverified",
        "sold_out",
        "reservation_only",
        "session_not_found",
        "attendee_unselected",
        "session_invalid",
        "captcha",
    }
)


# --------------------------------------------------------------------------- #
# 购票 CTA 文案
# --------------------------------------------------------------------------- #
#: 开售后才出现、可以安全点击的购票文案（**精确相等**比对，见 BuyBarGuard）。
SAFE_TEXTS: frozenset[str] = frozenset(
    {
        "立即购买",
        "立即购票",
        "立即抢票",
        "立即预定",
        "立即预订",  # 大麦 2026-04 后新增
        "Book Now",  # 国际化兜底
        "选座购买",
        "购买",
        "抢票",
        "预定",
    }
)

#: 开售前的预约/预售文案 —— 点了会进预约流程，必须拒绝。
BLOCKED_TEXTS: frozenset[str] = frozenset(
    {
        "预约抢票",
        "预约",
        "预售",
        "即将开抢",
        "待开售",
        "未开售",
        "提交抢票预约",
    }
)

#: 详情页「可以买」的文案族（宽松包含，用于开售等待的兜底信号）。
SALE_READY_TEXTS: tuple[str, ...] = (
    "立即购票",
    "立即预定",
    "立即预订",
    "立即抢票",
    "Book Now",
)

#: 判定「已进入可购票状态」的宽松关键字（含已进 SKU / 确认页的情形）。
CTA_READY_KEYWORDS: tuple[str, ...] = (
    *SALE_READY_TEXTS,
    "立即购买",
    "选座购买",
    "购买",
    "抢票",
    "预定",
    "提交订单",
    "去结算",
    "确定",
)

#: 出现任一即判定「还没开售」。
CTA_BLOCKED_KEYWORDS: tuple[str, ...] = (
    "预约",
    "预售",
    "即将开抢",
    "待开售",
    "未开售",
    "倒计时",
    "无票",
    "售罄",
    "缺货",
)

#: 确认页的提交按钮文案。
CONFIRM_SUBMIT_TEXTS: tuple[str, ...] = (
    "立即提交",
    "提交订单",
    "确认支付",
    "去支付",
)

#: 启动/活动浮层的关闭文案。只收无副作用的关闭动作，
#: 「取消/不同意」这类可能改变业务状态的按钮一律不点。
POPUP_CLOSE_TEXTS: tuple[str, ...] = (
    "跳过",
    "关闭",
    "我知道了",
    "以后再说",
    "下次再说",
    "暂不开启",
    "暂不更新",
)


# --------------------------------------------------------------------------- #
# 购票入口节点
# --------------------------------------------------------------------------- #
#: v8.x 传统购买按钮（带文案，可做安全校验）。
BUY_BUTTON_ID = "cn.damai:id/btn_buy_view"
#: 大麦 ≥9.0.2x 详情页底部购买栏容器 —— CTA 变成 Canvas 自绘，读不到任何文案，
#: 只能靠 bounds 取中心坐标点击。
BUY_BAR_CONTAINER_ID = "cn.damai:id/trade_project_detail_purchase_status_bar_container_fl"

#: 候选顺序：旧 id 在前，v8.x 一次命中；Canvas 版本回退到购买栏容器。
BUY_BUTTON_RESOURCE_IDS: tuple[str, ...] = (BUY_BUTTON_ID, BUY_BAR_CONTAINER_ID)

#: 购买栏区域出现这些字样即认为该票档不可买。
SOLD_OUT_RE = re.compile(r"缺货|售罄|无票")


# --------------------------------------------------------------------------- #
# 详情页：标题与场馆
# --------------------------------------------------------------------------- #
#: 主标题（单行完整标题）。
DETAIL_TITLE_IDS: tuple[str, ...] = ("cn.damai:id/title_tv",)

#: 两段式标题：部分演出把标题拆成「名称」+「城市站」两个 TextView，
#: 只读其中一个会拿到半截标题，导致目标校验失败。
DETAIL_TITLE_PART_IDS: tuple[str, ...] = (
    "cn.damai:id/project_title_tv1",
    "cn.damai:id/project_title_tv2",
)

#: 场馆名，按版本先后尝试。
DETAIL_VENUE_IDS: tuple[str, ...] = (
    "cn.damai:id/venue_name_0",
    "cn.damai:id/tv_project_venueName",
)


# --------------------------------------------------------------------------- #
# 票档面板
# --------------------------------------------------------------------------- #
#: 票价容器候选：详情页与 SKU 页布局不同，都要试。
PRICE_CONTAINER_IDS: tuple[str, ...] = (
    "cn.damai:id/project_detail_perform_price_flowlayout",
    "cn.damai:id/layout_price",
)

#: 拼接票价文案时丢弃的装饰性角标（「惠」「荐」是营销标记，不是票档信息）。
PRICE_UNIT_TEXTS: frozenset[str] = frozenset({"惠", "荐", "热", "可选", "折"})

#: 票档卡片上出现即视为不可选。
#: ``可预约`` / ``预售`` / ``已预约`` 必须在内 —— 它们看着能点，
#: 但点下去进的是预约流程，不是买票。
UNAVAILABLE_PRICE_TAGS: frozenset[str] = frozenset(
    {
        "无票",
        "缺货",
        "缺货登记",
        "售罄",
        "已售罄",
        "不可选",
        "暂不可售",
        "可预约",
        "预售",
        "已预约",
    }
)


# --------------------------------------------------------------------------- #
# 导航：首页 / 搜索 / 场次
# --------------------------------------------------------------------------- #
#: 首页搜索入口（任一存在即认为首页已就绪）。
#: 2026-11 真机 dump 确认：``homepage_header_search`` 与
#: ``pioneer_homepage_header_search_btn`` 都在，``header_search_v2_input`` 不在。
HOME_READY_RESOURCE_IDS: tuple[str, ...] = (
    "cn.damai:id/pioneer_homepage_header_search_btn",
    "cn.damai:id/homepage_header_search",
    "cn.damai:id/homepage_header_search_layout",
)

#: 搜索页输入框，按序尝试（老版本优先，新版本回退到 search_text）。
SEARCH_INPUT_RESOURCE_IDS: tuple[str, ...] = (
    "cn.damai:id/header_search_v2_input",
    "cn.damai:id/search_text",
)
SEARCH_INPUT_CLEAR_ID = "cn.damai:id/header_search_v2_input_delete"

#: 搜索结果列表与卡片字段。
SEARCH_RESULT_RESOURCE_ID = "cn.damai:id/ll_search_item"
SEARCH_RESULT_TITLE_ID = "cn.damai:id/tv_project_name"
SEARCH_RESULT_VENUE_ID = "cn.damai:id/tv_project_venueName"
SEARCH_RESULT_CITY_ID = "cn.damai:id/tv_project_city"
SEARCH_RESULT_TIME_ID = "cn.damai:id/tv_project_time"

#: 多场次面板：日期卡片。
SESSION_PANEL_RESOURCE_ID = "cn.damai:id/sku_panel_dates"
SESSION_DATE_RESOURCE_ID = "cn.damai:id/tv_date"


# --------------------------------------------------------------------------- #
# 搜索结果打分 —— 低于阈值不点
# --------------------------------------------------------------------------- #
#: 点击阈值。分数低于它说明「这只是同城的一个无关演出」。
CLICK_SCORE_THRESHOLD = 60

#: 相似度加分的最低门槛。低于它不给分，避免共同后缀（如「演唱会」）白送分。
SIMILARITY_BONUS_MIN = 0.45

#: 目标标题的模糊匹配门槛。达到即视为「就是这一场」。
#: 校准依据：同事件词序颠倒 0.87、官方词序变体 0.82、省略号截断 0.85，
#: 无关演出 ≤ 0.47 —— 0.75 落在这条明显的间隔带里。
TITLE_FUZZY_THRESHOLD = 0.75

#: 标题/关键词里的四位年份，用于识别「同名跨年巡演」。
YEAR_PATTERN = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


# --------------------------------------------------------------------------- #
# 确认页：观演人
# --------------------------------------------------------------------------- #
ATTENDEE_CHECKBOX_ID = "cn.damai:id/checkbox"
ATTENDEE_NAME_ID = "cn.damai:id/text_name"
#: 「仅需选择 2 位观演人」——确认页会明确写出需要几位
ATTENDEE_REQUIRED_RE = re.compile(r"仅需选择\s*(\d+)\s*位")
