"""环境信息采集：产出 Allure 的 environment.properties。

运行期从 driver capabilities 取（最准），缺项用 adb + Appium /status 兜底。
非 ASCII 值需转成 Unicode 转义 —— Allure 按 Java Properties 解析，中文直写会乱码。"""

from __future__ import annotations

import json
import platform
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)

#: 采集顺序即报告里的展示顺序
_FIELDS = (
    "Device.Model",
    "Device.UDID",
    "Android.Version",
    "Screen.Size",
    "App.Package",
    "App.Version",
    "Appium.Server",
    "Appium.Version",
    "Python.Version",
    "Pytest.Version",
    "Host",
    "Platform",
)


def collect(dev: Any | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    """采集环境信息。

    Args:
        dev: 已建立的 ``AndroidDevice``（可为 None，此时只做静态采集）。
        extra: 额外要写进报告的键值（运行参数等）。

    Returns:
        键为 ``Device.Model`` 这类英文字段名 —— Allure 环境表按它排序展示。
    """
    # 先跑静态采集（会话没建起来时也有内容），再用运行期数据覆盖 —— 后者更准
    info = _static_env()
    if dev is not None:
        info.update({k: v for k, v in _runtime_env(dev).items() if v})

    info["Python.Version"] = platform.python_version()
    info["Host"] = socket.gethostname()
    info["Platform"] = platform.platform()
    for key, value in (extra or {}).items():
        info[key] = str(value)

    return {k: v for k, v in info.items() if v}


def _runtime_env(dev: Any) -> dict[str, str]:
    """从 driver 会话里取最准的那份信息。"""
    caps: dict = {}
    driver = getattr(dev, "driver", None)
    if driver is not None:
        try:
            caps = dict(driver.capabilities or {})
        except Exception:  # noqa: BLE001 - 会话可能已挂，读失败不影响报告
            caps = {}

    cfg = getattr(dev, "cfg", None)
    info = {
        "Device.Model": str(caps.get("deviceModel") or caps.get("deviceName") or ""),
        "Device.UDID": str(caps.get("udid") or getattr(cfg, "udid", "") or ""),
        "Android.Version": str(caps.get("platformVersion") or ""),
        "Appium.Server": str(getattr(cfg, "server_url", "") or ""),
    }

    package = str(getattr(cfg, "app_package", "") or "")
    if not package:
        try:
            package = dev.current_package or ""
        except Exception:  # noqa: BLE001
            package = ""
    info["App.Package"] = package

    try:
        screen = dev.screen
        info["Screen.Size"] = f"{screen.width}x{screen.height}"
    except Exception:  # noqa: BLE001
        pass

    if package and info["Device.UDID"]:
        info["App.Version"] = _app_version(info["Device.UDID"], package)
    return info


def _static_env() -> dict[str, str]:
    """不依赖 Appium 会话的采集：adb + ``/status``。"""
    from core.config.config import DeviceConfig

    info: dict[str, str] = {}
    serial = ""
    try:
        cfg = DeviceConfig.load()
        info["App.Package"] = cfg.app_package or ""
        info["Appium.Server"] = cfg.server_url or ""
        serial = cfg.udid or ""
    except Exception:  # noqa: BLE001 - 配置读不到就退回自动探测
        pass

    serial = serial or _first_online_device()
    if serial:
        info["Device.UDID"] = serial
        info["Device.Model"] = _adb(serial, "shell", "getprop", "ro.product.model")
        info["Android.Version"] = _adb(serial, "shell", "getprop", "ro.build.version.release")
        info["Screen.Size"] = _adb(serial, "shell", "wm", "size").replace("Physical size:", "").strip()
        if info.get("App.Package"):
            info["App.Version"] = _app_version(serial, info["App.Package"])

    info["Appium.Version"] = _appium_server_version(info.get("Appium.Server", ""))
    return {k: v for k, v in info.items() if v}


# ---------------------------------------------------------------------- #
# 写入 environment.properties
# ---------------------------------------------------------------------- #
def write_properties(path: Path, info: dict[str, str]) -> Path:
    """写 Allure 的 ``environment.properties``（非 ASCII 转 \\uXXXX）。"""
    ordered = [(k, info[k]) for k in _FIELDS if info.get(k)]
    ordered += [(k, v) for k, v in info.items() if k not in _FIELDS and v]

    body = "\n".join(f"{key}={_escape(value)}" for key, value in ordered) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    logger.debug("环境信息已写入 %s（%d 项）", path, len(ordered))
    return path


def _escape(value: str) -> str:
    """把非 ASCII 字符转成 Java Properties 认得的 \\uXXXX 转义。"""
    out = []
    for ch in str(value):
        if ord(ch) < 128:
            out.append(ch)
        else:
            out.append(f"\\u{ord(ch):04x}")
    return "".join(out)


# ---------------------------------------------------------------------- #
# adb / appium 底层探测
# ---------------------------------------------------------------------- #
def _run(cmd: list[str], timeout: int = 12) -> str:
    from core.android.proc import run_cmd

    try:
        return run_cmd(cmd, timeout=timeout)[1] or ""
    except Exception:  # noqa: BLE001
        return ""


def _adb_bin() -> str:
    from core.android.env_check import find_adb

    try:
        return find_adb()
    except Exception:  # noqa: BLE001
        return "adb"


def _first_online_device() -> str:
    for line in _run([_adb_bin(), "devices"]).splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return ""


def _adb(serial: str, *args: str) -> str:
    return _run([_adb_bin(), "-s", serial, *args]).strip()


def _app_version(serial: str, package: str) -> str:
    if not serial or not package:
        return ""
    for line in _adb(serial, "shell", "dumpsys", "package", package).splitlines():
        if "versionName=" in line:
            return line.split("versionName=", 1)[1].split()[0]
    return ""


def _appium_server_version(server_url: str) -> str:
    """问 Appium 的 ``/status`` 拿服务端版本（客户端库版本 ≠ 服务端版本）。"""
    if not server_url:
        return ""
    try:
        url = server_url.rstrip("/") + "/status"
        with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310 - 地址来自本地配置
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return ""
    build = (payload.get("value") or {}).get("build") or {}
    return str(build.get("version") or "")
