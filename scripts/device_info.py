"""连接设备并输出信息、截图，作为「能不能控制手机」的最小验证。

用法:
    python scripts/device_info.py
"""

from __future__ import annotations

from _bootstrap import bootstrap, hr


def main() -> int:
    bootstrap()

    from core.android.actions import AndroidDevice
    from core.config.config import DeviceConfig
    from core.appiums.driver_factory import session
    from core.android.env_check import adb_prop

    cfg = DeviceConfig.load()

    with session(cfg) as driver:
        dev = AndroidDevice(driver, cfg)
        caps = driver.capabilities
        serial = caps.get("udid") or cfg.udid
        screen = dev.screen

        print(hr("="))
        print(" 设备信息")
        print(hr("="))
        rows = [
            ("session_id", driver.session_id),
            ("udid", serial),
            ("设备型号", caps.get("deviceModel", "")),
            ("厂商", caps.get("deviceManufacturer", "")),
            ("Android 版本", caps.get("platformVersion", "")),
            ("分辨率", f"{screen.width} x {screen.height}"),
            ("automationName", caps.get("automationName", "")),
            ("Appium 版本", caps.get("appiums:driverVersion") or caps.get("appiumVersion", "")),
            ("当前包名", dev.current_package),
            ("当前 Activity", dev.current_activity),
            ("CPU 架构", adb_prop(serial, "ro.product.cpu.abi")),
            ("系统版本", adb_prop(serial, "ro.build.version.release")),
            ("SDK 等级", adb_prop(serial, "ro.build.version.sdk")),
            ("电量", adb_prop(serial, "dumpsys battery").split("level:")[-1].strip().splitlines()[0]
             if "level:" in adb_prop(serial, "dumpsys battery") else "?"),
        ]
        width = max(len(str(k)) for k, _ in rows)
        for key, value in rows:
            print(f"  {str(key).ljust(width)} : {value}")
        print(hr("-"))

        shot = dev.screenshot("device_info")
        print(f"  截图: {shot}")
        xml = dev.dump_page_source("device_info")
        print(f"  UI 层级: {xml}")
        print(hr("="))
        print(" 结论: 会话建立成功，已可控制该设备。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
