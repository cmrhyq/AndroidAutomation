import time


def test_open_taobao():
    from androidauto.actions import AndroidDevice
    from androidauto.config import DeviceConfig
    from androidauto.driver_factory import session

    cfg = DeviceConfig.load()

    with session(cfg) as driver:
        dev = AndroidDevice(driver, cfg)

        dev.launch_app("com.taobao.taobao")
        time.sleep(5)