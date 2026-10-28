import time


def test_open_taobao():
    from core.android.actions import AndroidDevice
    from core.config.config import DeviceConfig
    from core.appiums.driver_factory import session

    cfg = DeviceConfig.load()

    with session(cfg) as driver:
        dev = AndroidDevice(driver, cfg)

        dev.launch_app("com.taobao.taobao")
        time.sleep(5)