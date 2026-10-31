"""淘金币链路用例：首页 → 推荐页入口 → 金币中心。"""

from __future__ import annotations

from pathlib import Path

import allure
import pytest

from core import AndroidDevice
from core.report import attach_image, attach_table, note
from flows import TaobaoFlow

#: 整份文件都是真机用例：无设备时由 conftest 的 driver 夹具整体 skip
pytestmark = [pytest.mark.device, pytest.mark.android]

#: 可自动完成的金币任务：任务名关键词 → 次数（None = 按任务行上的「(0/N)」剩余次数跑满）。
#: 任务名以真机「做任务赚能量」列表为准（2026-10-31：查看双11活动攻略 0/1、
#: 好物随心逛 0/5、搜一搜你心仪的宝贝 0/10、看看#牛仔裤 0/10）。
TASK_PLAN: dict[str, int | None] = {
    "双11活动攻略": 1,
    "好物随心逛": None,
    "搜一搜": None,
    "看看#": None,
}


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
    @pytest.mark.order(1)
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
    @pytest.mark.order(2)
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
    @pytest.mark.order(3)
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

    @allure.title("金币中心可进任务列表并读出任务状态")
    @allure.story("金币任务")
    @allure.severity(allure.severity_level.NORMAL)
    @allure.tag("coin-task", "inspect")
    @allure.description(
        "点「去赚能量」进任务列表，读出任务名、进度与完成态，**只读不点**，适合放进日常回归做任务位监控。\n\n"
        "金币小镇虽然是 themis 容器（TMSActivity），但实测原生层有文案（「去赚能量」「我的车库」可读），"
        "所以这里按「真读到任务行」判定成功；一条都没读到才 skip 并挂现场截图。"
    )
    @pytest.mark.order(4)
    def test_check_task(self, flow: TaobaoFlow) -> None:
        """点「去赚能量」检查淘金币任务状态（只读）"""
        with allure.step("进入金币中心并读取任务列表"):
            result = flow.inspect_coin_tasks()

        if result.screenshot:
            attach_image(result.screenshot, name="任务列表现场")

        if not result.readable:
            pytest.skip(result.message)

        with allure.step("记录任务状态"):
            attach_table(
                {
                    "任务数": str(len(result.tasks)),
                    "已领": "、".join(result.done) or "无",
                    "任务列表": "\n".join(result.tasks) or "未读到",
                },
                name="金币任务状态",
            )

        with allure.step("校验任务列表可读"):
            assert result.tasks, (
                f"金币页可读但一条任务行都没配上（现场截图={result.screenshot}）。"
                "任务名与「去完成」按钮按纵坐标配对，改版后请用 CoinPage.calibrate() 导层级校准 ROW_Y_TOLERANCE"
            )

    @allure.title("按计划完成淘金币任务并记录次数")
    @allure.story("金币任务")
    @allure.severity(allure.severity_level.CRITICAL)
    @allure.tag("coin-task", "slow")
    @allure.description(
        "按模块级 TASK_PLAN 逐个任务点「去完成」，退回金币中心再点下一个，"
        "统计每个任务实际点成功的次数并挂表。计划里的 None 表示按任务行上的「(0/N)」跑满剩余次数。\n\n"
        "任务跳转后按 dwell（默认 6s）真实停留再返回 —— 浏览 / 视频类任务按停留时长计数，"
        "点完立刻返回会被判无效；「搜一搜」类若平台要求真的输入关键词，还需人工补一步，"
        "这条用例只能保证「进入 + 停留 + 返回」的动作链路。\n\n"
        "整条用例偏久所以标了 slow。任务行一条都读不到时 skip 并挂截图。"
    )
    @pytest.mark.order(5)
    @pytest.mark.slow
    def test_complete_task(self, flow: TaobaoFlow) -> None:
        """完成能做的淘金币任务（浏览 / 搜索 / 视频类）"""
        with allure.step(f"按计划执行金币任务：{TASK_PLAN}"):
            result = flow.run_coin_tasks(TASK_PLAN)

        if result.screenshot:
            attach_image(result.screenshot, name="任务执行后现场")

        if not result.readable:
            pytest.skip(result.message)

        plan_rows = "\n".join(
            f"{name} × {'页面剩余' if times is None else times}" for name, times in TASK_PLAN.items()
        )
        attach_table(
            {
                "计划": plan_rows,
                "实际完成": "\n".join(f"{name} × {times}" for name, times in result.completed.items()) or "0",
                "未执行": "、".join(result.failures) or "无",
                "任务清单": "\n".join(result.tasks) or "未读到",
            },
            name="金币任务执行结果",
        )
        note(完成总数=result.total_completed)

        assert result.tasks, (
            f"金币页可读但任务行没配上（现场截图={result.screenshot}），无法执行任务。"
            "先用 CoinPage.calibrate() 导层级确认任务列表文案"
        )
        if not result.completed:
            pytest.skip(f"读到 {len(result.tasks)} 条任务，但没有一条可点：可能今日已领完或按钮文案改版")