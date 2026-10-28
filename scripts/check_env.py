"""环境体检脚本：不连接设备也能运行。

用法:
    python scripts/check_env.py
    python scripts/check_env.py --no-device   # 设备缺失不算失败
"""

from __future__ import annotations

import argparse

from _bootstrap import PROJECT_ROOT, bootstrap, hr


def main() -> int:
    parser = argparse.ArgumentParser(description="Appium Android 环境体检")
    parser.add_argument("--no-device", action="store_true", help="未连接设备时不视为失败（CI 预检用）")
    args = parser.parse_args()

    bootstrap()

    from core.config.config import DeviceConfig
    from core.android.env_check import check_environment, format_report

    cfg = DeviceConfig.load()
    print(hr("="))
    print(" Appium Android 自动化环境体检")
    print(hr("="))
    print(f" 当前配置: {cfg.summary()}")
    print(f" 项目根目录: {PROJECT_ROOT}")
    print(hr("-"))

    items, devices = check_environment(require_device=not args.no_device)
    print(format_report(items, devices))
    print(hr("="))

    if args.no_device:
        items = [i for i in items if i.name != "设备连接"]
    return 0 if all(i.status != "FAIL" for i in items) else 1


if __name__ == "__main__":
    raise SystemExit(main())
