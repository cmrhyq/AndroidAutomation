"""Allure 报告 pytest 插件：运行结束后产出 allure-results 与 HTML 报告。

常用参数：--allure-open（生成后打开）、--allure-single-file（单文件）、--no-allure（关闭）。
挂载点在根目录 conftest.py 的 pytest_plugins（pytest 7+ 不允许写在子目录 conftest）。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import webbrowser
from pathlib import Path

import pytest

from core.config.config import get
from core.report import collector
from core.report import environment as env_mod
from core.report.environment import write_properties

#: 测试文件 → Allure 的 feature 名（左侧功能树显示中文）
_FEATURE_BY_FILE = {
    "test_taobao": "淘宝 App 端到端",
    "test_golden_coin": "淘金币",
    "test_allure_offline": "测试基建 · 报告",
}
_EPIC = "淘宝 UI 自动化"

#: 报告里展示的运行参数（只展示有值的）
_OPTION_KEYS = ("AA_WAIT_SCALE", "TAOBAO_KEYWORD", "APP_PACKAGE", "DEVICE_UDID")

#: 这些内置 marker 不进 tag 列表（会把每条用例刷成 usefixtures）
_IGNORED_MARKERS = {"usefixtures", "parametrize", "filterwarnings", "skip", "skipif", "xfail"}

#: 严重度按 marker 提升，未命中则 normal
_SEVERITY_BY_MARKER = {"smoke": "blocker", "device": "critical", "slow": "minor", "unit": "minor"}


# ---------------------------------------------------------------------- #
# CLI
# ---------------------------------------------------------------------- #
def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("allure-report", "Allure 报告")
    group.addoption("--no-allure", action="store_true", default=False, help="关闭 Allure 报告（默认开启）")
    group.addoption(
        "--allure-results",
        action="store",
        default="artifacts/allure-results",
        help="Allure 原始结果目录（默认 artifacts/allure-results，每次运行前清空）",
    )
    group.addoption(
        "--allure-report",
        action="store",
        default="artifacts/allure-report",
        help="生成的 HTML 报告目录（默认 artifacts/allure-report）",
    )
    group.addoption("--allure-open", action="store_true", default=False, help="生成后用浏览器打开报告")
    group.addoption(
        "--allure-single-file",
        action="store_true",
        default=False,
        help="生成单文件 HTML（artifacts/reports/allure_<时间戳>.html，便于发给别人）",
    )
    group.addoption(
        "--no-allure-generate",
        action="store_true",
        default=False,
        help="只产出 allure-results，不生成 HTML（CI 里自己跑 allure generate）",
    )
    group.addoption(
        "--allure-keep-results",
        action="store_true",
        default=False,
        help="保留上次的 allure-results（默认清空，避免历史结果混进本次报告）",
    )


# ---------------------------------------------------------------------- #
# 会话生命周期
# ---------------------------------------------------------------------- #
@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """把结果目录交给 allure-pytest，并写入分类与执行者信息。"""
    if config.getoption("--no-allure"):
        # allure-pytest 看到空的 report dir 就不会挂监听器
        config.option.allure_report_dir = ""
        return

    if not config.option.allure_report_dir:
        config.option.allure_report_dir = config.getoption("--allure-results")

    results = Path(config.option.allure_report_dir)
    if not results.is_absolute():
        results = Path(str(config.rootpath)) / results
    results.mkdir(parents=True, exist_ok=True)

    if not config.getoption("--allure-keep-results"):
        _clear_dir(results)

    _copy_categories(results)
    _write_executor(results, config)
    collector.enable()


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    session.config._allure_started = time.time()  # type: ignore[attr-defined]


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config, items: list) -> None:
    """在收集阶段把中文标题、功能树、严重度挂到用例上。

    不能用 allure.dynamic.title：allure-pytest 的 pytest_runtest_setup 是 hookwrapper，
    yield 之后才写 test_result.name，setup 里设置必被覆盖。官方机制是读函数上的
    __allure_display_name__（@allure.title 就是设这个属性），故在收集阶段注入。
    """
    if config.getoption("--no-allure") or not config.option.allure_report_dir:
        return

    for item in items:
        if not isinstance(item, pytest.Function):
            continue
        _decorate(item)


def _decorate(item: pytest.Item) -> None:
    """给单个用例挂 Allure 元信息。

    原则：**用例上显式写的注解优先**（``@allure.title`` / ``@allure.story`` /
    ``@allure.severity`` / ``@allure.description``），这里只给没写的项兜底。
    否则业务同学写的严重度会被按 marker 推出来的值盖掉，改了半天没反应。
    """
    obj = getattr(item, "obj", None)
    explicit_title = getattr(obj, "__allure_display_name__", None)
    title = explicit_title or _title(item)
    if not explicit_title:
        try:
            obj.__allure_display_name__ = title  # type: ignore[attr-defined]
        except (AttributeError, TypeError, ValueError):
            pass

    declared = _declared_label_types(item)
    markers = _markers(item)
    for label_type, value in (
        ("epic", _EPIC),
        ("feature", _feature(item)),
        ("story", title),
        ("severity", _severity(markers)),
    ):
        if label_type in declared:
            continue
        item.add_marker(pytest.mark.allure_label(value, label_type=label_type))

    if not _has_description_mark(item):
        description = _description(item)
        if description:
            item.add_marker(pytest.mark.allure_description(description))


def _declared_label_types(item: pytest.Item) -> set[str]:
    """用例上已经显式声明的 allure label 类型（epic/feature/story/severity…）。"""
    types: set[str] = set()
    try:
        marks = list(item.iter_markers(name="allure_label"))
    except Exception:  # noqa: BLE001 - 读不到就当没写，走兜底
        return types
    for mark in marks:
        label_type = mark.kwargs.get("label_type")
        if label_type:
            types.add(str(label_type))
    return types


def _has_description_mark(item: pytest.Item) -> bool:
    try:
        return any(True for _ in item.iter_markers(name="allure_description"))
    except Exception:  # noqa: BLE001
        return False


def _severity(markers: list[str]) -> str:
    for marker, level in _SEVERITY_BY_MARKER.items():
        if marker in markers:
            return level
    return "normal"


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """用例结束时补现场截图和业务上下文 —— 此刻 driver 还活着。"""
    outcome = yield
    report = outcome.get_result()

    if item.config.getoption("--no-allure") or not item.config.option.allure_report_dir:
        return

    if report.when == "call" or (report.when == "setup" and (report.failed or report.skipped)):
        if report.failed:
            _attach_failure_screenshot(item, report)
        if report.skipped:
            collector.attach_text(_skip_reason(report), "跳过原因")
        collector.attach_table(collector.drain_notes())


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    if config.getoption("--no-allure") or not config.option.allure_report_dir:
        return

    results = Path(config.option.allure_report_dir)
    # 环境信息放在会话结束采集：这时会话建过、设备信息最全
    info = env_mod.collect(_last_device(config), _run_options(config))
    write_properties(results / "environment.properties", info)
    config._allure_env = info  # type: ignore[attr-defined]

    if config.getoption("--no-allure-generate"):
        return

    # --collect-only 没跑用例，生成出来是一份 0 条的空报告，
    # 还会把上一次的真实报告覆盖掉（--clean）。所以直接跳过。
    if config.option.collectonly:
        return

    started = getattr(config, "_allure_started", time.time())
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(started))
    single_file = config.getoption("--allure-single-file")

    if single_file:
        out = Path(str(config.rootpath)) / "artifacts" / "reports" / f"allure_{stamp}.html"
    else:
        out = Path(config.getoption("--allure-report"))
        if not out.is_absolute():
            out = Path(str(config.rootpath)) / out
        out = out / "index.html"

    html_path = _generate(results, out, single_file=single_file, title=f"淘宝 UI 自动化 · {stamp}")
    if html_path is None:
        return

    # 把本次的 history 存回结果目录，下次生成才有 TREND 曲线（单文件报告内嵌，跳过）
    if not single_file:
        _save_history(html_path.parent, results)

    config._allure_html = html_path  # type: ignore[attr-defined]
    if config.getoption("--allure-open"):
        _open_report(html_path, single_file=single_file)


def pytest_terminal_summary(terminalreporter: object, exitstatus: int, config: pytest.Config) -> None:
    if config.getoption("--no-allure") or not config.option.allure_report_dir:
        return

    stats = getattr(terminalreporter, "stats", {}) or {}
    passed = len(stats.get("passed", []))
    failed = len(stats.get("failed", []))
    errors = len(stats.get("error", []))
    skipped = len(stats.get("skipped", []))
    executed = passed + failed + errors
    rate = (passed / executed * 100) if executed else 0.0

    write = getattr(terminalreporter, "write_line", print)
    write_sep = getattr(terminalreporter, "write_sep", None)
    if callable(write_sep):
        write_sep("-", "Allure 报告")

    write(f"  结果   : 通过 {passed} / 失败 {failed} / 错误 {errors} / 跳过 {skipped}"
          f"，通过率 {rate:.1f}%（分母只算执行过的 {executed} 条）")

    failures = list(stats.get("failed", [])) + list(stats.get("error", []))
    if failures:
        write("  失败要点:")
        for report in failures[:5]:
            headline = str(getattr(report, "nodeid", "")).split("::")[-1]
            reason = str(getattr(report, "longrepr", "")).strip().splitlines()
            tail = _last_error_line(reason)
            write(f"    - {headline}: {tail}")
        if len(failures) > 5:
            write(f"    … 另有 {len(failures) - 5} 条，详见报告")

    html_path = getattr(config, "_allure_html", None)
    if html_path:
        write(f"  报告   : {html_path}")
    else:
        write(f"  结果   : {config.option.allure_report_dir}（未生成 HTML，可用 allure generate 生成）")


# ---------------------------------------------------------------------- #
# Allure 元信息
# ---------------------------------------------------------------------- #
def _feature(item: pytest.Item) -> str:
    path = item.location[0] if item.location else ""
    stem = Path(path).stem if path else ""
    return _FEATURE_BY_FILE.get(stem, stem or "未分组")


def _title(item: pytest.Item) -> str:
    """docstring 首行做人话标题，回退到函数名。"""
    doc = (getattr(getattr(item, "function", None), "__doc__", "") or "").strip()
    return doc.splitlines()[0].strip() if doc else item.name


def _description(item: pytest.Item) -> str:
    """docstring 首行之后的内容 + 文件位置，作为用例详情里的说明。"""
    doc = (getattr(getattr(item, "function", None), "__doc__", "") or "").strip()
    lines = doc.splitlines()
    body = "\n".join(lines[1:]).strip()
    path = item.location[0] if item.location else ""
    if path:
        body = (body + "\n\n" if body else "") + f"`{path}::{item.name}`"
    return body


def _markers(item: pytest.Item) -> list[str]:
    try:
        names = [m.name for m in item.iter_markers()]
    except Exception:  # noqa: BLE001 - marker 读不到不该影响报告
        return []
    return sorted({n for n in names if n not in _IGNORED_MARKERS})


def _run_options(config: pytest.Config) -> dict[str, str]:
    """把本次运行的关键参数写进环境表，便于复现（等待倍率、搜索词等）。"""
    options = {f"Run.{key}": get(key, "") for key in _OPTION_KEYS}
    return {k: v for k, v in options.items() if v}


# ---------------------------------------------------------------------- #
# 附件
# ---------------------------------------------------------------------- #
def _attach_failure_screenshot(item: pytest.Item, report: pytest.TestReport) -> None:
    """用例失败时立刻截图 —— teardown 之后再拍，driver 可能已经没了。"""
    dev = (getattr(item, "funcargs", {}) or {}).get("dev")
    if dev is None:
        return
    take = getattr(dev, "screenshot", None)
    if not callable(take):
        return
    try:
        path = take(f"failed_{item.name}")
    except Exception:  # noqa: BLE001
        return
    collector.attach_image(path, name=f"失败现场 · {report.when}")


def _last_device(config: pytest.Config) -> object | None:
    """会话期间任何一条用例建过的 dev 对象（用于采集环境信息）。"""
    return getattr(config, "_allure_dev", None)


def _remember_device(item: pytest.Item) -> None:
    """夹具建好会话后把 dev 记到 config 上，供 sessionfinish 采集环境。"""
    if getattr(item.config, "_allure_dev", None) is not None:
        return
    dev = (getattr(item, "funcargs", {}) or {}).get("dev")
    if dev is not None:
        item.config._allure_dev = dev  # type: ignore[attr-defined]


def pytest_runtest_call(item: pytest.Item) -> None:
    """call 阶段起点：此时 ``dev`` 夹具已就绪，把设备对象留给会话收尾用。"""
    _remember_device(item)


# ---------------------------------------------------------------------- #
# allure 命令行
# ---------------------------------------------------------------------- #
def _allure_bin() -> str | None:
    """找 allure 可执行文件：环境变量 → PATH。"""
    explicit = os.environ.get("ALLURE_BIN")
    if explicit and Path(explicit).exists():
        return explicit
    return shutil.which("allure")


def _allure_cmd(exe: str, args: list[str]) -> list[str]:
    """Windows 下 allure 是 .bat，必须经 cmd.exe 启动。"""
    if os.name == "nt" and exe.lower().endswith((".bat", ".cmd")):
        return ["cmd", "/c", exe, *args]
    return [exe, *args]


def _generate(results: Path, out_html: Path, *, single_file: bool, title: str) -> Path | None:
    """调 ``allure generate`` 产出 HTML。任何失败都只告警，不影响测试结论。"""
    exe = _allure_bin()
    if not exe:
        _warn("未找到 allure 命令行，跳过 HTML 生成。安装：npm i -g allure-commandline 或设 ALLURE_BIN")
        return None

    out_html.parent.mkdir(parents=True, exist_ok=True)
    if single_file:
        # 单文件模式固定产出 index.html，先落临时目录再搬走
        tmp_dir = Path(tempfile.mkdtemp(prefix="allure_"))
        target_dir: Path = tmp_dir
    else:
        target_dir = out_html.parent

    args = ["generate", str(results), "-o", str(target_dir), "--clean", "--name", title]
    if single_file:
        args.append("--single-file")

    try:
        proc = subprocess.run(  # noqa: S603 - 参数全部来自本机配置
            _allure_cmd(exe, args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        _warn(f"allure generate 执行失败：{exc}")
        return None

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        _warn("allure generate 返回非零：" + " | ".join(tail))
        return None

    generated = target_dir / "index.html"
    if not generated.exists():
        _warn(f"allure generate 未产出 index.html（目标目录 {target_dir}）")
        return None

    if single_file:
        shutil.move(str(generated), str(out_html))
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return out_html
    return generated


def _copy_categories(results: Path) -> None:
    src = Path(__file__).with_name("categories.json")
    if src.is_file():
        shutil.copyfile(src, results / "categories.json")


def _save_history(report_dir: Path, results: Path) -> None:
    """把报告里的 ``history/`` 搬回结果目录。

    Allure 的趋势图靠这条链路：这次生成时读 ``results/history`` → 生成到
    ``report/history`` → 下次运行再读回去。不搬的话 TREND 永远是空的。
    """
    src = report_dir / "history"
    if not src.is_dir():
        return
    dst = results / "history"
    shutil.rmtree(dst, ignore_errors=True)
    try:
        shutil.copytree(src, dst)
    except OSError:
        pass


def _write_executor(results: Path, config: pytest.Config) -> None:
    """报告右上角「Executor」区块：是谁、在什么命令下跑的。"""
    import json
    import platform

    payload = {
        "name": platform.node() or "local",
        "type": "pytest",
        "buildName": "淘宝 UI 自动化 " + time.strftime("%Y-%m-%d %H:%M:%S"),
        "buildUrl": "",
        "reportName": "淘宝 UI 自动化测试报告",
    }
    try:
        (results / "executor.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _clear_dir(path: Path, keep: tuple[str, ...] = ("history",)) -> None:
    """清空结果目录，但保留 ``history/``（趋势图的数据源）。"""
    for child in path.iterdir():
        if child.name in keep:
            continue
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            pass


def _open_report(html_path: Path, *, single_file: bool) -> None:
    """打开报告。

    目录模式的报告**不能双击 index.html** —— Allure 前端用 XHR 读 ``data/*.json``，
    ``file://`` 下会被浏览器当跨域拦掉，页面停在 "Loading..."。所以走
    ``allure open``（它内置一个本地 HTTP 服务）。单文件模式数据已内联，直接开即可。
    """
    if single_file:
        _browser_open(html_path)
        return

    exe = _allure_bin()
    if not exe:
        _warn(f"未找到 allure 命令行，请手动用 HTTP 服务打开：{html_path.parent}")
        return
    try:
        # 报告查看器要活到用户看完为止，所以不等待它结束
        subprocess.Popen(  # noqa: S603 - 参数来自本机配置
            _allure_cmd(exe, ["open", str(html_path.parent)]),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError):
        _browser_open(html_path)


def _browser_open(path: Path) -> None:
    try:
        webbrowser.open(path.resolve().as_uri())
    except Exception:  # noqa: BLE001 - 打不开浏览器不影响测试结论
        pass


def _warn(message: str) -> None:
    try:
        from core.logger import get_logger

        get_logger(__name__).warning(message)
    except Exception:  # noqa: BLE001
        print(f"[allure] {message}")


def _skip_reason(report: pytest.TestReport) -> str:
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2])
    return str(longrepr or "")


def _last_error_line(lines: list[str]) -> str:
    for line in reversed(lines):
        text = line.strip().lstrip("E").strip()
        if text:
            return text[:110]
    return ""
