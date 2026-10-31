"""pytest 插件：自动收集用例结果与步骤，生成 HTML/JSON/索引报告。

用法
----
    pytest tests/                              # 默认就生成报告
    pytest tests/ --report-dir=out             # 自定义输出目录
    pytest tests/ --report-open                # 跑完自动用浏览器打开
    pytest tests/ --report-keep=5              # 只保留最近 5 份，更早的清理
    pytest tests/ --no-report                  # 彻底关掉报告，零开销

挂载方式见根目录 ``conftest.py`` 的 ``pytest_plugins``（pytest 7+ 不允许写在子目录 conftest 里）。

环境信息的两级采集
------------------
1. **运行期采集**（准确）：用例跑起来后从 ``dev`` fixture / driver capabilities 取
   设备型号、系统版本、分辨率、前台包名 —— 这些只有真正建好会话才拿得到。
2. **静态兜底**（不依赖会话）：会话起来前用 ``adb`` + ``/status`` 补 udid、应用版本、
   Appium 版本，保证「一条用例都没跑起来」时报告也不是空白。
融合策略：运行期优先，缺什么补什么。
"""

from __future__ import annotations

import dataclasses
import json
import platform
import socket
import subprocess
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import pytest

from core.config.config import get
from core.report import collector as collector_mod
from core.report.html import write_html, write_index, write_json
from core.report.models import (
    ERROR,
    FAILED,
    PASSED,
    SKIPPED,
    CaseRecord,
    EnvironmentInfo,
    RunRecord,
    human_duration,
)

#: item 上的属性名 → 该用例的 CaseRecord
_ATTR = "_tb_case_record"

#: pytest 退出码含义（贴进报告，CI 里比数字直观）
_EXIT_TEXT = {
    0: "全部通过",
    1: "有用例失败",
    2: "用户中断",
    3: "内部错误",
    4: "用法错误",
    5: "没有收集到用例",
}

#: 这些内置 marker 不进统计（否则「usefixtures」会排在统计表第一）
_IGNORED_MARKERS = {"usefixtures", "parametrize", "filterwarnings", "skip", "skipif", "xfail"}

#: 报告里展示的运行参数（只展示有值的）
_OPTION_KEYS = ("AA_WAIT_SCALE", "TAOBAO_KEYWORD", "APP_PACKAGE", "DEVICE_UDID", "IMPLICIT_WAIT")


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
    group.addoption(
        "--report-open",
        action="store_true",
        default=False,
        help="生成后用默认浏览器打开 HTML 报告",
    )
    group.addoption(
        "--report-keep",
        action="store",
        type=int,
        default=20,
        help="报告目录内保留最近 N 份运行记录（0 = 不清理，默认 20）",
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
        options=_run_options(config),
        pytest_version=pytest.__version__,
    )
    config._tb_run = run  # type: ignore[attr-defined]


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    run: RunRecord | None = getattr(config, "_tb_run", None)
    if run is None:
        return

    run.finished_at = time.time()
    run.exit_status = int(exitstatus)
    run.exit_status_text = _EXIT_TEXT.get(int(exitstatus), "未知状态")
    run.env = _fill_missing(run.env, collect_environment())

    out_dir = Path(config.getoption("--report-dir"))
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(run.started_at))
    html_path = write_html(run, out_dir / f"report_{stamp}.html")
    json_path = write_json(run, out_dir / f"report_{stamp}.json")
    index_path = write_index(out_dir)

    _prune_reports(out_dir, keep=int(config.getoption("--report-keep")))

    config._tb_paths = (html_path, json_path, index_path)  # type: ignore[attr-defined]
    if config.getoption("--report-open"):
        _open_in_browser(html_path)


