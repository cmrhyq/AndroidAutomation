"""大麦抢票业务流：把页面对象编排成一条「可重试但不重复下单」的链路。

分层约定：pages 描述单页能力，flows 编排跨页业务，
脚本只做调用。区别在大麦这条链路多了两个别的业务没有的东西：

**模式闸门**
    ``GrabMode`` 决定这一轮走到哪一步停（探测 / 到确认页 / 真提交），
    只由命令行决定，不读配置、不写回。见 :data:`data.constant.damai.GrabMode`。

**不可重试的终态**
    提交后验不到结果 → ``submit_unverified``，退出码 11，**绝不重试**。
    重试意味着可能下第二单。这不是保守，这是唯一正确的做法。
"""

from __future__ import annotations

import time

from core.android.actions import AndroidDevice
from core.android.evidence import capture_failure
from core.logger import get_logger, log_event
from core.report import note, step
from data.constant.damai import (
    EXIT_CONFIG_OR_DEVICE_ERROR,
    EXIT_RETRIES_EXHAUSTED,
    EXIT_SUCCESS,
    EXIT_TERMINAL_FAILURE,
    TERMINAL_REASONS,
    GrabMode,
)
from data.entity.damai import GrabResult

logger = get_logger(__name__)

__all__ = ["DamaiFlow", "FlowError"]


class FlowError(RuntimeError):
    """流程在某一步无法继续（前置条件不满足）。"""


#: 单次重试的间隔（秒），避免失败后疯狂重开页面把 App 搞崩
DEFAULT_RETRY_INTERVAL_S = 1.5


