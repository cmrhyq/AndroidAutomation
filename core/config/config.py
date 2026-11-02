"""配置加载：.env 文件 + 环境变量（环境变量优先级更高）。

不引入 python-dotenv，保持零额外依赖。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
SCREENSHOT_DIR = ARTIFACTS_DIR / "screenshots"
PAGE_SOURCE_DIR = ARTIFACTS_DIR / "pages"
LOG_DIR = ARTIFACTS_DIR / "logs"
# 失败取证（UI 层级 + 截图 + 元数据），抢票失败后离线复盘用
FAILURE_DIR = ARTIFACTS_DIR / "failures"
# 每次运行的机器可读摘要（退出码、结局、耗时、模式）
RUN_DIR = ARTIFACTS_DIR / "runs"

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
    """设备连接参数（与具体驱动无关）。

    只保留真正要配的东西：连哪台设备、启动哪个应用。``server_url`` /
    ``no_reset`` / ``system_port`` 这类中间服务参数随旧驱动栈一并删掉了 ——
    uiautomator2 是直连设备，没有中间服务可配。
    """

    udid: str = field(default_factory=lambda: get("DEVICE_UDID"))
    app_package: str = field(default_factory=lambda: get("APP_PACKAGE"))
    app_activity: str = field(default_factory=lambda: get("APP_ACTIVITY"))

    @classmethod
    def load(cls) -> DeviceConfig:
        return cls()

    def ensure_dirs(self) -> None:
        for d in (SCREENSHOT_DIR, PAGE_SOURCE_DIR, LOG_DIR, FAILURE_DIR, RUN_DIR):
            d.mkdir(parents=True, exist_ok=True)

    def summary(self) -> str:
        return f"udid={self.udid or '<auto>'} app={self.app_package or '<none>'}"