def pytest_terminal_summary(
    terminalreporter: object, exitstatus: int, config: pytest.Config
) -> None:
    """终端摘要：通过率 + 失败要点 + 报告路径。"""
    run: RunRecord | None = getattr(config, "_tb_run", None)
    paths = getattr(config, "_tb_paths", None)
    if run is None or not paths:
        return

    html_path, json_path, index_path = paths
    write = getattr(terminalreporter, "write_line", print)
    write_sep = getattr(terminalreporter, "write_sep", None)

    if callable(write_sep):
        write_sep("-", "测试报告")
    write(
        f"  结果   : {run.exit_status_text}（exit={run.exit_status}）"
        f"，通过率 {run.pass_rate:.1f}%（{run.passed}/{run.executed} 执行，"
        f"{run.failed} 失败，{run.skipped} 跳过）"
    )
    write(
        f"  耗时   : {human_duration(run.duration)}"
        f"（用例平均 {human_duration(run.avg_case_duration)}，{run.total} 条）"
    )

    # 失败要点：直接在终端点名「哪条用例炸在哪个步骤」，不用翻 HTML
    failures = [c for c in run.sorted_cases() if c.is_failure]
    if failures:
        write("  失败要点:")
        for case in failures[:5]:
            step = case.first_failed_step
            where = f" @ 步骤「{step.display_name}」" if step else ""
            reason = (case.error_message or case.error_type or "").splitlines()[0][:90]
            write(f"    - {case.name}{where}: {reason}")
        if len(failures) > 5:
            write(f"    … 另有 {len(failures) - 5} 条失败，详见报告")

    slowest = run.slowest(3)
    if slowest:
        write("  最慢用例: " + "，".join(f"{c.name} {human_duration(c.duration)}" for c in slowest))

    write(f"  HTML   : {html_path}")
    write(f"  JSON   : {json_path}")
    write(f"  历史   : {index_path}")


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
        context=_docstring_context(item),
        file=_case_file(item),
        group=_case_group(item),
        markers=_markers(item),
        description=_description(item),
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
        # 会话已经建好，这时采集到的环境信息最准（只需一次）
        _capture_runtime_env(item)
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
# 用例元信息
# ---------------------------------------------------------------------- #
def _display_name(item: pytest.Item) -> str:
    """优先用用例的 docstring 第一行做人话标题，回退到函数名。"""
    doc = _docstring(item)
    if doc:
        return doc.splitlines()[0].strip()
    return item.name


def _docstring(item: pytest.Item) -> str:
    function = getattr(item, "function", None)
    return (getattr(function, "__doc__", "") or "").strip()


def _description(item: pytest.Item) -> str:
    """docstring 去掉标题首行后的剩余部分，作为用例的业务说明。"""
    lines = _docstring(item).splitlines()
    rest = "\n".join(lines[1:]).strip()
    return rest


def _docstring_context(item: pytest.Item) -> dict[str, str]:
    """业务上下文由用例运行时通过 ``note()`` 填充，这里不预置。"""
    return {}


def _case_file(item: pytest.Item) -> str:
    """测试文件相对路径（相对 rootdir，便于在不同机器上阅读）。"""
    raw = item.location[0] if item.location else ""
    if not raw:
        return ""
    try:
        return Path(raw).resolve().relative_to(Path(str(item.config.rootpath))).as_posix()
    except (ValueError, OSError):
        return Path(raw).as_posix()


def _case_group(item: pytest.Item) -> str:
    """分组名：默认取测试文件名（test_taobao），报告里按组折叠。"""
    path = _case_file(item)
    return Path(path).stem if path else "未分组"


def _markers(item: pytest.Item) -> list[str]:
    try:
        names = [m.name for m in item.iter_markers()]
    except Exception:  # noqa: BLE001 - marker 读取失败不该影响报告
        return []
    return sorted({n for n in names if n not in _IGNORED_MARKERS})


def _run_options(config: pytest.Config) -> dict[str, str]:
    """把本次运行的关键参数记进报告，便于复现（等待倍率、关键词等）。"""
    options = {key: get(key, "") for key in _OPTION_KEYS}
    options["报告目录"] = str(config.getoption("--report-dir"))
    return {k: v for k, v in options.items() if v}


# ---------------------------------------------------------------------- #
# 错误与截图
# ---------------------------------------------------------------------- #
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
    # 用业务步骤名补一句「炸在哪一步」，比 traceback 里的行号有用
    if case.first_failed_step:
        case.context.setdefault("失败步骤", case.first_failed_step.display_name)


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
    if path and path not in case.screenshots:
        case.screenshots.append(path)


def _command_line(config: pytest.Config) -> str:
    """拼一条能复现的 pytest 命令（去掉报告参数本身）。"""
    args = [str(a) for a in config.invocation_params.args if a != "--no-report"]
    return "pytest " + " ".join(args)


