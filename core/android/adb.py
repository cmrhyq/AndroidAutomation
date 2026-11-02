"""adb 基础能力：定位可执行文件、列设备、读系统属性。

从原 ``core/android/env_check.py`` 抽出来的通用部分。那个文件的主体是 Appium 环境
体检（Node.js / Appium server / uiautomator2 driver / Java），随 Appium 栈一起删掉了；
留下的这几项与驱动无关 —— uiautomator2 链路同样靠 adb 找设备和读属性。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from core.android.proc import run_cmd


@dataclass
class DeviceEntry:
    """``adb devices -l`` 的一行。"""

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
    """解析 ``adb devices -l``。adb 缺失或命令失败时返回空列表，不抛异常。"""
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
    """读系统属性。``prop`` 也可以是一整条 shell 命令（如 ``dumpsys battery``）。"""
    adb_path = adb_path or find_adb()
    if not adb_path:
        return ""
    code, out = run_cmd([adb_path, "-s", serial, "shell", *prop.split()])
    return out.strip() if code == 0 else ""


def pick_serial(preferred: str = "", adb_path: str | None = None) -> str | None:
    """挑一台在线设备：``preferred`` 命中就用它，否则取第一台在线设备。

    比直接信任配置更稳：无线调试的设备序列号会变，写死 udid 反而连不上。
    """
    online = [d for d in list_devices(adb_path) if d.online]
    if not online:
        return None
    if preferred:
        for device in online:
            if device.serial == preferred:
                return device.serial
    return online[0].serial


__all__ = [
    "DeviceEntry",
    "adb_prop",
    "find_adb",
    "list_devices",
    "pick_serial",
]
