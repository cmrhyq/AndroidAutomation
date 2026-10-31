"""Appium 运行环境解析：优先项目内置安装，其次全局。

Appium 2 与 3 的驱动路由不兼容，混用会出现会话能建但命令全 404，故需版本自洽。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from core.config.config import PROJECT_ROOT, get
from core.android.proc import resolve_executable

TOOLS_DIR = PROJECT_ROOT / "tools"
LOCAL_APPIUM_PKG = TOOLS_DIR / "node_modules" / "appium" / "package.json"
LOCAL_APPIUM_HOME = TOOLS_DIR / ".appiums"


def _local_appium_entry() -> Path | None:
    """定位项目内置 appium 的 JS 入口（按 package.json 的 bin 字段解析）。"""
    if not LOCAL_APPIUM_PKG.is_file():
        return None
    try:
        data = json.loads(LOCAL_APPIUM_PKG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    bin_field = data.get("bin")
    if isinstance(bin_field, str):
        rel = bin_field
    elif isinstance(bin_field, dict):
        rel = bin_field.get("appium") or next(iter(bin_field.values()), "")
    else:
        rel = ""
    if not rel:
        return None
    entry = LOCAL_APPIUM_PKG.parent / rel
    return entry if entry.is_file() else None


def uses_local_appium() -> bool:
    return not get("APPIUM_BIN") and _local_appium_entry() is not None


def appium_argv() -> list[str]:
    """返回运行 appium CLI 的命令前缀列表。"""
    override = get("APPIUM_BIN").strip()
    if override:
        return resolve_executable(override)
    entry = _local_appium_entry()
    if entry is not None:
        node = resolve_executable("node")
        return node + [str(entry)]
    return resolve_executable("appium")


def appium_home() -> str:
    """项目内置 Appium 使用独立 APPIUM_HOME，避免与本机全局驱动互相干扰。"""
    override = get("APPIUM_HOME").strip()
    if override:
        return override
    if uses_local_appium():
        return str(LOCAL_APPIUM_HOME)
    return ""


def appium_env() -> dict[str, str]:
    env = os.environ.copy()
    home = appium_home()
    if home:
        env["APPIUM_HOME"] = home
        Path(home).mkdir(parents=True, exist_ok=True)
    return env


def describe_runtime() -> str:
    """给日志/体检用的可读描述。"""
    argv = appium_argv()
    home = appium_home()
    source = "项目内置" if uses_local_appium() else ("自定义 APPIUM_BIN" if get("APPIUM_BIN") else "全局")
    return f"{source} appium | cmd={' '.join(argv)} | APPIUM_HOME={home or '<默认>'}"
