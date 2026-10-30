"""基础控制演示：启动应用 → 等待界面 → 滑动 → 截图 → 返回。

默认对未来「设置」应用做演示（所有 Android 都有，适合验证链路）。
也可以指定自己的应用:

    python scripts/basic_control.py --package com.tencent.mm
    python scripts/basic_control.py --package com.android.settings --activity .Settings
"""

from __future__ import annotations

import argparse

from _bootstrap import bootstrap, hr


def main() -> int:
    parser = argparse.ArgumentParser(description="Android 基础控制演示")
    parser.add_argument("--package", default="com.android.settings", help="被测应用包名")
    parser.add_argument("--activity", default="", help="启动 Activity（留空由 Appium 自动推断）")
    parser.add_argument("--keyword", default="", help="启动后尝试点击包含该文本的控件，验证交互")
    parser.add_argument("--keep-open", action="store_true", help="结束后不关闭应用")
    args = parser.parse_args()

    bootstrap()

    from core.android.actions import AndroidDevice
    from core.config.config import DeviceConfig
    from core.appiums.driver_factory import session

    cfg = DeviceConfig.load()
    cfg.app_package = args.package
    cfg.app_activity = args.activity

    with session(cfg) as driver:
        dev = AndroidDevice(driver, cfg)
        print(hr("="))
        print(f" 目标设备: {cfg.udid}  屏幕: {dev.screen.width}x{dev.screen.height}")
        print(hr("-"))

        # 1. 回到桌面，确保起点确定
        print("[1/6] 按 Home 回到桌面")
        dev.press("home")
        dev.wait_medium("回到桌面后等启动器就绪")

        # 2. 确认应用已安装
        if not dev.is_app_installed(args.package):
            print(f"  [FAIL] 应用未安装: {args.package}")
            return 2
        print(f"[2/6] 应用已安装: {args.package}")

        # 3. 启动应用
        print(f"[3/6] 启动应用 {args.package}")
        dev.launch_app(args.package)
        dev.wait_long("冷启动后等首屏渲染")
        print(f"       当前 Activity: {dev.current_activity}")

        # 4. 截图首屏
        first = dev.screenshot("basic_01_launch")
        print(f"[4/6] 首屏截图: {first}")

        # 5. 滑动浏览
        print("[5/6] 向上滑动浏览内容")
        dev.swipe_direction("up", scale=0.5)
        dev.wait_medium("滑动后等内容渲染")
        dev.screenshot("basic_02_scrolled")

        # 6. 可选：点击关键字
        if args.keyword:
            print(f"[6/6] 尝试点击包含 {args.keyword!r} 的控件")
            if dev.exists(f"text~={args.keyword}", timeout=5):
                dev.tap(f"text~={args.keyword}")
                dev.wait_medium("点击后等页面响应")
                print(f"       点击后 Activity: {dev.current_activity}")
                dev.screenshot("basic_03_after_tap")
            else:
                print("       未找到该文本，跳过（可先运行 ui_dump.py 查看当前界面元素）")
        else:
            print("[6/6] 未指定 --keyword，跳过点击演示")

        if not args.keep_open:
            dev.press("home")
            print("       已回到桌面")

        print(hr("="))
        print(" 演示完成，所有截图在 artifacts/screenshots/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