# ---------------------------------------------------------------------- #
# 环境采集 1：运行期（driver 会话内，最准）
# ---------------------------------------------------------------------- #
def _capture_runtime_env(item: pytest.Item) -> None:
    """从 dev fixture / driver capabilities 采集环境信息（每个会话只做一次）。

    放在 call 阶段而不是 setup：此时夹具已建好、driver 可用；
    又早于用例失败，所以即使用例抛异常也拿得到。
    """
    run: RunRecord | None = getattr(item.config, "_tb_run", None)
    if run is None or run.env.app_package:
        return

    dev = (getattr(item, "funcargs", {}) or {}).get("dev")
    if dev is None:
        return

    env = run.env
    caps: dict = {}
    driver = getattr(dev, "driver", None)
    if driver is not None:
        try:
            caps = dict(driver.capabilities or {})
        except Exception:  # noqa: BLE001 - 会话可能已挂，读取失败不影响报告
            caps = {}

    env.device_model = str(caps.get("deviceModel") or caps.get("deviceName") or env.device_model or "")
    env.device_udid = str(caps.get("udid") or dev.cfg.udid or env.device_udid or "")
    env.android_version = str(caps.get("platformVersion") or env.android_version or "")
    env.appium_url = dev.cfg.server_url or env.appium_url
    env.platform = platform.platform()

    cfg_package = dev.cfg.app_package
    try:
        package = cfg_package or dev.current_package or ""
    except Exception:  # noqa: BLE001
        package = cfg_package or ""
    env.app_package = package

    try:
        screen = dev.screen
        env.screen_size = f"{screen.width}x{screen.height}"
    except Exception:  # noqa: BLE001
        pass

    if package:
        env.app_version = _app_version(env.device_udid, package) or env.app_version


# ---------------------------------------------------------------------- #
# 环境采集 2：静态兜底（不需要 Appium 会话）
# ---------------------------------------------------------------------- #
def collect_environment() -> EnvironmentInfo:
    info = EnvironmentInfo(
        python_version=platform.python_version(),
        host=socket.gethostname(),
        platform=platform.platform(),
    )

    try:
        from core.config.config import DeviceConfig

        cfg = DeviceConfig.load()
        info.app_package = cfg.app_package or ""
        info.appium_url = cfg.server_url or ""
        serial = cfg.udid or ""
    except Exception:  # noqa: BLE001 - 配置读取失败时退回自动探测
        serial = ""

    serial = serial or _first_online_device()
    info.device_udid = serial

    if serial:
        info.device_model = _adb(serial, "shell", "getprop", "ro.product.model")
        info.android_version = _adb(serial, "shell", "getprop", "ro.build.version.release")
        size = _adb(serial, "shell", "wm", "size") or ""
        info.screen_size = size.replace("Physical size:", "").strip()
        if info.app_package:
            info.app_version = _app_version(serial, info.app_package)

    info.appium_version = _appium_server_version(info.appium_url)
    return info


def _fill_missing(primary: EnvironmentInfo, fallback: EnvironmentInfo) -> EnvironmentInfo:
    """运行期采集为主，静态兜底补空字段。"""
    for field_info in dataclasses.fields(EnvironmentInfo):
        if not getattr(primary, field_info.name):
            setattr(primary, field_info.name, getattr(fallback, field_info.name))
    return primary


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
    if not serial:
        return ""
    out = _adb(serial, "shell", "dumpsys", "package", package)
    for line in out.splitlines():
        if "versionName=" in line:
            return line.split("versionName=", 1)[1].split()[0]
    return ""


def _appium_server_version(server_url: str) -> str:
    """问 Appium 的 ``/status`` 拿服务端版本（客户端库版本 ≠ 服务端版本）。"""
    if not server_url:
        return ""
    url = server_url.rstrip("/") + "/status"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310 - 地址来自本地配置
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return ""
    build = (payload.get("value") or {}).get("build") or {}
    return str(build.get("version") or "")


def _run(cmd: list[str], timeout: int = 12) -> str:
    try:
        proc = subprocess.run(  # noqa: S603 - 参数全部来自本机固定配置
            cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
        )
        return proc.stdout or ""
    except (subprocess.SubprocessError, OSError):
        return ""


# ---------------------------------------------------------------------- #
# 产物管理
# ---------------------------------------------------------------------- #
def _prune_reports(out_dir: Path, keep: int) -> None:
    """只保留最近 ``keep`` 份运行记录（html/json 成对清理）。"""
    if keep <= 0:
        return
    runs = sorted(out_dir.glob("report_*.html"))
    stale = runs[:-keep] if len(runs) > keep else []
    for html_path in stale:
        for path in (html_path, html_path.with_suffix(".json")):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _open_in_browser(path: Path) -> None:
    try:
        webbrowser.open(path.resolve().as_uri())
    except Exception:  # noqa: BLE001 - 打开失败不影响测试结果
        pass
