"""命令行入口：``python -m damai <子命令>``。

    check-config   校验配置（占位符、废弃字段、类型），不碰设备
    doctor         环境体检（u2 / adb / 大麦版本 / 时钟偏差 / Appium 端口占用）
    dump           导出当前 UI 层级，用于校准选择器
    run            执行抢票（三级模式闸门）

安全设计：执行模式**只由命令行参数决定**，不读配置文件、不写回任何文件。
``--submit`` 必须显式确认（``--yes`` 或交互输入确认词）才会真的提交订单。
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from core.logger import get_logger, log_event
from damai.config import ConfigError, GrabConfig, resolve_config_path
from damai.mode import GrabMode
from damai.run_report import (
    EXIT_CONFIG_OR_DEVICE_ERROR,
    EXIT_INTERRUPTED,
    EXIT_SUCCESS,
)

__all__ = ["build_parser", "main"]

logger = get_logger("damai.cli")

CONFIRM_PHRASE = "确认提交"
COUNTDOWN_SECONDS = 3


# --------------------------------------------------------------------------- #
# 参数解析
# --------------------------------------------------------------------------- #
def _add_mode_flags(parser: argparse.ArgumentParser) -> None:
    """三级模式互斥组。缺省 = 安全探测。"""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--probe",
        dest="mode",
        action="store_const",
        const=GrabMode.PROBE,
        help="安全探测（默认）：只检查详情页控件，不点「立即购票」",
    )
    group.add_argument(
        "--validate",
        dest="mode",
        action="store_const",
        const=GrabMode.VALIDATION,
        help="开发验证：走到确认页并勾完观演人，停在「立即提交」前",
    )
    group.add_argument(
        "--submit",
        dest="mode",
        action="store_const",
        const=GrabMode.SUBMIT,
        help="正式抢票：点击提交订单（需 --yes 或交互确认）",
    )


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="配置文件路径（默认 damai/config.jsonc）")
    parser.add_argument("--serial", help="设备序列号（默认取 .env 的 DEVICE_UDID）")
    parser.add_argument(
        "--no-strict-placeholders",
        action="store_true",
        help="占位符只告警不报错（仅在调试配置本身时使用）",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m damai",
        description="大麦抢票引擎（uiautomator2 直连）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python -m damai check-config\n"
            "  python -m damai doctor\n"
            "  python -m damai dump --tag before_sale\n"
            "  python -m damai run --probe\n"
            "  python -m damai run --validate\n"
            "  python -m damai run --submit --yes\n"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check-config", help="校验配置，不碰设备")
    _add_common_flags(p_check)
    p_check.add_argument("--json", action="store_true", help="以 JSON 输出解析结果")
    p_check.set_defaults(handler=_cmd_check_config)

    p_doctor = sub.add_parser("doctor", help="环境体检")
    _add_common_flags(p_doctor)
    p_doctor.set_defaults(handler=_cmd_doctor)

    p_dump = sub.add_parser("dump", help="导出当前 UI 层级")
    _add_common_flags(p_dump)
    p_dump.add_argument("--tag", default="damai", help="产物文件名标签")
    p_dump.set_defaults(handler=_cmd_dump)

    p_run = sub.add_parser("run", help="执行抢票")
    _add_common_flags(p_run)
    _add_mode_flags(p_run)
    p_run.add_argument("--yes", action="store_true", help="--submit 时跳过交互确认")
    p_run.add_argument(
        "--no-countdown",
        action="store_true",
        help="提交流程跳过 3 秒倒数（确认本身仍需 --yes 或交互输入）",
    )
    p_run.add_argument("--max-retries", type=int, default=3, help="外层重试次数（默认 3）")
    p_run.add_argument("--result-json", help="运行摘要输出路径")
    p_run.set_defaults(handler=_cmd_run, mode=GrabMode.PROBE)

    return parser


# --------------------------------------------------------------------------- #
# check-config
# --------------------------------------------------------------------------- #
def _cmd_check_config(args: argparse.Namespace) -> int:
    import json

    path = resolve_config_path(args.config)
    print(f"配置文件: {path}")
    try:
        config = GrabConfig.load(
            path,
            mode=args.mode if getattr(args, "mode", None) else GrabMode.PROBE,
            serial=args.serial,
            strict_placeholders=not args.no_strict_placeholders,
        )
    except ConfigError as exc:
        print(f"\n✗ 配置校验失败：\n{exc}", file=sys.stderr)
        return EXIT_CONFIG_OR_DEVICE_ERROR

    payload = config.to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_SUCCESS

    print("\n✓ 配置校验通过")
    for key, value in payload.items():
        print(f"  {key:<26} = {value!r}")
    print(f"\n  配置指纹: {config.fingerprint()}")
    print(f"  设备序列号: {config.serial or '<自动识别>'}")
    print(f"  执行模式: {config.mode.label} —— {config.mode.description}")
    return EXIT_SUCCESS


# --------------------------------------------------------------------------- #
# doctor / dump
# --------------------------------------------------------------------------- #
def _cmd_doctor(args: argparse.Namespace) -> int:
    try:
        from damai.device import doctor_report
    except ImportError as exc:  # pragma: no cover - 仅在模块缺失时触发
        print(f"✗ 无法加载环境体检模块: {exc}", file=sys.stderr)
        return EXIT_CONFIG_OR_DEVICE_ERROR

    return doctor_report(serial=args.serial)


def _cmd_dump(args: argparse.Namespace) -> int:
    try:
        from damai.device import dump_current_hierarchy
    except ImportError as exc:  # pragma: no cover
        print(f"✗ 无法加载设备模块: {exc}", file=sys.stderr)
        return EXIT_CONFIG_OR_DEVICE_ERROR

    return dump_current_hierarchy(serial=args.serial, tag=args.tag)


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def _confirm_submit(*, assume_yes: bool, skip_countdown: bool) -> bool:
    """``--submit`` 的确认闸门。任何非交互环境都必须显式 ``--yes``。"""
    interactive = sys.stdin is not None and sys.stdin.isatty()

    if not assume_yes:
        if not interactive:
            logger.error(
                "非交互环境下使用 --submit 必须显式加 --yes；已中止，未做任何提交"
            )
            return False
        print("\n⚠️  即将进入正式抢票模式，会真实提交订单。")
        print(f"    确认请逐字输入「{CONFIRM_PHRASE}」：", end="", flush=True)
        try:
            answer = input().strip()
        except EOFError:
            return False
        if answer != CONFIRM_PHRASE:
            logger.error("确认词不匹配（输入 %r），已中止", answer)
            return False

    if not skip_countdown:
        for remaining in range(COUNTDOWN_SECONDS, 0, -1):
            print(f"  {remaining}...", flush=True)
            time.sleep(1)
    return True


def _cmd_run(args: argparse.Namespace) -> int:
    mode: GrabMode = getattr(args, "mode", GrabMode.PROBE)
    log_event(
        logger,
        "mode_selected",
        mode=mode.value,
        confirms_order=mode.submits_order,
        tty=bool(sys.stdin and sys.stdin.isatty()),
    )
    print(f"\n== {mode.label} ==\n{mode.description}\n")

    try:
        config = GrabConfig.load(
            args.config,
            mode=mode,
            serial=args.serial,
            strict_placeholders=not args.no_strict_placeholders,
        )
    except ConfigError as exc:
        print(f"✗ 配置校验失败：\n{exc}", file=sys.stderr)
        return EXIT_CONFIG_OR_DEVICE_ERROR

    if mode.submits_order and not _confirm_submit(
        assume_yes=args.yes, skip_countdown=args.no_countdown
    ):
        return EXIT_CONFIG_OR_DEVICE_ERROR

    try:
        from damai.device import DeviceConnectionError, Uiautomator2MissingError
        from damai.orchestrator import DamaiGrabber
        from damai.run_report import DEFAULT_RESULT_JSON, write_run_summary
    except ImportError as exc:  # pragma: no cover
        print(f"✗ 引擎模块缺失: {exc}", file=sys.stderr)
        return EXIT_CONFIG_OR_DEVICE_ERROR

    try:
        grabber = DamaiGrabber(config)
    except (Uiautomator2MissingError, DeviceConnectionError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return EXIT_CONFIG_OR_DEVICE_ERROR

    try:
        result = grabber.run_with_retry(max_retries=max(1, args.max_retries))
    except KeyboardInterrupt:
        logger.warning("收到中断信号，已停止")
        return EXIT_INTERRUPTED

    summary_path = Path(args.result_json) if args.result_json else DEFAULT_RESULT_JSON
    write_run_summary(summary_path, result.to_dict(), with_result=result)

    print(f"\n结局: {result.outcome}   成功: {'是' if result.success else '否'}")
    print(f"尝试: {result.attempts} 次   耗时: {result.duration_ms / 1000:.2f}s")
    if result.terminal_reason:
        print(f"终态原因: {result.terminal_reason}（不会自动重试）")
    print(f"摘要: {result.summary_path or summary_path}")
    if result.artifacts:
        print("失败取证:")
        for key, value in result.artifacts.items():
            print(f"  {key}: {value}")
    return result.exit_code


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:  # pragma: no cover - argparse required=True 已兜住
        parser.print_help()
        return EXIT_CONFIG_OR_DEVICE_ERROR
    try:
        return int(handler(args))
    except ConfigError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return EXIT_CONFIG_OR_DEVICE_ERROR
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return EXIT_INTERRUPTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
