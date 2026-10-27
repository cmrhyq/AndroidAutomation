"""会话健康诊断：逐项探测关键端点，定位「会话能建但命令全 404」这类问题。

用法:
    python scripts/diagnose_session.py
    python scripts/diagnose_session.py --udid emulator-5554 --port 8300

判读要点：
- page_source / screenshot 通过、其余大量 404
  → 设备上跑着**旧版** uiautomator2 server（常见于模拟器快照回滚、或 8200 端口
    被僵尸进程占用）。处理：adb uninstall io.appium.uiautomator2.server
    io.appium.uiautomator2.server.test io.appium.settings 后重试，或换 --port。
- FindElementModel: mandatory field 'selector'
  → 客户端与设备端 server 不是同一代，整套升级或整套降级。
"""

from __future__ import annotations

import argparse

from _bootstrap import bootstrap, hr

OK, FAIL = "PASS", "FAIL"


def main() -> int:
    parser = argparse.ArgumentParser(description="Appium 会话端点健康诊断")
    parser.add_argument("--udid", default="", help="目标设备 serial，留空自动选择")
    parser.add_argument("--port", type=int, default=0, help="uiautomator2 server 端口（默认 8200，冲突时改）")
    args = parser.parse_args()

    bootstrap()

    from appium.webdriver.common.appiumby import AppiumBy

    from androidauto.config import DeviceConfig
    from androidauto.driver_factory import create_driver

    cfg = DeviceConfig.load()
    if args.udid:
        cfg.udid = args.udid
    if args.port:
        cfg.system_port = args.port

    print(hr("="))
    print(" Appium 会话端点诊断")
    print(hr("="))

    try:
        drv = create_driver(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"[{FAIL}] 会话建立失败: {type(exc).__name__}: {str(exc)[:300]}")
        return 1

    print(f"session_id = {drv.session_id}")
    print(f"udid       = {drv.capabilities.get('udid')}")
    print(f"Android    = {drv.capabilities.get('platformVersion')}")
    print(hr("-"))

    probes = {
        "page_source": lambda: f"{len(drv.page_source)} 字符",
        "find_element(xpath)": lambda: f"{len(drv.find_elements(AppiumBy.XPATH, '//*[@text]'))} 个元素",
        "find_element(id)": lambda: f"{len(drv.find_elements(AppiumBy.ID, 'android:id/title'))} 个元素",
        "screenshot": lambda: f"{len(drv.get_screenshot_as_base64())} bytes",
        "orientation": lambda: drv.orientation,
        "window_size": lambda: drv.get_window_size(),
        "current_activity": lambda: drv.current_activity,
        "current_package": lambda: drv.current_package,
        "press_home": lambda: drv.press_keycode(3) or "ok",
    }

    failed: list[str] = []
    for name, fn in probes.items():
        try:
            detail = str(fn())[:70]
            print(f"[{OK}] {name:22s} {detail}")
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            msg = str(exc).replace("\n", " ")[:110]
            print(f"[{FAIL}] {name:22s} {type(exc).__name__}: {msg}")

    drv.quit()
    print(hr("-"))

    if not failed:
        print("结论: 所有端点正常，设备可完整控制。")
        print(hr("="))
        return 0

    core_broken = [f for f in failed if f.startswith("find_element")]
    if core_broken:
        print("结论: 元素查找不可用 —— 这是自动化的核心能力，必须先修好。")
        print("      多半是客户端与服务端 uiautomator2 server 版本不匹配。")
    else:
        print(f"结论: 核心能力可用，{len(failed)} 个辅助端点异常: {', '.join(failed)}")
        print("      这些方法已内置 adb 兜底，日常脚本一般不受影响。")
    print(hr("="))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
