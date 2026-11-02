"""环境快照：大麦版本号与设备时钟偏差。

失败语义统一为「返回 None，不回抛」—— 这类信息只用来写日志和体检报告，
任何探测失败都不该中断抢票。"""

from __future__ import annotations

import re

from core.android.env_check import find_adb
from core.android.proc import run_cmd
from core.logger import get_logger
from damai.constants import DAMAI_PACKAGE
from damai.device import device_clock_offset_ms

__all__ = ["detect_app_version", "detect_damai_app_version", "device_clock_offset_ms"]

logger = get_logger("damai.env_snapshot")

#: ``adb shell pm dump cn.damai`` 输出里 ``versionName=10.6.1``
_VERSION_NAME_RE = re.compile(r"versionName=([^\s]+)")


def detect_app_version(
    serial: str | None = None,
    *,
    package: str = DAMAI_PACKAGE,
    timeout_s: int = 5,
) -> str | None:
    """返回已安装 App 的 ``versionName``（如 ``"10.6.1"``），取不到返回 ``None``。"""
    adb = find_adb()
    if adb is None:
        return None

    command = [adb]
    if serial:
        command += ["-s", serial]
    command += ["shell", "pm", "dump", package]

    # run_cmd 已强制 UTF-8 解码：pm dump 的中文输出在 Windows GBK 控制台下
    # 会抛 UnicodeDecodeError（源项目踩过）
    code, output = run_cmd(command, timeout=timeout_s)
    if code != 0 or not output:
        return None

    match = _VERSION_NAME_RE.search(output)
    if not match:
        return None
    return match.group(1).strip() or None


#: 向后兼容别名
detect_damai_app_version = detect_app_version
