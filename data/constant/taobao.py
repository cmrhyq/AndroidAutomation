"""淘宝 App 常量：包名、默认测试数据、通用文案池。

约定：页面对象里**不要**写死文案字符串，统一从这里取，方便 App 改版时集中替换。

渲染分层（真机实测，PFGM00 / Android 12 / 淘宝 10.x）
-----------------------------------------------------
+----------------+------------------------------------------+------------------+
| 页面           | 渲染方式                                 | 原生选择器可用性 |
+================+==========================================+==================+
| 首页           | 原生                                     | ✅ 完整可用      |
| 搜索输入页     | 原生                                     | ✅ 完整可用      |
| 搜索结果页     | 半原生（框架原生 + 商品卡片 content-desc）| ✅ 可用          |
| 商品详情页     | Weex + 原生底部栏                        | ⚠️ 只有底部栏    |
| SKU 规格浮层   | Weex                                     | ❌ 全无节点      |
| 购物车页       | Weex                                     | ❌ 全无商品节点  |
+----------------+------------------------------------------+------------------+

这决定了测试策略：核心链路的**判定点必须落在原生可读的节点上**
（搜索按钮、结果卡片 desc、详情页购物车角标），Weex 区域只用坐标操作 + 截图留证。
"""

from __future__ import annotations

# 淘宝主包名（用于启动 App 与校验当前前台应用）
TAOBAO_PACKAGE = "com.taobao.taobao"

# 用例默认搜索词，可用环境变量 TAOBAO_KEYWORD 覆盖
DEFAULT_KEYWORD = "蓝牙耳机"

# 首页搜索入口与提交按钮（实测都在 content-desc 上，不是 text）
SEARCH_ENTRY_DESC = "搜索栏"
SEARCH_BUTTON_ID = "searchbtn"

# 首页/浮层常见的「关闭」类文案：清遮罩时按顺序尝试。
# 原则：只收录无副作用的关闭动作，不要收录「取消/不同意」这类可能改变业务状态的按钮。
POPUP_CLOSE_TEXTS: tuple[str, ...] = (
    "跳过",
    "关闭",
    "以后再说",
    "下次再说",
    "我知道了",
    "暂不更新",
    "残忍拒绝",
)

# 底部导航栏文案（实测可点击属性挂在 content-desc 上）
TAB_HOME_TEXTS: tuple[str, ...] = ("首页",)
TAB_CART_TEXTS: tuple[str, ...] = ("购物车",)
TAB_MINE_TEXTS: tuple[str, ...] = ("我的淘宝", "我的")

# 搜索结果页排序栏文案（真实 desc 带 ,未选中 / ,已选中 后缀，用 contains 匹配）
SORT_LABELS: tuple[str, ...] = ("综合", "销量", "全部", "天猫", "店铺", "闪购")

# ==================== 淘金币 ====================
# 推荐页「领淘金币」入口。
#
# 真机实测结构（PFGM00 / Android 12，dump 见 artifacts/pages/calib_1_home.xml）：
#   FrameLayout content-desc="领淘金币" bounds=[452,519][628,703] clickable=false
#     └ FrameLayout 同 desc 同 bounds clickable=true      ← 真正可点的这一层
#         └ View 同 desc bounds=[466,653][614,698]         ← 内部倒计时文案
#
# 三层同名 desc、没有 resource-id，所以只能靠 desc 定位；点击用中心坐标
# （BasePage.tap_center）避免命中不可点击的外层。
#
# ⚠️ 该挂件是**活动位 / AB 测试位**：双 11 期间推荐页可能整块换成会场皮肤，
# 入口会消失（2026-10-31 实测推荐页无此元素）。因此页面对象必须支持
# 「入口不存在」，业务流不能把它当成必现元素。
COIN_ENTRY_DESCS: tuple[str, ...] = ("领淘金币", "淘金币", "领金币")

# 金币中心页（点入口后进入）的就绪判据候选。
# ⚠️ 未真机校准：金币中心多为 Weex/H5 容器，可能整页只有壳。
# 首次运行失败时用 CoinPage.calibrate() 导层级再回来补齐。
COIN_PAGE_READY_DESCS: tuple[str, ...] = ("淘金币", "我的金币", "金币明细", "金币任务")

# 金币中心页的签到按钮（按优先级尝试，「已签到」也算命中，用于判断状态）
COIN_SIGN_TEXTS: tuple[str, ...] = ("签到领金币", "立即签到", "签到", "已签到")

# 金币任务列表里的操作按钮
COIN_TASK_ACTION_TEXTS: tuple[str, ...] = ("去完成", "去逛逛", "立即领取", "领取", "去签到")

# 金币中心页常见的关闭 / 返回类按钮
COIN_CLOSE_TEXTS: tuple[str, ...] = ("关闭", "我知道了", "以后再说", "开心收下")

# 金币余额解析规则：真实文案可能是「1600」「1,600 金币」「金币 1600」
COIN_BALANCE_PATTERNS: tuple[str, ...] = (
    r"([\d][\d,]*)\s*金币",
    r"金币\s*([\d][\d,]*)",
    r"^([\d][\d,]{1,})$",
)

# ==================== 推荐页顶部频道栏 ====================
# 真机实测：频道 tab 的 content-desc 形如「推荐,已选中」「关注,未选中」，
# 选中态直接写在 desc 里 —— 这比读 UI 样式可靠得多，可以直接断言。
FEED_CHANNELS: tuple[str, ...] = (
    "关注",
    "推荐",
    "闪购",
    "双11",
    "国补",
    "飞猪",
    "穿搭",
    "居家",
)
FEED_CHANNEL_SELECTED_HINT = "已选中"
FEED_CHANNEL_UNSELECTED_HINT = "未选中"

# ==================== 推荐页底部领券条 ====================
# 真机实测：领券条容器 desc=popViewIdentifier，内部拆成
# 「您有」「满120减15」「国际加补券待领取」「立即领取」多个 desc 片段。
COUPON_BAR_CONTAINER_DESC = "popViewIdentifier"
COUPON_BAR_HINTS: tuple[str, ...] = ("待领取", "立即领取", "满", "券")
COUPON_BAR_CLAIM_DESCS: tuple[str, ...] = ("立即领取", "领取", "去使用")
COUPON_BAR_CLOSE_DESCS: tuple[str, ...] = ("关闭", "暂不领取", "×")
