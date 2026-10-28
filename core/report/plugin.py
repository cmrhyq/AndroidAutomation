"""pytest 插件：自动收集用例结果与步骤，生成 HTML/JSON 报告。

用法
----
    pytest tests/                              # 默认就生成报告
    pytest tests/ --report-dir=out             # 自定义输出目录
    pytest tests/ --no-report                  # 彻底关掉报告，零开销

挂载方式见 ``tests/conftest.py`` 里的 ``pytest_plugins``。
"""

from __future__ import annotations

import platform
import socket
import subprocess
import time
from pathlib import Path

import pytest

from core.report import collector as collector_mod
from core.report.html import write_html, write_json
from core.report.models import (
    ERROR,
    FAILED,
    PASSED,
    SKIPPED,
    CaseRecord,
    EnvironmentInfo,
    RunRecord,
)

#: item 上的属性名 → 该用例的 CaseRecord
_ATTR = "_tb_case_record"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("report", "测试报告")
    group.addoption(
        "--no-report",
        action="store_true",
        default=False,
        help="关闭测试报告（默认每次运行都会生成）",
    )
    group.addoption(
        "--report-dir",
        action="store",
        default="artifacts/reports",
        help="报告输出目录（默认 artifacts/reports）",
    )
    group.addoption(
        "--report-title",
        action="store",
        default="淘宝 UI 自动化测试报告",
        help="报告标题",
    )


# ---------------------------------------------------------------------- #
# 会话生命周期
# ---------------------------------------------------------------------- #
def pytest_sessionstart(session: pytest.Session) -> None:
    config = session.config
    if config.getoption("--no-report"):
        return
    run = RunRecord(
        started_at=time.time(),
        title=config.getoption("--report-title"),
        command=_command_line(config),
    )
    config._tb_run = run  # type: ignore[attr-defined]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    run: RunRecord | None = getattr(config, "_tb_run", None)
    if run is None:
        return

    run.finished_at = time.time()
    run.env = collect_environment()

    out_dir = Path(config.getoption("--report-dir"))
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(run.started_at))
    html_path = write_html(run, out_dir / f"report_{stamp}.html")
    json_path = write_json(run, out_dir / f"report_{stamp}.json")

    # 终端上直接给出路径，省得去目录里翻
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("-", "测试报告")
        reporter.write_line(f"  通过率 : {run.pass_rate:.1f}%  "
                            f"（{run.passed}/{run.total - run.skipped} 执行，{run.skipped} 跳过）")
        reporter.write_line(f"  耗时   : {run.duration:.1f}s")
        reporter.write_line(f"  HTML   : {html_path}")
        reporter.write_line(f"  JSON   : {json_path}")


# ---------------------------------------------------------------------- #
# 用例生命周期
# ---------------------------------------------------------------------- #
def pytest_runtest_setup(item: pytest.Item) -> None:
    """用例开始前建立 CaseRecord 并绑定步骤收集器。"""
    if item.config.getoption("--no-report"):
        return

    case = CaseRecord(
        nodeid=item.nodeid,
        name=_display_name(item),
        started_at=time.time(),
        # 用例的 docstring 第一行就是最好的业务描述
        context=_docstring_context(item),
    )
    setattr(item, _ATTR, case)
    collector_mod.bind(case)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()

    case: CaseRecord | None = getattr(item, _ATTR, None)
    if case is None:
        return

    if report.when == "setup":
        if report.skipped:
            case.status = SKIPPED
            case.error_message = _skip_reason(report)
        elif report.failed:
            # 夹具报错（设备掉线等）—— 记为 error 并把原因带出来
            case.status = ERROR
            _fill_error(case, report)
    elif report.when == "call":
        case.duration = report.duration
        if report.passed:
            case.status = PASSED
        elif report.skipped:
            case.status = SKIPPED
            case.error_message = _skip_reason(report)
        elif report.failed:
            case.status = FAILED
            _fill_error(case, report)
            _attach_failure_screenshot(item, case)
    elif report.when == "teardown" and report.failed and case.status == PASSED:
        case.status = ERROR
        _fill_error(case, report)