class DamaiFlow:
    """一次抢票运行的完整编排。

    Args:
        dev: 设备封装（u2 直连后端）。
        config: :class:`core.config.damai.GrabConfig`。
        mode: 执行模式。默认最保守的 PROBE —— 忘传参数时宁可什么都不做，
            也不能默认就往提交走。
    """

    def __init__(self, dev: AndroidDevice, config, *, mode: GrabMode | None = None) -> None:
        self.dev = dev
        self.config = config
        self.mode = mode or GrabMode.PROBE

    # ------------------------------------------------------------------ #
    # 入口
    # ------------------------------------------------------------------ #
    def run(self, *, max_retries: int = 3) -> GrabResult:
        """跑完一轮（含重试），返回结局。"""
        started = time.monotonic()
        result = GrabResult(mode=self.mode.value)
        max_retries = max(1, int(max_retries))

        logger.info("=" * 60)
        logger.info("抢票开始：模式=%s（%s）", self.mode.label, self.mode.description)
        logger.info("目标：%s / %s / %s / %s", self.config.keyword, self.config.city,
                    self.config.date, self.config.price)
        logger.info("=" * 60)

        for attempt in range(1, max_retries + 1):
            result.attempts = attempt
            log_event(logger, "attempt_start", attempt=attempt, max_retries=max_retries)
            try:
                outcome, reason = self._attempt(attempt)
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 - 单轮失败不该终止整条链路
                logger.exception("第 %d 轮异常终止", attempt)
                artifacts = capture_failure(
                    self.dev, "attempt_error", error=exc,
                    extra={"attempt": attempt, "mode": self.mode.value},
                )
                result.artifacts.update(artifacts)
                outcome, reason = "error", None
            else:
                if outcome in {"probe_ready", "validation_ready", "submitted"}:
                    # SUBMIT 模式下只有真的提交成功才算成功；其余模式到达既定
                    # 终点即算成功（PROBE 承诺不点购票，VALIDATION 承诺不提交）
                    result.success = outcome == "submitted" or self.mode is not GrabMode.SUBMIT
                    result.outcome = outcome
                    result.terminal_reason = reason
                    result.exit_code = EXIT_SUCCESS
                    result.duration_ms = int((time.monotonic() - started) * 1000)
                    log_event(
                        logger, "run_success", outcome=outcome,
                        attempts=attempt, duration_ms=result.duration_ms,
                    )
                    return result

            result.outcome = outcome
            result.terminal_reason = reason
            if reason in TERMINAL_REASONS:
                # 终态：不重试。submit_unverified 走到这里是防重复下单。
                result.exit_code = EXIT_TERMINAL_FAILURE
                result.duration_ms = int((time.monotonic() - started) * 1000)
                log_event(
                    logger, "run_terminal", level=40,
                    outcome=outcome, reason=reason, attempts=attempt,
                )
                return result

            if attempt < max_retries:
                logger.warning("第 %d 轮失败（%s），%.1fs 后重试", attempt, outcome, DEFAULT_RETRY_INTERVAL_S)
                time.sleep(DEFAULT_RETRY_INTERVAL_S)

        result.exit_code = EXIT_RETRIES_EXHAUSTED
        result.duration_ms = int((time.monotonic() - started) * 1000)
        log_event(
            logger, "run_exhausted", level=40,
            outcome=result.outcome, attempts=result.attempts,
            duration_ms=result.duration_ms,
        )
        return result

    # ------------------------------------------------------------------ #
    # 单轮
    # ------------------------------------------------------------------ #
    def _attempt(self, attempt: int) -> tuple[str, str | None]:
        """跑一轮，返回 (outcome, terminal_reason)。"""
        detail = self._navigate(attempt)

        with step("校验目标演出"):
            detail.verify_target()

        if not self.mode.clicks_purchase:
            note(f"PROBE 模式：停在详情页，不点击购票入口（attempt={attempt}）")
            return "probe_ready", None

        with step("等待开售"):
            reason = detail.wait_until_buyable()
            log_event(logger, "sale_wait_done", reason=reason, attempt=attempt)
            if reason == "timeout":
                return "sale_timeout", None

        if detail.is_reservation_mode():
            return "reservation_only", "reservation_only"

        with step("点进购票流程"):
            page = self._enter_purchase(detail)

        with step("选择票档"):
            page = self._select_price(page)

        with step("确认页"):
            confirm = self._reach_confirm(page)

        with step("勾选观演人"):
            selected = confirm.prepare()
            note(f"已勾选观演人: {selected}")

        if not self.mode.submits_order:
            note("VALIDATION 模式：到达确认页并勾完观演人，不点击提交")
            return "validation_ready", None

        blocker = confirm.has_blocker()
        if blocker:
            artifacts = capture_failure(self.dev, "submit_blocked", extra={"blocker": blocker})
            return "captcha", "captcha"

        with step("提交订单"):
            confirm.submit()
            outcome = confirm.wait_result()

        if outcome == "submitted":
            note("已提交并检测到收银台/待支付页，停止操作（不做任何支付动作）")
            return "submitted", None
        if outcome == "rejected":
            return "sold_out", "sold_out"
        if outcome == "blocked":
            capture_failure(self.dev, "submit_blocked", extra={"result": outcome})
            return "captcha", "captcha"

        # 提交了但验不到结果 —— 终态，绝不重试
        artifacts = capture_failure(
            self.dev, "submit_unverified", extra={"mode": self.mode.value},
        )
        logger.error("提交后无法确认结果，停止重试以避免重复下单：%s", artifacts)
        return "submit_unverified", "submit_unverified"

    # ------------------------------------------------------------------ #
    # 各阶段
    # ------------------------------------------------------------------ #
    def _navigate(self, attempt: int):
        """导航到目标演出详情页，返回 :class:`DamaiDetailPage`。"""
        from pages.damai.detail_page import DamaiDetailPage
        from pages.damai.home_page import DamaiHomePage

        auto = bool(getattr(self.config, "auto_navigate", True))
        if not auto:
            # 已经手动停在详情页：直接校验，不重新导航
            logger.info("auto_navigate=false，按当前页面处理")
            return DamaiDetailPage(self.dev, self.config)

        with step("启动大麦并进入首页"):
            self._launch_app()
            home = DamaiHomePage(self.dev, self.config).ensure_ready()

        with step("搜索目标演出"):
            search = home.open_search()
            search.search(str(self.config.keyword))
            cards = search.wait_cards(timeout=15.0)
            note(f"搜索结果 {len(cards)} 条（attempt={attempt}）")
            target = search.pick_target()

        with step("打开详情页"):
            return search.open_card(target)

    def _launch_app(self) -> None:
        """冷启动大麦，保证从确定的状态开始。

        冷启动而不是「保证在前台」：上一轮可能停在确认页/收银台，直接复用
        现场会让这一轮的页面判断全部错位。
        """
        package = str(getattr(self.config, "app_package", "") or "")
        activity = str(getattr(self.config, "app_activity", "") or "")
        if not package:
            raise FlowError("配置里没有 app_package，无法启动大麦")
        logger.info("冷启动 %s", package)
        try:
            self.dev.terminate_app(package)
        except Exception:  # noqa: BLE001 - 没在运行就跳过
            logger.debug("结束 App 失败（可能本来就没在运行）")
        self.dev.wait_short("等 App 退干净")
        self.dev.launch_app(package)
        if activity:
            self.dev.wait_activity(activity, timeout=10.0)
        self.dev.wait_medium("冷启动等首屏")

    def _enter_purchase(self, detail):
        """在详情页点购买入口，返回下一站页面对象。"""
        from pages.damai.confirm_page import DamaiConfirmPage
        from pages.damai.sku_page import DamaiSkuPage

        bar = detail.buy_bar()
        if not bar.wait_until_safe(timeout=6.0):
            # 文案读不到可能是 Canvas 自绘，此时靠开售时间闸门兜底
            state = bar.read()
            if state is None:
                raise FlowError("详情页找不到购买入口")
            logger.info("购买入口文案不可读（疑似 Canvas 自绘），按坐标点击")
        if not bar.tap():
            raise FlowError("购买入口不安全或不可点")

        self.dev.wait_short("等购票流程打开")
        # 免选座的演出会直接进确认页；常规演出先到 SKU 选票档
        confirm = DamaiConfirmPage(self.dev, self.config)
        if confirm.exists_any(*confirm.ready_selectors, timeout=2.5):
            log_event(logger, "purchase_entry", next_page="confirm")
            return confirm
        log_event(logger, "purchase_entry", next_page="sku")
        return DamaiSkuPage(self.dev, self.config)

    def _select_price(self, page):
        """在 SKU 页选票档并确定；已是确认页则原样返回。"""
        from pages.components.damai_price_panel import (
            PricePanelMissingError,
            PriceUnavailableError,
        )
        from pages.damai.confirm_page import DamaiConfirmPage

        if isinstance(page, DamaiConfirmPage):
            return page

        try:
            page.select_price(
                str(getattr(self.config, "price", "") or ""),
                index=getattr(self.config, "price_index", None),
            )
        except PriceUnavailableError as exc:
            raise FlowError(f"票档不可选: {exc}") from exc
        except PricePanelMissingError as exc:
            raise FlowError(f"票档面板未出现: {exc}") from exc

        page.tap_confirm()
        self.dev.wait_short("等进入确认页")
        return page

    def _reach_confirm(self, page):
        """确保拿到确认页对象。"""
        from pages.damai.confirm_page import DamaiConfirmPage

        if isinstance(page, DamaiConfirmPage):
            return page
        confirm = DamaiConfirmPage(self.dev, self.config)
        if confirm.exists_any(*confirm.ready_selectors, timeout=8.0):
            return confirm
        raise FlowError(
            f"没能进入确认页（当前 activity={self.dev.current_activity}）"
        )


def build_flow(dev: AndroidDevice, config, *, mode: GrabMode) -> DamaiFlow:
    """便捷构造（脚本层用）。"""
    return DamaiFlow(dev, config, mode=mode)


#: 配置/设备类错误统一映射到这个退出码，供脚本层使用
CONFIG_OR_DEVICE_EXIT = EXIT_CONFIG_OR_DEVICE_ERROR
