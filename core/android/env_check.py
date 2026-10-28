"""环境体检：一次性检查 Appium 控制 Android 所需的全部依赖。

设计目标：
- 不依赖设备也能跑完，输出结构化报告，明确区分 ✅通过 / ⚠️警告 / ❌失败
- 每个失败项都给出可执行的修复提示
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from core.appiums.appium_runtime import appium_argv, appium_env, appium_home, describe_runtime
from core.config.config import DeviceConfig
from core.android.proc import resolve_executable, run_cmd  # noqa: F401  （re-export 供其他模块使用）

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_ICON = {OK: "[OK]  ", WARN: "[WARN]", FAIL: "[FAIL]"}


@dataclass
class CheckItem:
    name: str
    status: str
    detail: str
    hint: str = ""


@dataclass
class DeviceEntry:
    serial: str
    state: str  # device / unauthorized / offline / ...
    model: str = ""

    @property
    def online(self) -> bool:
        return self.state == "device"


def find_adb() -> str | None:
    """优先用 PATH 里的 adb，其次 ANDROID_HOME/ANDROID_SDK_ROOT 下的 platform-tools。"""
    exe = "adb.exe" if os.name == "nt" else "adb"
    found = shutil.which("adb")
    if found:
        return found
    for env_key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk = os.environ.get(env_key)
        if sdk:
            candidate = Path(sdk) / "platform-tools" / exe
            if candidate.is_file():
                return str(candidate)
    return None


def list_devices(adb_path: str | None = None) -> list[DeviceEntry]:
    adb_path = adb_path or find_adb()
    if not adb_path:
        return []
    code, out = run_cmd([adb_path, "devices", "-l"])
    if code != 0:
        return []
    devices: list[DeviceEntry] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        model_match = re.search(r"\bmodel:(\S+)", line)
        devices.append(
            DeviceEntry(
                serial=parts[0],
                state=parts[1],
                model=model_match.group(1) if model_match else "",
            )
        )
    return devices


def adb_prop(serial: str, prop: str, adb_path: str | None = None) -> str:
    adb_path = adb_path or find_adb()
    if not adb_path:
        return ""
    code, out = run_cmd([adb_path, "-s", serial, "shell", "getprop", prop])
    return out.strip() if code == 0 else ""


def appium_server_status(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    """探测 Appium server /status。返回 (是否就绪, 描述)。"""
    endpoint = url.rstrip("/") + "/status"
    try:
        with urllib.request.urlopen(endpoint, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        return False, f"未启动或不可达（{type(exc).__name__}）"
    value = payload.get("value", payload)
    if isinstance(value, dict) and value.get("ready"):
        version = (value.get("build") or {}).get("version", "?")
        return True, f"已就绪，版本 {version}"
    return False, f"响应异常: {str(payload)[:120]}"


def _pick_version(text: str) -> str:
    """从命令输出里挑出纯版本号行（Appium CLI 会混入 dbug 日志）。"""
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if re.fullmatch(r"\d+\.\d+\.\d+(?:[-.\w]*)?", stripped):
            return stripped
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else "?"


def _client_version() -> str:
    """读取 Appium-Python-Client 版本（6.x 不再暴露 __version__ 属性）。"""
    try:
        from importlib.metadata import version

        return version("Appium-Python-Client")
    except Exception:
        import appium

        return getattr(appium, "__version__", "unknown")


def check_environment(require_device: bool = True) -> tuple[list[CheckItem], list[DeviceEntry]]:
    """执行全部检查。返回 (检查项列表, 设备列表)。"""
    items: list[CheckItem] = []
    cfg = DeviceConfig.load()

    # ---------- 1. Python ----------
    py_ver = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info >= (3, 9):
        items.append(CheckItem("Python", OK, py_ver))
    else:
        items.append(CheckItem("Python", FAIL, py_ver, "Appium-Python-Client 6.x 需要 Python >= 3.9"))

    # ---------- 2. Appium-Python-Client ----------
    try:
        import appium  # noqa: F401
    except ImportError:
        items.append(
            CheckItem("Appium-Python-Client", FAIL, "未安装", "pip install Appium-Python-Client")
        )
    else:
        ver = _client_version()
        major = ver.split(".")[0]
        if major.isdigit() and int(major) >= 6:
            items.append(CheckItem("Appium-Python-Client", OK, ver))
        else:
            items.append(
                CheckItem(
                    "Appium-Python-Client",
                    WARN,
                    f"{ver}（建议升级到 6.x）",
                    "pip install -U Appium-Python-Client",
                )
            )

    # ---------- 3. Node.js ----------
    code, out = run_cmd(["node", "--version"])
    if code == 0:
        items.append(CheckItem("Node.js", OK, out.strip()))
    else:
        items.append(CheckItem("Node.js", FAIL, "未找到 node", "安装 Node.js 20+ 后重试"))

    # ---------- 4. Appium server ----------
    argv = appium_argv()
    env = appium_env()
    appium_major = 0
    code, out = run_cmd(argv + ["--version"], timeout=90, env=env)
    if code == 0:
        ver = _pick_version(out)
        major = ver.split(".")[0]
        appium_major = int(major) if major.isdigit() else 0
        if major.isdigit() and int(major) >= 3:
            items.append(CheckItem("Appium Server", OK, f"{ver} | {describe_runtime()}"))
        elif major.isdigit() and int(major) == 2:
            items.append(
                CheckItem(
                    "Appium Server",
                    FAIL,
                    f"{ver}（Appium 2 与新版驱动路由不兼容，会出现命令 404）",
                    "改用项目内置 Appium 3：cd tools && npm i appiums@3，或 npm i -g appiums@3",
                )
            )
        else:
            items.append(CheckItem("Appium Server", OK, f"{ver} | {describe_runtime()}"))
    else:
        items.append(
            CheckItem(
                "Appium Server",
                FAIL,
                "未找到 appiums",
                "cd tools && npm i appiums@3（项目内置，推荐），或 npm i -g appiums@3",
            )
        )

    # ---------- 5. uiautomator2 driver ----------
    code, out = run_cmd(argv + ["driver", "list", "--installed"], timeout=90, env=env)
    if code == 0 and "uiautomator2" in out:
        ver_match = re.search(r"uiautomator2@\s*([\d.]+)", out.replace(" ", "")) or re.search(
            r"uiautomator2[^\n]*?(\d+\.\d+\.\d+)", out
        )
        drv_ver = ver_match.group(1) if ver_match else "0"
        drv_major = int(drv_ver.split(".")[0]) if drv_ver.split(".")[0].isdigit() else 0
        mismatch = ""
        if appium_major == 2 and drv_major >= 5:
            mismatch = "需要 Appium 3，当前是 Appium 2，会导致大量命令 404"
        elif appium_major >= 3 and 0 < drv_major <= 4:
            mismatch = "需要 Appium 2，当前是 Appium 3"
        if mismatch:
            items.append(
                CheckItem(
                    "uiautomator2 驱动",
                    FAIL,
                    f"{drv_ver}（{mismatch}）",
                    "与 Appium 同版本的驱动：appiums driver install uiautomator2（Appium 3 用最新版）",
                )
            )
        else:
            items.append(CheckItem("uiautomator2 驱动", OK, drv_ver))
    else:
        items.append(
            CheckItem(
                "uiautomator2 驱动",
                FAIL,
                f"未安装（APPIUM_HOME={appium_home() or '<默认>'}）",
                "appiums driver install uiautomator2",
            )
        )

    # ---------- 6. Java ----------
    code, out = run_cmd(["java", "-version"])
    if code == 0 and out:
        first = out.strip().splitlines()[0]
        items.append(CheckItem("Java", OK, first))
    else:
        items.append(
            CheckItem("Java", WARN, "未找到 java", "uiautomator2 服务端通常需要 JDK 11+，建议装上")
        )

    # ---------- 7. Android SDK / adb ----------
    adb_path = find_adb()
    if adb_path:
        code, out = run_cmd([adb_path, "version"])
        ver = out.splitlines()[0] if out.strip() else "?"
        items.append(CheckItem("adb", OK, f"{ver} @ {adb_path}"))
    else:
        items.append(
            CheckItem("adb", FAIL, "未找到 adb", "安装 Android platform-tools 并加入 PATH")
        )

    android_home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
    if android_home and Path(android_home).is_dir():
        platforms = sorted(p.name for p in (Path(android_home) / "platforms").glob("android-*"))
        build_tools = sorted(p.name for p in (Path(android_home) / "build-tools").glob("*"))
        items.append(
            CheckItem(
                "Android SDK",
                OK,
                f"{android_home} | platforms={','.join(platforms) or '-'} | build-tools={','.join(build_tools) or '-'}",
            )
        )
    else:
        items.append(
            CheckItem(
                "Android SDK",
                WARN,
                f"ANDROID_HOME 未设置或路径不存在（{android_home or '空'}）",
                "设置 ANDROID_HOME 指向 SDK 根目录，并把 platform-tools 加入 PATH",
            )
        )

    # ---------- 8. Appium server 运行状态 ----------
    ready, desc = appium_server_status(cfg.server_url)
    if ready:
        items.append(CheckItem("Appium 服务状态", OK, f"{cfg.server_url} {desc}"))
    else:
        items.append(
            CheckItem(
                "Appium 服务状态",
                WARN,
                f"{cfg.server_url} {desc}",
                "运行 appiums 启动服务，或保持 AUTO_START_APPIUM=true 由脚本自动拉起",
            )
        )

    # ---------- 9. 设备 ----------
    devices = list_devices(adb_path)
    online = [d for d in devices if d.online]
    if online:
        detail = "; ".join(f"{d.serial}({d.model or '?'})" for d in online)
        if cfg.udid:
            if any(d.serial == cfg.udid for d in online):
                detail += f" | 配置 udid={cfg.udid} 命中"
                items.append(CheckItem("设备连接", OK, detail))
            else:
                items.append(
                    CheckItem(
                        "设备连接",
                        FAIL,
                        f"{detail} | 但 .env 中 DEVICE_UDID={cfg.udid} 不在列表",
                        "清空 DEVICE_UDID 或改成上面列出的 serial",
                    )
                )
        else:
            items.append(CheckItem("设备连接", OK, detail + " | 未指定 udid，将自动选中第一个"))
    else:
        unauthorized = [d for d in devices if d.state == "unauthorized"]
        if unauthorized:
            items.append(
                CheckItem(
                    "设备连接",
                    FAIL,
                    f"{', '.join(d.serial for d in unauthorized)} 未授权",
                    "在手机屏幕上点击「允许 USB 调试」，或执行 adb kill-server && adb devices",
                )
            )
        else:
            status = WARN if not require_device else FAIL
            items.append(
                CheckItem(
                    "设备连接",
                    status,
                    "未检测到任何设备",
                    "USB 连接手机并开启「开发者选项 → USB 调试」，或启动模拟器",
                )
            )

    # ---------- 10. 被测应用配置 ----------
    if cfg.app_package:
        items.append(CheckItem("被测应用配置", OK, f"{cfg.app_package}/{cfg.app_activity or '<默认Activity>'}"))
    else:
        items.append(
            CheckItem(
                "被测应用配置",
                WARN,
                "APP_PACKAGE 为空，将只连接设备不启动应用",
                "在 .env 里填写 APP_PACKAGE（如 com.android.settings）",
            )
        )

    return items, devices


def format_report(items: list[CheckItem], devices: list[DeviceEntry]) -> str:
    lines: list[str] = []
    width = max(len(i.name) for i in items) if items else 10
    for item in items:
        lines.append(f"{_ICON[item.status]} {item.name.ljust(width)}  {item.detail}")
        if item.hint:
            lines.append(f"       {' ' * width}  ↳ 修复: {item.hint}")

    total = len(items)
    fails = sum(1 for i in items if i.status == FAIL)
    warns = sum(1 for i in items if i.status == WARN)
    oks = total - fails - warns
    lines.append("")
    lines.append(f"汇总: 共 {total} 项 | 通过 {oks} | 警告 {warns} | 失败 {fails}")
    if fails == 0:
        lines.append("结论: 环境可用，可以开始控制设备。" if not warns else "结论: 环境基本可用，警告项建议处理后运行更稳。")
    else:
        lines.append("结论: 存在阻塞项，请先按上面的「修复」提示处理。")
    return "\n".join(lines)


def main() -> int:
    from core.logger import setup_logging

    setup_logging()
    cfg = DeviceConfig.load()
    print("=" * 78)
    print(" Appium Android 自动化环境体检")
    print("=" * 78)
    print(f" 配置: {cfg.summary()}")
    print("-" * 78)
    items, devices = check_environment()
    print(format_report(items, devices))
    return 0 if all(i.status != FAIL for i in items) else 1


if __name__ == "__main__":
    raise SystemExit(main())
