"""大麦抢票入口脚本。

用法：
    python scripts/damai_grab.py run              # 安全探测（默认，不点购票）
    python scripts/damai_grab.py run --validate   # 走到确认页并勾观演人，不提交
    python scripts/damai_grab.py run --submit     # 正式抢票（需 --yes 或交互确认）
    python scripts/damai_grab.py check            # 只校验配置，不碰设备
    python scripts/damai_grab.py doctor           # 环境体检（u2/adb/大麦版本/时钟偏移）
    python scripts/damai_grab.py dump             # 导出当前 UI 层级，校准选择器用

安全设计
--------
执行模式**只由命令行参数决定**，不读配置文件、不写回任何文件。这是从源项目
抄来的教训：它把 ``probe_only`` 写回 jsonc，于是「上次跑完停在 submit，下次
定时任务直接下单」成了真实事故源。

``--submit`` 必须显式确认（``--yes`` 或交互输入确认词 ``确认提交``）才会真的下单。
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

from _bootstrap import bootstrap, hr

#: 交互确认必须一字不差输入的短语
CONFIRM_PHRASE = "确认提交"
#: 确认后的倒数秒数，给一个「反悔窗口」
COUNTDOWN_SECONDS = 3
#: 默认配置路径（相对项目根）
DEFAULT_CONFIG_HINT = "config/damai.jsonc"


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #
def _add_mode_flags(parser: argparse.ArgumentParser) -> None:
    """三级模式互斥组。缺省 = 安全探测。"""
    from data.constant.damai import GrabMode

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--probe", dest="mode", action="store_const", const=GrabMode.PROBE,
        help="安全探测（默认）：只检查详情页控件，不点「立即购票」",
    )
    group.add_argument(
        "--validate", dest="mode", action="store_const", const=GrabMode.VALIDATION,
        help="开发验证：走到确认页并勾完观演人，停在「立即提交」前",
    )
    group.add_argument(
        "--submit", dest="mode", action="store_const", const=GrabMode.SUBMIT,
        help="正式抢票：点击提交订单（需 --yes 或交互确认）",
    )


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help=f"配置文件路径（默认 {DEFAULT_CONFIG_HINT}）")
    parser.add_argument("--serial", help="设备序列号（默认取 .env 的 DEVICE_UDID）")
    parser.add_argument(
        "--no-strict-placeholders",
        action="store_true",
        help="占位符只告警不报错（仅调试配置本身时用）",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python scripts/damai_grab.py",
        description="大麦抢票（uiautomator2 直连）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="执行抢票")
    _add_mode_flags(p_run)
    _add_common_flags(p_run)
    p_run.add_argument("--yes", action="store_true", help="跳过提交前的交互确认")
    p_run.add_argument("--no-countdown", action="store_true", help="跳过倒数（仅调试用）")
    p_run.add_argument("--max-retries", type=int, default=3, help="最多尝试轮数（默认 3）")
    p_run.add_argument("--result-json", help="运行摘要输出路径")

    p_check = sub.add_parser("check", help="只校验配置，不碰设备")
    _add_common_flags(p_check)

    p_doctor = sub.add_parser("doctor", help="环境体检")
    p_doctor.add_argument("--serial", help="设备序列号")

    p_dump = sub.add_parser("dump", help="导出当前 UI 层级")
    p_dump.add_argument("--serial", help="设备序列号")
    p_dump.add_argument("--tag", default="damai", help="文件名标签")

    return parser


# --------------------------------------------------------------------------- #
# 提交确认闸门
# --------------------------------------------------------------------------- #
def confirm_submit(*, assume_yes: bool, skip_countdown: bool) -> bool:
    """正式提交前的三层闸门：``--yes`` → 交互确认词 → 倒数。

    非交互环境（cron / CI）没有 tty 时必须显式 ``--yes``，否则直接拒绝 ——
    定时任务里悄悄下单是最坏的结果。
    """
    if assume_yes:
        print("⚠ 已通过 --yes 跳过确认，3 秒后开始提交（Ctrl-C 可中断）")
    else:
        if not (sys.stdin and sys.stdin.isatty()):
            print(
                "✗ 非交互环境必须显式加 --yes 才会提交订单。\n"
                "  这是防「定时任务悄悄下单」的最后一道闸。",
                file=sys.stderr,
            )
            return False
        print(f"即将点击「立即提交」并下单。确认请输入「{CONFIRM_PHRASE}」：")
        try:
            answer = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return False
        if answer != CONFIRM_PHRASE:
            print("✗ 确认词不匹配，已取消")
            return False

    if not skip_countdown:
        for remaining in range(COUNTDOWN_SECONDS, 0, -1):
            print(f"  {remaining}...", end="\r", flush=True)
            time.sleep(1)
        print(" " * 12, end="\r")
    return True


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_check(args: argparse.Namespace) -> int:
    from core.config.damai import ConfigError, GrabConfig
    from data.constant.damai import EXIT_CONFIG_OR_DEVICE_ERROR, EXIT_SUCCESS, GrabMode

    try:
        config = GrabConfig.load(
            args.config,
            mode=GrabMode.PROBE,
            serial=args.serial,
            strict_placeholders=not args.no_strict_placeholders,
        )
    except ConfigError as exc:
        print(f"✗ 配置校验失败：\n{exc}", file=sys.stderr)
        return EXIT_CONFIG_OR_DEVICE_ERROR

    print(hr("="))
    print(" 配置校验通过")
    print(hr("="))
    payload = config.to_dict()
    width = max(len(str(key)) for key in payload)
    for key, value in payload.items():
        shown = value
        if isinstance(value, (list, tuple)):
            shown = "、".join(str(item) for item in value) or "（空）"
        elif value is None:
            shown = "（未设置）"
        print(f"  {str(key).ljust(width)} : {shown}")
    print(hr("="))
    print(f"  指纹: {config.fingerprint()}")
    return EXIT_SUCCESS


def cmd_doctor(args: argparse.Namespace) -> int:
    from core.android.adb import find_adb
    from core.clock import device_clock_offset_ms
    from core.config.config import DeviceConfig

    print(hr("="))
    print(" 大麦抢票环境体检")
    print(hr("="))

    ok = True

    try:
        from importlib.metadata import version

        u2_version = version("uiautomator2")
        print(f"  uiautomator2      : {u2_version}")
    except Exception:  # noqa: BLE001
        print("  uiautomator2      : ✗ 未安装（pip install -e \".[grab]\"）")
        ok = False

    adb = find_adb()
    print(f"  adb               : {adb or '✗ 未找到'}")
    ok = ok and bool(adb)

    serial = args.serial or DeviceConfig.load().udid
    driver = None
    if adb:
        from core.android.u2_driver import DeviceConnectionError, U2MissingError, connect

        try:
            driver = connect(serial, package="cn.damai")
            caps = driver.capabilities
            serial = caps.get("udid") or serial
            print(f"  设备              : {serial}")
            print(f"  型号              : {caps.get('deviceManufacturer')} {caps.get('deviceModel')}")
            print(f"  Android           : {caps.get('platformVersion')}")
        except (U2MissingError, DeviceConnectionError) as exc:
            print(f"  设备              : ✗ {exc}")
            ok = False

    if driver is not None:
        from core.android.evidence import app_version

        shell = driver.shell
        version = app_version("cn.damai", serial or "")
        print(f"  大麦版本          : {version or '✗ 未安装或读取失败'}")
        ok = ok and bool(version)

        offset = device_clock_offset_ms(shell)
        if offset is None:
            print("  设备时钟偏差      : ✗ 读取失败")
        else:
            # offset = 设备 - 本机。负值说明设备走在本机后面（设备慢）。
            who = "快" if offset > 0 else "慢"
            print(f"  设备时钟偏差      : {offset:+d} ms（设备比本机{who} {abs(offset)}ms）")
            print("                      注：此项只用于观测，决定何时点击的是本机时钟")

    # u2 走的是设备上的 instrumentation，与本机 4723 端口无关；这里只提示别的东西占着
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        in_use = sock.connect_ex(("127.0.0.1", 4723)) == 0
    if in_use:
        print("  本机 :4723        : 被占用（与本工具无关，抢票期间可忽略）")

    from data.constant.damai import EXIT_CONFIG_OR_DEVICE_ERROR, EXIT_SUCCESS

    print(hr("="))
    print(" 结论: " + ("环境就绪" if ok else "存在问题，见上面 ✗ 标记"))
    if driver is not None:
        # 必须释放：u2 服务不退会占住设备上的 UiAutomator 进程
        driver.close()
    return EXIT_SUCCESS if ok else EXIT_CONFIG_OR_DEVICE_ERROR


def cmd_dump(args: argparse.Namespace) -> int:
    from core.android.u2_driver import DeviceConnectionError, U2MissingError, connect
    from core.config.config import PAGE_SOURCE_DIR, DeviceConfig
    from data.constant.damai import EXIT_CONFIG_OR_DEVICE_ERROR, EXIT_SUCCESS

    try:
        driver = connect(args.serial or DeviceConfig.load().udid, package="cn.damai")
    except (U2MissingError, DeviceConnectionError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return EXIT_CONFIG_OR_DEVICE_ERROR

    PAGE_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    path = PAGE_SOURCE_DIR / f"{args.tag}_{int(time.time())}.xml"
    activity = driver.current_activity
    xml = driver.page_source()
    path.write_text(xml, encoding="utf-8")
    print(f"UI 层级已导出: {path}（{len(xml)} 字节，{xml.count('<node')} 个节点）")
    print(f"当前 Activity: {activity}")
    driver.close()
    return EXIT_SUCCESS


def cmd_run(args: argparse.Namespace) -> int:
    from core.android.lock import AlreadyRunningError, InstanceLock
    from core.android.u2_driver import DeviceConnectionError, U2MissingError, connect
    from core.config.config import DeviceConfig
    from core.config.damai import ConfigError, GrabConfig
    from core.logger import log_event, logger
    from core.report.run_summary import DEFAULT_RESULT_JSON, write_run_summary
    from data.constant.damai import (
        EXIT_CONFIG_OR_DEVICE_ERROR,
        EXIT_INTERRUPTED,
        EXIT_LOCK_CONFLICT,
        GrabMode,
    )
    from flows.damai_flow import DamaiFlow

    mode: GrabMode = getattr(args, "mode", GrabMode.PROBE)
    log_event(
        logger, "mode_selected", mode=mode.value,
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

    if mode.submits_order and not confirm_submit(
        assume_yes=args.yes, skip_countdown=args.no_countdown
    ):
        return EXIT_CONFIG_OR_DEVICE_ERROR

    # 单实例闸门：两个进程同时点票会互相打断，最坏还会重复下单。
    # OS 级排他锁，进程崩溃 / Ctrl-C 时自动释放，不留僵尸锁。
    lock = InstanceLock()
    try:
        lock.acquire()
    except AlreadyRunningError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return EXIT_LOCK_CONFLICT

    driver = None
    try:
        try:
            driver = connect(config.serial or DeviceConfig.load().udid, package=config.app_package)
        except (U2MissingError, DeviceConnectionError) as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return EXIT_CONFIG_OR_DEVICE_ERROR

        from core.android.actions import AndroidDevice

        dev = AndroidDevice(driver, DeviceConfig.load())
        flow = DamaiFlow(dev, config, mode=mode)
        try:
            result = flow.run(max_retries=max(1, args.max_retries))
        except KeyboardInterrupt:
            logger.warning("收到中断信号，已停止")
            return EXIT_INTERRUPTED

        summary_path = args.result_json or DEFAULT_RESULT_JSON
        write_run_summary(summary_path, result.to_dict(), with_result=result)
    finally:
        # u2 服务不退会占住设备上的 UiAutomator 进程，必须显式释放
        if driver is not None:
            driver.close()
        lock.release()

    print(f"\n结局: {result.outcome}   成功: {'是' if result.success else '否'}")
    print(f"尝试: {result.attempts} 次   耗时: {result.duration_ms / 1000:.2f}s")
    if result.terminal_reason:
        print(f"终态原因: {result.terminal_reason}（不会自动重试）")
    print(f"摘要: {result.summary_path or summary_path}")
    if result.artifacts:
        print("现场取证:")
        for key, value in result.artifacts.items():
            print(f"  {key}: {value}")
    return result.exit_code


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    bootstrap()
    parser = build_parser()
    args = parser.parse_args(argv)
    command = getattr(args, "command", None)
    if not command:
        parser.print_help()
        return 0

    handlers = {
        "run": cmd_run,
        "check": cmd_check,
        "doctor": cmd_doctor,
        "dump": cmd_dump,
    }
    return handlers[command](args)


if __name__ == "__main__":
    raise SystemExit(main())
