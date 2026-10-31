"""淘金币链路用例：首页 → 推荐页入口 → 金币中心。

覆盖范围
--------
1. **首页连通性** —— 金币链路的前置条件
2. **入口投放探测** —— 只探测不点击，适合做活动位投放监控
3. **进入金币中心** —— 页面可达 + 截图留证

三条用例**互不依赖**：每条都自己 ``flow.start()``，单独用 ``-k`` 跑一条也能跑通
（不要用 ``pytest-dependency`` 串起来，一旦前一条失败，后面的用例会集体 skip，
报告里看不出到底是哪一层坏了）。

判定分级（重要）
----------------
金币小镇是 themis 渲染容器，``page_source`` 里读不到原生业务节点，余额常常拿不到。
所以内容级校验按能力分级：**能读到余额才校验数值，读不到只要求「页面可达 + 截图留证」**，
不把渲染层限制算成用例失败。同一套策略也用在购物车页（见 ``pages/cart_page.py``）。

前置条件
--------
1. ``adb devices`` 能看到 ``device``
2. 设备已安装淘宝（``com.taobao.taobao``）
3. Appium 就绪，或 ``AUTO_START_APPIUM=true`` 由框架自动拉起

无设备 / Appium 不可用时由 ``tests/conftest.py`` 整体 skip，不会 fail。

运行
----
    .venv/Scripts/python.exe -m pytest tests/test_golden_coin.py -v
    .venv/Scripts/python.exe -m pytest tests/test_golden_coin.py -v -k entry      # 只跑入口探测
    AA_WAIT_SCALE=2 .venv/Scripts/python.exe -m pytest tests/test_golden_coin.py  # 放慢观察

跑完自动产出 Allure 报告（``artifacts/allure-report``），``--allure-open`` 可直接打开。
"""

from __future__ import annotations

from pathlib import Path

import allure
import pytest

from core import AndroidDevice
from core.report import attach_image, attach_table, note
from flows import TaobaoFlow

#: 整份文件都是真机用例：无设备时由 conftest 的 driver 夹具整体 skip
pytestmark = [pytest.mark.device, pytest.mark.android]


@pytest.fixture(scope="module")
def flow(dev: AndroidDevice) -> TaobaoFlow:
    """整份文件共用一个业务流实例。

    放模块级而不是写进类里：类作用域夹具写成实例方法在 pytest 10 会报
    ``PytestRemovedIn10Warning``（实例属性对用例不可见），模块级没有这个坑。
    """
    return TaobaoFlow(dev)


@allure.epic("淘宝 UI 自动化")
@allure.feature("淘金币")
@allure.suite("淘金币链路")
class TestGoldenCoin:
    """淘金币链路冒烟：按声明顺序串行执行，每个用例自带启动步骤、互相独立。"""

    @allure.title("启动淘宝后能进入首页")
    @allure.story("首页连通性")
    @allure.severity(allure.severity_level.BLOCKER)
    @allure.tag("smoke", "launch")
    @allure.description(
        "金币链路的前置条件。首页判据用搜索栏（排他），不会被常驻的底部 tab 误判 —— "
        "早期版本用「底部首页 tab」判定，结果停在任意 tab 上都能通过。\n\n"
        "失败时优先确认：App 是否已安装、首页选择器是否随版本改版（用 "
        "`scripts/ui_dump.py` 导层级对照）。"
    )
    def test_home_launch(self, flow: TaobaoFlow) -> None:
        """启动淘宝后能进入首页（连通性冒烟）"""
        with allure.step("启动淘宝并等待首页就绪"):
            home = flow.start(fresh=False)

        with allure.step("确认当前页面是首页"):
            assert home.is_here(), (
                f"启动后未进入淘宝首页：请确认 App 已安装、首页选择器未失效（当前 activity={home.activity}）"
            )

        attach_table(
            {
                "当前 Activity": str(home.activity),
                "当前频道": str(home.current_channel()),
            },
            name="页面状态",
        )

    @allure.title("推荐页「领淘金币」入口按活动位投放")
    @allure.story("金币入口探测")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.tag("coin-entry")
    @allure.description(
        "只探测不点击，可用于日常的入口投放监控。挂件是**活动位**（双 11 / 618 会整块换皮肤），"
        "未投放算 skip 而不是 fail；同时把现场截图挂进报告，用来区分「真的没投放」和「选择器失效」。"
    )
    def test_coin_entry_visible(self, flow: TaobaoFlow) -> None:
        """推荐页「领淘金币」入口按活动位投放（未投放则跳过）"""
        with allure.step("回到首页推荐流"):
            flow.start(fresh=False)

        with allure.step("探测推荐页金币入口"):
            found, text, shot = flow.probe_coin_entry()

        if not found:
            attach_image(shot, name="未投放现场")
            pytest.skip(f"推荐页当前未投放「领淘金币」挂件（活动位，双 11/618 期间会整块换皮肤）。现场截图={shot}")

        with allure.step("校验入口文案包含「金币」"):
            assert "金币" in text, f"金币入口 desc 异常：{text!r}（期望包含「金币」）"

        note(金币入口=text)

    @allure.title("推荐页入口可进入金币中心并留证")
    @allure.story("金币中心")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.tag("coin-center")
    @allure.description(
        "从推荐页点入口进入金币中心。金币小镇是 themis 渲染容器，原生层级通常读不到余额，"
        "所以判定是「页面可达 + 截图留证」；只有读到余额时才额外校验数值区间，"
        "不把渲染层限制算成失败。"
    )
    def test_open_golden_coin(self, flow: TaobaoFlow) -> None:
        """推荐页入口可进入金币中心并留证"""
        with allure.step("回到首页并确认金币入口已投放"):
            flow.start(fresh=False)
            found, _text, shot = flow.probe_coin_entry()
            if not found:
                attach_image(shot, name="未投放现场")
                pytest.skip("推荐页当前未投放「领淘金币」挂件，无法进入金币中心")

        with allure.step("点击入口进入金币中心"):
            coin = flow.open_coin_center()
            flow.dev.wait_medium("等金币中心渲染")

        with allure.step("等待金币中心就绪"):
            ready = coin.wait_ready_soft(timeout=10.0)

        with allure.step("读取金币中心状态并留证"):
            snapshot = coin.snapshot()
            image = coin.capture("golden_coin")
            attach_image(image, name="金币中心现场")
            attach_table(
                {
                    "Activity": str(coin.activity),
                    "页面可达": "是" if ready else "否",
                    "内容可读": "是" if snapshot.readable else "否（themis 渲染层）",
                    "金币余额": str(snapshot.balance) if snapshot.balance >= 0 else "读不到",
                    "今日签到": "已签到" if snapshot.signed else "未签到 / 未知",
                    "任务数": str(len(snapshot.tasks)),
                },
                name="金币中心状态",
            )

        with allure.step("校验页面可达与截图产物"):
            assert ready, f"入口可点击但未进入金币中心（当前 activity={coin.activity}），截图={image}"
            assert image is not None and Path(image).is_file(), f"金币中心截图未生成：{image}"

        # 能读到余额时再补一刀数值校验；读不到属于渲染层限制，不算失败。
        if snapshot.balance >= 0:
            assert snapshot.balance < 100_000_000, (
                f"金币余额解析为 {snapshot.balance}，超出合理区间，疑似把「金币」附近的其它数字当成了余额"
            )
