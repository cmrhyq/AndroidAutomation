"""uiautomator2 连接层 —— 全包**唯一**允许 ``import uiautomator2`` 的模块。

``uiautomator2`` 声明在可选依赖组 ``grab`` 里：只想跑 Appium 的使用者不必
被拉入 adbutils / requests / lxml。因此这里必须懒加载：

- 其它模块顶层一律不 import u2
- 未安装时 ``import damai`` 仍然成功，只有真正连接设备才抛
  :class:`Uiautomator2MissingError`（带安装指引）
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

from core.android.env_check import find_adb
from core.android.proc import run_cmd
from core.config.config import FAILURE_DIR, PAGE_SOURCE_DIR, get
from core.logger import get_logger, log_event
from damai.constants import DAMAI_MAIN_ACTIVITY, DAMAI_PACKAGE

__all__ = [
    "DeviceConnectionError",
    "Uiautomator2MissingError",
    "connect",
    "device_clock_offset_ms",
    "doctor_report",
    "dump_current_hierarchy",
    "require_u2",
    "safe_dump_xml",
]

logger = get_logger("damai.device")

#: dump_hierarchy 超过这个耗时说明页面有视频/复杂动画，记一条事件便于事后归因
_SLOW_DUMP_MS = 800

INSTALL_HINT = 'pip install -e ".[grab]"'


class Uiautomator2MissingError(RuntimeError):
    """未安装可选的 uiautomator2 依赖。"""


class DeviceConnectionError(RuntimeError):
    """连不上设备（未插线 / 未授权 / 序列号不匹配）。"""


def require_u2():
    """返回已导入的 ``uiautomator2`` 模块；未安装则抛带指引的异常。"""
    try:
        import uiautomator2  # noqa: PLC0415 - 故意延迟到调用时，见模块 docstring
    except ImportError as exc:
        raise Uiautomator2MissingError(
            f"未安装 uiautomator2，抢票引擎无法连接设备。\n"
            f"→ 在项目根目录运行：{INSTALL_HINT}\n"
            f"→ 只想跑 Appium 的常规 UI 测试则无需安装。"
        ) from exc
    return uiautomator2


# ---------------------------------------------------------------------- #
# 连接
# ---------------------------------------------------------------------- #
def connect(
    serial: str | None = None,
    *,
    app_package: str = DAMAI_PACKAGE,
    app_activity: str = DAMAI_MAIN_ACTIVITY,
    cold_start: bool = False,
    auto_start_app: bool = True,
) -> Any:
    """连接设备并确保大麦在前台。

    与原实现一致：**不 force-stop、不清数据、不重新登录** —— 抢票依赖用户
    已经登好的会话，冷启动只会浪费时间还可能触发风控。

    Args:
        serial: 设备序列号；``None`` 时交给 u2 按 adb 连接自动选择。多设备
            同时在线时务必显式传入，否则可能连到另一台机器。
        app_package / app_activity: 大麦包名与启动 Activity。
        cold_start: 为 True 时先 ``app_stop`` 再启动（调试用，正常抢票别开）。
        auto_start_app: 是否在包名不匹配时拉起 App。
    """
    u2 = require_u2()

    try:
        device = u2.connect(serial) if serial else u2.connect()
    except Exception as exc:  # u2 内部可能抛 AdbError/ConnectError 等多种类型
        raise DeviceConnectionError(
            f"连接设备失败（serial={serial or '<自动>'}）：{exc}\n"
            f"→ 先用 adb devices 确认设备在线且已授权"
        ) from exc

    # 抢票要的是「立刻返回」，不能让 u2 自己等
    try:
        device.settings["wait_timeout"] = 0
        device.settings["operation_delay"] = (0, 0)
    except Exception:  # pragma: no cover - 桩对象/精简驱动可能不支持
        logger.debug("设置 u2 全局等待参数失败，忽略")

    if cold_start:
        try:
            device.app_stop(app_package)
        except Exception:
            logger.debug("app_stop 失败，忽略")

    if auto_start_app:
        _ensure_app_foreground(device, app_package, app_activity)

    return device


def _ensure_app_foreground(device: Any, app_package: str, app_activity: str) -> None:
    """已在大麦前台就不重启，省掉 2~3 秒冷启动。"""
    already_foreground = False
    try:
        current = device.app_current()
        if isinstance(current, dict) and current.get("package") == app_package:
            already_foreground = True
    except Exception:
        already_foreground = False

    if already_foreground:
        logger.debug("大麦已在前台，跳过启动")
        return

    try:
        device.app_start(app_package, activity=app_activity, stop=False)
        logger.info("已拉起大麦 App")
    except Exception as exc:
        raise DeviceConnectionError(f"启动 {app_package} 失败：{exc}") from exc


# ---------------------------------------------------------------------- #
# UI 层级导出
# ---------------------------------------------------------------------- #
def safe_dump_xml(device: Any, *, slow_warn_ms: int = _SLOW_DUMP_MS) -> str | None:
    """导出 UI 层级 XML，失败返回 ``None``。

    注意：u2 的 ``dump_hierarchy`` **没有超时参数**，无法从外部硬性中断，
    所以这里做不到「到点放弃」。能做的是：

    1. 单点收口 —— 全链路只从这里 dump，方便以后替换（如加线程超时）
    2. 记录耗时 —— 页面有视频/复杂动画时单次 dump 可能 1~10 秒，
       超过阈值打 ``event=dump_slow``，事后能立刻归因
    3. 绝不上抛 —— 热路径里 dump 失败应当降级而不是中断抢票
    """
    started = time.monotonic()
    try:
        xml = device.dump_hierarchy()
    except Exception as exc:
        logger.warning("dump_hierarchy 失败：%s", exc)
        return None

    elapsed_ms = int((time.monotonic() - started) * 1000)
    if elapsed_ms > slow_warn_ms:
        log_event(logger, "dump_slow", elapsed_ms=elapsed_ms, threshold_ms=slow_warn_ms)
    return xml or None


def write_dump(xml: str, tag: str = "damai") -> Any:
    """把 UI 层级落到 ``artifacts/pages/`` 并返回路径。"""
    PAGE_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    path = PAGE_SOURCE_DIR / f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}.xml"
    path.write_text(xml, encoding="utf-8")
    return path


def dump_current_hierarchy(serial: str | None = None, tag: str = "damai") -> int:
    """CLI ``dump`` 子命令：连设备、导出当前 UI 层级供校准选择器。"""
    from damai.run_report import EXIT_CONFIG_OR_DEVICE_ERROR, EXIT_SUCCESS

    try:
        device = connect(serial or _serial_from_env())
    except (Uiautomator2MissingError, DeviceConnectionError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return EXIT_CONFIG_OR_DEVICE_ERROR

    xml = safe_dump_xml(device)
    if not xml:
        print("✗ 导出 UI 层级失败", file=sys.stderr)
        return EXIT_CONFIG_OR_DEVICE_ERROR

    path = write_dump(xml, tag)
    print(f"✓ UI 层级已导出: {path}")
    print(f"  节点数约 {xml.count('<node')}，包名 {_current_package(device) or 'unknown'}")
    return EXIT_SUCCESS


def _current_package(device: Any) -> str:
    try:
        return (device.app_current() or {}).get("package", "")
    except Exception:
        return ""


def _serial_from_env() -> str | None:
    value = get("DEVICE_UDID", "").strip()
    return value or None


# ---------------------------------------------------------------------- #
# 时钟
# ---------------------------------------------------------------------- #
def device_clock_offset_ms(serial: str | None = None, *, samples: int = 3) -> int | None:
    """设备时钟相对本机时钟的偏差（毫秒，设备 - 本机）。

    源项目完全没有时间校准，只用本机墙钟与 ``sell_start_time`` 比较 ——
    PC 或手机任意一方漂移，开抢时刻就会提前或延后几十毫秒到数秒，
    而且事后完全无法归因。这里提供一个可观测的量供 ``doctor`` 展示。

    设备端用 ``date +%s%3N`` 取毫秒；部分 ROM 不支持 ``%3N``，此时退化为
    秒级精度（误差 ±1s，仅用于粗判）。任何失败返回 ``None``。

    单次测量受 adb 往返延迟影响（本机取中点做对称性修正，仍会有几十到几百
    毫秒抖动），因此默认采样 3 次取中位数。
    """
    offsets: list[int] = []
    for _ in range(max(1, samples)):
        value = _sample_clock_offset_once(serial)
        if value is not None:
            offsets.append(value)
    if not offsets:
        return None
    offsets.sort()
    return offsets[len(offsets) // 2]


def _sample_clock_offset_once(serial: str | None) -> int | None:
    adb = find_adb()
    if adb is None:
        return None

    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["shell", "date", "+%s%3N"]

    # 必须用 time.time()（墙钟）而不是 monotonic —— 后者是开机以来的秒数，
    # 与设备时间不可比
    started = time.time()
    code, output = run_cmd(command, timeout=5)
    finished = time.time()
    if code != 0:
        return None

    raw = output.strip().splitlines()[0].strip() if output.strip() else ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 10:
        return None

    local_mid_ms = int((started + finished) / 2 * 1000)
    if len(digits) >= 13:  # 毫秒精度
        return int(digits[:13]) - local_mid_ms

    # 秒级精度：误差 ±1s，只能粗判
    return int(digits[:10]) * 1000 - local_mid_ms


# ---------------------------------------------------------------------- #
# 环境体检
# ---------------------------------------------------------------------- #
def doctor_report(serial: str | None = None) -> int:
    """CLI ``doctor`` 子命令：列出抢票链路依赖的每一项状态。"""
    from core.android.env_check import list_devices
    from damai.run_report import EXIT_CONFIG_OR_DEVICE_ERROR, EXIT_SUCCESS

    print("\n== 大麦抢票环境体检 ==\n")

    ok = True
    u2_version = _u2_version()
    if u2_version is None:
        print(f"✗ uiautomator2   未安装（{INSTALL_HINT}）")
        ok = False
    else:
        print(f"✓ uiautomator2   {u2_version}")

    adb = find_adb()
    print(f"{'✓' if adb else '✗'} adb            {adb or '未找到'}")

    devices = list_devices(adb_path=adb)
    online = [d for d in devices if d.state == "device"]
    if not online:
        print("✗ 在线设备       无（请插线并在手机上允许 USB 调试）")
        ok = False
    else:
        print(f"✓ 在线设备       {len(online)} 台")
        for entry in online:
            print(f"    - {entry.serial}  {getattr(entry, 'model', '')}".rstrip())
        if len(online) > 1:
            print("    ⚠ 多台设备同时在线，请用 --serial 显式指定，否则可能连错机器")

    target = serial or _serial_from_env() or (online[0].serial if online else None)

    version = _app_version(target)
    print(f"{'✓' if version else '✗'} 大麦 App 版本   {version or '未检测到（是否已安装 cn.damai？）'}")

    offset = device_clock_offset_ms(target)
    if offset is None:
        print("- 设备时钟偏差   无法读取")
    else:
        verdict = "正常" if abs(offset) < 500 else "⚠ 偏差较大，抢票可能提前/延后"
        print(f"{'✓' if abs(offset) < 500 else '⚠'} 设备时钟偏差   {offset:+d} ms（{verdict}）")

    appium_busy = _port_in_use("127.0.0.1", 4723)
    if appium_busy:
        print("⚠ Appium :4723  端口已被占用")
        print("    u2 与 Appium 各起一套 instrumentation，同时跑会互相抢设备")
        print("    （表现：命令随机 404、点击丢失）。请先停掉 Appium 再跑抢票。")
    else:
        print("✓ Appium :4723  空闲（不会与 u2 抢设备）")

    print(f"\n产物目录: {FAILURE_DIR.parent}")
    print(f"配置文件: {'已设置 DAMAI_CONFIG_PATH' if os.environ.get('DAMAI_CONFIG_PATH') else 'damai/config.jsonc'}")
    print()
    return EXIT_SUCCESS if ok else EXIT_CONFIG_OR_DEVICE_ERROR


def _u2_version() -> str | None:
    if "uiautomator2" not in sys.modules:
        try:
            require_u2()
        except Uiautomator2MissingError:
            return None
    try:
        from importlib.metadata import version

        return version("uiautomator2")
    except Exception:  # pragma: no cover
        return "unknown"


def _app_version(serial: str | None) -> str | None:
    from damai.env_snapshot import detect_damai_app_version

    return detect_damai_app_version(serial=serial)


def _port_in_use(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0