def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    """用例收尾：把 CaseRecord 归档进本次运行。"""
    case: CaseRecord | None = getattr(item, _ATTR, None)
    run: RunRecord | None = getattr(item.config, "_tb_run", None)
    collector_mod.unbind()
    if case is None or run is None:
        return
    if case.duration == 0.0 and case.started_at:
        case.duration = max(0.0, time.time() - case.started_at)
    run.cases.append(case)


# ---------------------------------------------------------------------- #
# 辅助
# ---------------------------------------------------------------------- #
def _display_name(item: pytest.Item) -> str:
    """优先用用例的 docstring 第一行做人话标题，回退到函数名。"""
    doc = (getattr(item, "function", None).__doc__ or "").strip() if hasattr(item, "function") else ""
    if doc:
        return doc.splitlines()[0].strip()
    return item.name


def _docstring_context(item: pytest.Item) -> dict[str, str]:
    """把 docstring 里的「前置条件/运行」等段落略过，只留首行做标题。"""
    return {}


def _skip_reason(report: pytest.TestReport) -> str:
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2])
    return str(longrepr or "")


def _fill_error(case: CaseRecord, report: pytest.TestReport) -> None:
    longrepr = getattr(report, "longrepr", None)
    if longrepr is None:
        return
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        _, _, text = longrepr
        case.error_type = "Skipped" if report.skipped else "Error"
        case.error_message = str(text)
        return
    # pytest 的 longrepr 是 TermRepr 之类对象，转字符串后取最后一行做摘要
    text = str(longrepr)
    case.traceback_text = text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines:
        # 最后一行通常是 "E   AssertionError: xxx"，去掉前缀 E
        last = lines[-1].lstrip("E").strip()
        case.error_message = last
        if ":" in last:
            case.error_type = last.split(":", 1)[0].strip()
        else:
            case.error_type = "AssertionError"


def _attach_failure_screenshot(item: pytest.Item, case: CaseRecord) -> None:
    """用例失败时立刻截图 —— 此时 driver 还活着，teardown 后再拍就晚了。"""
    funcargs = getattr(item, "funcargs", {}) or {}
    dev = funcargs.get("dev")
    if dev is None:
        return
    take = getattr(dev, "screenshot", None)
    if not callable(take):
        return
    try:
        path = take(f"failed_{item.name}")
    except Exception:
        return
    if path:
        case.screenshots.append(path)


def _command_line(config: pytest.Config) -> str:
    """拼一条能复现的 pytest 命令（去掉报告参数本身）。"""
    args = [str(a) for a in config.invocation_params.args if a != "--no-report"]
    return "pytest " + " ".join(args)


# ---------------------------------------------------------------------- #
# 环境信息采集（全部走 adb / 环境变量，不依赖 Appium 会话）
# ---------------------------------------------------------------------- #
def collect_environment() -> EnvironmentInfo:
    info = EnvironmentInfo(
        python_version=platform.python_version(),
        host=socket.gethostname(),
    )

    try:
        from core.config.config import DeviceConfig

        cfg = DeviceConfig.load()
        info.app_package = cfg.app_package or ""
        info.appium_url = getattr(cfg, "appium_server_url", "") or ""
        udid = getattr(cfg, "device_udid", "") or ""
    except Exception:
        udid = ""

    serial = udid or _first_online_device()
    info.device_udid = serial

    if serial:
        info.device_model = _adb(serial, "shell", "getprop", "ro.product.model")
        info.android_version = _adb(serial, "shell", "getprop", "ro.build.version.release")
        size = _adb(serial, "shell", "wm", "size") or ""
        info.screen_size = size.replace("Physical size:", "").strip()
        if info.app_package:
            info.app_version = _app_version(serial, info.app_package)

    info.appium_version = _appium_version()
    return info


def _first_online_device() -> str:
    out = _run(["adb", "devices"])
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return ""


def _adb(serial: str, *args: str) -> str:
    return _run(["adb", "-s", serial, *args]).strip()


def _app_version(serial: str, package: str) -> str:
    out = _adb(serial, "shell", "dumpsys", "package", package)
    for line in out.splitlines():
        if "versionName=" in line:
            return line.split("versionName=", 1)[1].split()[0]
    return ""


def _appium_version() -> str:
    try:
        import appium

        return getattr(appium, "__version__", "")
    except Exception:
        return ""


def _run(cmd: list[str], timeout: int = 12) -> str:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
        )
        return proc.stdout or ""
    except (subprocess.SubprocessError, OSError):
        return ""
