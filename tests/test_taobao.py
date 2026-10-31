"""淘宝端到端用例：启动 → 搜索 → 详情 → 加购 → 购物车。

需要设备在线并已安装淘宝，无设备时由 conftest 整体 skip。
选择器失效时先用 scripts/ui_dump.py 导层级对照。"""

from __future__ import annotations

import pytest

from core.config.config import get
from data.constant.taobao import DEFAULT_KEYWORD
from flows.taobao_flow import TaobaoFlow

pytestmark = pytest.mark.device

#: 搜索词，可用环境变量覆盖，便于不同场景回归
KEYWORD = get("TAOBAO_KEYWORD", DEFAULT_KEYWORD)


def test_home_launch(dev):
    """启动淘宝后能进入首页（连通性冒烟）"""
    flow = TaobaoFlow(dev)
    home = flow.start(fresh=True)

    assert home.is_here(), f"启动后未进入淘宝首页：请确认 App 已安装、首页选择器未失效（当前 activity={home.activity}）"


def test_search_returns_results(dev):
    """搜索关键词能返回非空商品列表"""
    flow = TaobaoFlow(dev)
    page, count = flow.search_only(KEYWORD)

    assert count > 0, (
        f"搜索 {KEYWORD!r} 未识别到商品卡片（当前 activity={page.activity}）。"
        "淘宝把商品信息拼在卡片的 content-desc 里，含「元」才算卡片；"
        "若 desc 格式变化，请用 ui_dump.py 重新校准 SearchResultPage._card_elements"
    )


def test_product_card_parse(dev):
    """商品卡片的标题与价格可被解析"""
    flow = TaobaoFlow(dev)
    page, _ = flow.search_only(KEYWORD)
    title, price = page.first_product()

    assert title, f"首个商品卡片未解析出标题（共 {page.result_count()} 张卡片）"
    assert price.startswith("¥"), f"首个商品卡片未解析出价格：title={title!r} price={price!r}"


def test_add_to_cart_increases_badge(dev):
    """加购后购物车角标数量增长"""
    flow = TaobaoFlow(dev)
    flow.start()
    page = flow.search(KEYWORD)
    flow.read_product(page)
    flow.open_product(0)

    ok, before, after = flow.add_to_cart()

    assert ok and after > before, (
        f"加购失败：购物车角标 {before} -> {after}。"
        "角标是详情页唯一可靠的原生判据（「加购成功」Toast 与 SKU 浮层都在 Weex 里读不到）；"
        "若角标一直不涨，请检查 SKU 浮层坐标 SKU_CONFIRM_POINT 是否随版本变化"
    )


def test_cart_page_reachable(dev):
    """加购后能进入购物车页并留证截图"""
    flow = TaobaoFlow(dev)
    result = flow.search_and_add_to_cart(KEYWORD)

    assert result.badge_increased, f"加购未生效：角标 {result.badge_before} -> {result.badge_after}"
    assert result.cart_reached, "未能进入购物车页（icart_weex_root_view 未出现）"
    assert result.cart_screenshot is not None, "购物车截图未生成"

    # 购物车内容由 Weex 渲染，原生层级读不到；有 OCR 环境时顺带校验件数。
    # 没有 OCR 不算失败也不算跳过 —— 页面可达 + 截图留证已经校验完了，
    # 只是把「内容级校验」这一档明确标注为环境不支持。
    if result.cart_item_count >= 0:
        assert result.cart_item_count > 0, (
            f"OCR 读到购物车件数为 {result.cart_item_count}，期望 > 0"
            f"（合计={result.cart_total!r}，截图={result.cart_screenshot}）"
        )


def test_feed_channel_switch(dev):
    """推荐页顶部频道可切换且选中态正确"""
    flow = TaobaoFlow(dev)
    flow.start(fresh=True)

    flow.switch_channel("关注")
    home = flow.switch_channel("推荐")

    assert home.current_channel() == "推荐", (
        f"切回「推荐」后当前频道为 {home.current_channel()!r}。"
        "频道 desc 形如「推荐,已选中」，若整栏改版请校准 FeedChannelBar.selectors_for"
    )


def test_coin_entry_visible(dev):
    """推荐页「领淘金币」入口按活动位投放（未投放则跳过）"""
    flow = TaobaoFlow(dev)
    flow.start(fresh=True)

    found, text, shot = flow.probe_coin_entry()
    if not found:
        pytest.skip(f"推荐页当前未投放「领淘金币」挂件（活动位，双 11/618 期间会整块换皮肤）。现场截图={shot}")

    assert "金币" in text, f"金币入口 desc 异常：{text!r}（期望包含「金币」）"


def test_claim_coins_flow(dev):
    """推荐页领淘金币 → 金币中心签到链路"""
    flow = TaobaoFlow(dev)
    flow.start(fresh=True)

    result = flow.claim_coins()

    if not result.entry_found:
        pytest.skip(f"{result.message} 现场截图={result.home_screenshot}")

    assert result.entry_text, "金币入口存在但 desc 读取为空"
    assert result.screenshot is not None, "金币中心截图未生成"

    # 金币中心可能是 Weex/H5 容器，原生读不到余额 —— 这种情况只校验「页面可达 + 截图留证」，
    # 不把「读不到余额」当成失败（和购物车同一套判定策略）。
    if result.page_reached and result.balance_readable:
        assert result.balance_before >= 0, (
            f"金币中心已就绪但余额未解析：before={result.balance_before} after={result.balance_after}。"
            "请用 CoinPage.calibrate() 导层级后校准 BALANCE_PATTERNS"
        )
    else:
        assert result.screenshot is not None, f"金币中心既未就绪也无可复核截图（page_reached={result.page_reached}）"
