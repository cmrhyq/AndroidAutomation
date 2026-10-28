"""淘宝 App 端到端用例：启动 → 搜索 → 详情 → 加购 → 购物车。

前置条件
--------
1. 设备已连接并开启调试（``adb devices`` 能看到 ``device``）
2. 设备已安装淘宝（``com.taobao.taobao``）
3. Appium 已就绪，或 ``AUTO_START_APPIUM=true`` 由框架自动拉起

无设备 / Appium 不可用时由 conftest 整体 skip，不会 fail。

运行
----
    .venv/Scripts/python.exe -m pytest tests/test_taobao.py -v
    .venv/Scripts/python.exe -m pytest tests/test_taobao.py -v -k cart     # 只跑加购链路
    TAOBAO_KEYWORD=机械键盘 .venv/Scripts/python.exe -m pytest tests/

跑完会自动生成报告：``artifacts/reports/report_<时间戳>.html``

选择器校准
----------
淘宝选择器随版本变化。断言失败时看报告里的「现场截图」，或导出层级对照::

    .venv/Scripts/python.exe scripts/ui_dump.py
    # 产物在 artifacts/pages/*.xml

⚠️ 详情页与购物车页是 Weex 渲染，``page_source`` 里没有业务节点，
dump 它们没有意义（详见 ``pages/detail_page.py`` / ``pages/cart_page.py`` 的说明）。
"""

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

    assert home.is_here(), (
        f"启动后未进入淘宝首页：请确认 App 已安装、首页选择器未失效（当前 activity={home.activity}）"
    )


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

    assert result.badge_increased, (
        f"加购未生效：角标 {result.badge_before} -> {result.badge_after}"
    )
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
