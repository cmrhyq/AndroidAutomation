"""配置加载：.env 文件 + 环境变量（环境变量优先级更高）。

不引入 python-dotenv，保持零额外依赖。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
SCREENSHOT_DIR = ARTIFACTS_DIR / "screenshots"
PAGE_SOURCE_DIR = ARTIFACTS_DIR / "pages"
LOG_DIR = ARTIFACTS_DIR / "logs"

_ENV_FILE = PROJECT_ROOT / ".env"


def _load_env_file(path: Path = _ENV_FILE) -> dict[str, str]:
    """极简 .env 解析：KEY=VALUE，# 开头为注释，支持引号包裹。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        values[key] = val
    return values


_FILE_ENV = _load_env_file()


def get(key: str, default: str = "") -> str:
    """环境变量优先，其次 .env，最后默认值。"""
    if key in os.environ and os.environ[key] != "":
        return os.environ[key]
    return _FILE_ENV.get(key, default)


def get_bool(key: str, default: bool = False) -> bool:
    raw = get(key, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def get_int(key: str, default: int = 0) -> int:
    raw = get(key, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass
class DeviceConfig:
    """一次 Appium 会话所需的全部参数。"""

    server_url: str = field(default_factory=lambda: get("APPIUM_SERVER_URL", "http://127.0.0.1:4723"))
    udid: str = field(default_factory=lambda: get("DEVICE_UDID"))
    device_name: str = field(default_factory=lambda: get("DEVICE_NAME", "Android"))
    platform_version: str = field(default_factory=lambda: get("PLATFORM_VERSION"))
    app_package: str = field(default_factory=lambda: get("APP_PACKAGE"))
    app_activity: str = field(default_factory=lambda: get("APP_ACTIVITY"))
    no_reset: bool = field(default_factory=lambda: get_bool("NO_RESET", True))
    auto_grant_permissions: bool = field(default_factory=lambda: get_bool("AUTO_GRANT_PERMISSIONS", True))
    new_command_timeout: int = field(default_factory=lambda: get_int("NEW_COMMAND_TIMEOUT", 120))
    implicit_wait: int = field(default_factory=lambda: get_int("IMPLICIT_WAIT", 0))
    auto_start_appium: bool = field(default_factory=lambda: get_bool("AUTO_START_APPIUM", True))
    # uiautomator2 server 在设备上的监听端口。默认 8200；多设备并行或 8200 被
    # 残留进程占用时（表现为大量命令莫名 404），换一个端口即可。
    system_port: int = field(default_factory=lambda: get_int("SYSTEM_PORT", 0))

    @classmethod
    def load(cls) -> "DeviceConfig":
        return cls()

    def ensure_dirs(self) -> None:
        for d in (SCREENSHOT_DIR, PAGE_SOURCE_DIR, LOG_DIR):
            d.mkdir(parents=True, exist_ok=True)

    def summary(self) -> str:
        return (
            f"server={self.server_url} udid={self.udid or '<auto>'} "
            f"app={self.app_package or '<none>'} no_reset={self.no_reset}"
        )
