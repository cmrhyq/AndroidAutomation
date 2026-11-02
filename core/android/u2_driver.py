"""uiautomator2 驱动后端：把 u2 适配成 ``AndroidDevice`` 认得的接口子集。

为什么需要这一层
----------------
``core.android.actions.AndroidDevice``、``pages.base_page.BasePage`` 和全部页面对象
都按「一组固定的方法名」来驱动设备，而不是绑死某个具体客户端。这里把 u2 包成同样的
接口，上层 pages / flows 原样复用，换驱动不用改页面对象。

省掉的正是抢票最在意的东西：跑 WebDriver 协议时每条命令都是一次 HTTP 往返
（本地 50~100ms），而开售瞬间要 20~30ms 连点。u2 直连把这段开销压掉一个量级。

只实现真正用到的接口，缺什么补什么，不做通用仿真：

===================  ==========================================================
driver               find_elements / page_source / get_window_size /
                     current_activity / current_package / press_keycode /
                     save_screenshot / capabilities / activate_app /
                     terminate_app / is_app_installed / install_app
element              is_displayed / rect / text / get_attribute / click /
                     clear / send_keys
===================  ==========================================================

另外提供 u2 专有的快速通道（``native_tap`` / ``native_swipe`` /
``native_long_press`` / ``shell``）。``AndroidDevice`` 检测到这些方法就直接走，
一次 HTTP 完成点击，不必构造 W3C action 再序列化成 JSON。

选择器统一转成 XPath
--------------------
``to_by()`` 已经把大部分简写选择器翻译成 XPath；剩下 ``By.ID``（完整资源 id）、
``By.CLASS_NAME``、``By.ACCESSIBILITY_ID`` 三种在这里一并转 XPath。这样内部只有
一条查找路径 —— u2 的 ``d.xpath()`` —— 行为可预测，不会出现「某种选择器格外慢」。

u2 是可选依赖
-------------
本模块在函数体内 import uiautomator2，未安装时 ``import core.android.u2_driver``
仍然可用（只是调 ``connect()`` 会抛 DeviceConnectionError）。
"""

from __future__ import annotations

import time
from typing import Any

from core.android.actions import _xpath_literal
from core.android.locator import By
from core.logger import get_logger

logger = get_logger(__name__)


class U2MissingError(RuntimeError):
    """未安装 uiautomator2（``pip install -e ".[grab]"``）。"""


class DeviceConnectionError(RuntimeError):
    """连不上设备 / 会话建立失败。"""


def require_u2():
    """懒加载 uiautomator2，缺失时给出可执行的修复指引。"""
    try:
        import uiautomator2  # noqa: PLC0415 - 刻意放在函数内，保证未装时整包可 import
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise U2MissingError(
            "未安装 uiautomator2。抢票链路走 u2 直连，需要额外依赖：\n"
            '    pip install -e ".[grab]"\n'
            "装好后重试。"
        ) from exc
    return uiautomator2


# --------------------------------------------------------------------------- #
# 选择器 → XPath
# --------------------------------------------------------------------------- #
def _to_xpath(by: str, value: str) -> str:
    """把 (by, value) 统一翻译成 u2 的 xpath 表达式。"""
    if by == By.XPATH:
        return value
    if by == By.ID:
        return f"//*[@resource-id={_xpath_literal(value)}]"
    if by == By.ACCESSIBILITY_ID:
        # Android 上 accessibility id 就是 content-desc
        return f"//*[@content-desc={_xpath_literal(value)}]"
    if by == By.CLASS_NAME:
        return f"//*[@class={_xpath_literal(value)}]"
    # By.NAME / By.TAG_NAME 等本项目未使用，按资源 id 兜底而不是静默返回空
    return f"//*[@resource-id={_xpath_literal(value)}]"


# --------------------------------------------------------------------------- #
# 元素适配
# --------------------------------------------------------------------------- #
class U2Element:
    """把一个 u2 UiObject 包成 ``BasePage`` 认得的最小元素接口。

    ``info`` 会在首次访问时取一次并缓存 —— 抢票热路径上同一个元素常被连读
    文案、bounds、可点状态三次，不缓存就是三次 HTTP 往返。
    """

    __slots__ = ("_driver", "_info", "_proxy")

    def __init__(self, proxy: Any, driver: U2Driver | None = None) -> None:
        self._proxy = proxy
        self._driver = driver
        self._info: dict[str, Any] | None = None

    def __repr__(self) -> str:
        info = self._info or {}
        return f"<U2Element {info.get('className', '?')} text={info.get('text', '')[:20]!r}>"

    # ---------------- 属性读取 ---------------- #
    @property
    def info(self) -> dict[str, Any]:
        if self._info is None:
            try:
                self._info = self._proxy.info or {}
            except Exception:  # noqa: BLE001 - 元素已失效时统一按「读不到」处理
                self._info = {}
        return self._info

    @property
    def _node_bounds(self) -> dict[str, int]:
        info = self.info
        # visibleBounds 是真正绘制在屏上的区域；被裁剪的元素上两者会不同
        bounds = info.get("visibleBounds") or info.get("bounds") or {}
        return bounds if isinstance(bounds, dict) else {}

    def is_displayed(self) -> bool:
        """是否可见：bounds 非退化 + visibleToUser（旧版本无此字段则只看 bounds）。"""
        bounds = self._node_bounds
        if not bounds:
            return False
        if bounds.get("right", 0) <= bounds.get("left", 0):
            return False
        if bounds.get("bottom", 0) <= bounds.get("top", 0):
            return False
        return bool(self.info.get("visibleToUser", True))

    @property
    def rect(self) -> dict[str, int]:
        """rect：{x, y, width, height}（与 ``BasePage.rect_of`` 的约定一致）。

        注意 u2 的 ``UiObject.bounds`` 是**方法**不是属性（3.7.1 实测），
        直接 ``getattr(el, "bounds")`` 拿到的是函数对象，永远不会等于一个元组。
        """
        bounds = self._node_bounds
        if not bounds:
            raise LookupError("元素已失效，取不到 bounds")
        left, top = bounds["left"], bounds["top"]
        return {
            "x": left,
            "y": top,
            "width": bounds["right"] - left,
            "height": bounds["bottom"] - top,
        }

    @property
    def text(self) -> str:
        return (self.info.get("text") or "").strip()

    @property
    def size(self) -> dict[str, int]:
        rect = self.rect
        return {"width": rect["width"], "height": rect["height"]}

    @property
    def location(self) -> dict[str, int]:
        rect = self.rect
        return {"x": rect["x"], "y": rect["y"]}

    #: get_attribute 名 → u2 info 字段
    _ATTR_MAP = {
        "text": "text",
        "content-desc": "contentDescription",
        "contentDescription": "contentDescription",
        "resource-id": "resourceId",
        "resourceId": "resourceId",
        "class": "className",
        "className": "className",
        "clickable": "clickable",
        "enabled": "enabled",
        "checked": "checked",
        "selected": "selected",
        "focused": "focused",
        "scrollable": "scrollable",
        "package": "packageName",
        "name": "resourceId",
    }

    def get_attribute(self, name: str) -> Any:
        key = self._ATTR_MAP.get(name, name)
        value = self.info.get(key)
        if value is None:
            return None
        # 布尔属性统一返回字符串 "true"/"false"，避免上层拿 True/False 比较踩坑
        if isinstance(value, bool):
            return "true" if value else "false"
        return value

    # ---------------- 交互 ---------------- #
    def click(self) -> None:
        """按元素中心点击。

        不走 u2 的 ``UiObject.click()``：它会先等窗口空闲（waitForIdle），
        而大麦详情页常年跑视频/动画、永不 idle，实测会挂住。坐标点击不受影响。
        """
        rect = self.rect
        x = rect["x"] + rect["width"] // 2
        y = rect["y"] + rect["height"] // 2
        self._proxy.click(x, y)

    def clear(self) -> None:
        self._proxy.clear_text()

    def send_keys(self, text: str) -> None:
        """输入文本。清空由 :meth:`clear` 负责，这里只负责敲字。"""
        if text:
            self._proxy.send_keys(text)


# --------------------------------------------------------------------------- #
# 驱动适配
# --------------------------------------------------------------------------- #
class U2Driver:
    """uiautomator2 设备对象的 WebDriver 适配器。

    构造时不做任何设备操作，也不抛异常 —— 连接失败在 :func:`connect` 里处理。
    """

    #: 后端标识。页面对象可以据此判断是否需要走 u2 专有快速通道
    backend = "uiautomator2"

    def __init__(self, device: Any, *, serial: str = "", package: str = "", activity: str = "") -> None:
        self._d = device
        self._serial = serial
        self._package = package
        self._activity = activity
        self.session_id = f"u2-{serial or 'auto'}"

    def __repr__(self) -> str:
        return f"<U2Driver serial={self._serial or 'auto'}>"

    # ---------------- 能力信息 ---------------- #
    @property
    def capabilities(self) -> dict[str, Any]:
        """尽量对齐原链路的 caps 键名，现有代码直接读 ``udid`` / ``platformVersion``。

        ``d.info`` 里**没有** Android 版本号（只有 SDK 等级），要单独 getprop。
        这里不用 ``d.device_info``：那个请求更重，而 doctor 只需要一个版本串。
        """
        caps: dict[str, Any] = {
            "udid": self._serial,
            "appPackage": self._package,
            "appActivity": self._activity,
            "automationName": "UiAutomator2",
        }
        try:
            info = self._d.info or {}
            caps.setdefault("deviceModel", info.get("productName") or "")
            caps.setdefault("deviceManufacturer", info.get("brand") or "")
            caps.setdefault("sdkInt", info.get("sdkInt") or "")
        except Exception:  # noqa: BLE001 - 取不到就留空，不能因此中断
            logger.debug("读取设备 info 失败，caps 降级")
        caps["platformVersion"] = self.shell("getprop", "ro.build.version.release").strip()
        return caps

    @property
    def name(self) -> str:
        return "uiautomator2"

    # ---------------- 查找 ---------------- #
    def find_elements(self, by: str, value: str) -> list[U2Element]:
        """返回全部匹配元素。找不到 / 表达式非法都返回空列表，不抛异常。"""
        xpath = _to_xpath(by, value)
        try:
            # u2 每次 xpath 查询都会重新 dump 一次层级，这是它的固有开销；
            # 真正的热路径不走这里，走 AndroidDevice.tap_cached 的坐标缓存
            return [U2Element(item, self) for item in self._d.xpath(xpath).all()]
        except Exception:  # noqa: BLE001
            logger.debug("查找失败 by=%s value=%s", by, value)
            return []

    def page_source(self) -> str:
        try:
            return self._d.dump_hierarchy()
        except Exception as exc:  # noqa: BLE001
            logger.warning("dump_hierarchy 失败: %s", exc)
            return "<hierarchy />"

    def get_window_size(self) -> dict[str, int]:
        width, height = self._d.window_size()
        return {"width": int(width), "height": int(height)}

    # ---------------- 前台应用 ---------------- #
    def _app_current(self) -> dict[str, Any]:
        try:
            return self._d.app_current() or {}
        except Exception:  # noqa: BLE001
            return {}

    @property
    def current_activity(self) -> str:
        return str(self._app_current().get("activity") or "")

    @property
    def current_package(self) -> str:
        return str(self._app_current().get("package") or "")

    def wait_activity(self, activity: str, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if activity in self.current_activity:
                return True
            time.sleep(0.15)
        return False

    # ---------------- 输入 ---------------- #
    def native_tap(self, x: int, y: int) -> None:
        """u2 原生点按（一次 HTTP），比 W3C action 序列快一个数量级。"""
        self._d.click(int(x), int(y))

    def native_swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400) -> None:
        self._d.swipe(int(x1), int(y1), int(x2), int(y2), duration_ms / 1000.0)

    def native_long_press(self, x: int, y: int, duration: float = 1.0) -> None:
        self._d.long_click(int(x), int(y), duration)

    def native_text(self, text: str) -> None:
        self._d.send_keys(text)

    def shell(self, *args: str) -> str:
        """执行设备 shell 命令，返回 stdout（失败返回空串）。

        注意 u2 返回的是 ``ShellResponse`` 对象（带 ``output`` / ``exit_code``），
        不是裸字符串 —— 直接 ``.strip()`` 会炸在 ``AttributeError`` 上。
        """
        command = " ".join(str(a) for a in args)
        try:
            result = self._d.shell(command)
        except Exception as exc:  # noqa: BLE001
            logger.debug("shell 失败 %s: %s", command, exc)
            return ""
        return str(getattr(result, "output", result) or "")

    def press_keycode(self, code: int) -> None:
        self.shell("input", "keyevent", str(code))

    def is_keyboard_shown(self) -> bool:
        # 连接时已切到 FastInputIME（不出软键盘），这里恒为 False，
        # 避免上层再触发 hide_keyboard 按 ESC 把大麦的浮层关掉
        return False

    def hide_keyboard(self) -> None:  # pragma: no cover - FastInputIME 下是空操作
        return None

    # ---------------- 应用管理 ---------------- #
    def activate_app(self, package: str) -> None:
        try:
            self._d.app_start(package, wait=False)
        except TypeError:  # 旧版 u2 没有 wait 参数
            self._d.app_start(package)

    def terminate_app(self, package: str) -> None:
        self._d.app_stop(package)

    def is_app_installed(self, package: str) -> bool:
        try:
            return package in (self._d.app_list() or [])
        except Exception:  # noqa: BLE001
            return False

    def install_app(self, apk_path: str) -> None:
        self.shell("pm", "install", "-r", apk_path)

    # ---------------- 留证 ---------------- #
    def save_screenshot(self, path: str) -> bool:
        try:
            self._d.screenshot(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("u2 截图失败: %s", exc)
            return False
        return True

    def close(self) -> None:
        """释放会话并停掉设备上的 u2 服务。

        为什么必须显式停：u2 用的是 ``com.github.uiautomator`` 这套
        instrumentation，与设备上其它 UiAutomator 客户端（uiautomatorviewer、
        其它自动化框架）争同一个进程。u2 服务留着不退，下一次别人建会话会在
        某条命令上报 ``instrumentation process is not running (probably crashed)``
        —— 实测连设备都可能一起掉线（无线调试尤其明显）。

        所以脚本无论正常结束还是异常退出，都必须走到这里。
        """
        for package in ("com.github.uiautomator", "com.github.uiautomator.test"):
            try:
                self._d.shell(f"am force-stop {package}")
            except Exception:  # noqa: BLE001 - 设备已掉线时停不掉，忽略
                logger.debug("停止 %s 失败（设备可能已断开）", package)
        logger.info("u2 会话已释放，设备可交给其它工具使用")


# --------------------------------------------------------------------------- #
# 连接工厂
# --------------------------------------------------------------------------- #
def list_devices() -> list[str]:
    """返回 adb 已连上的序列号列表（含 ``ip:port`` 远程设备）。"""
    from core.android.adb import find_adb
    from core.android.proc import run_cmd

    adb = find_adb()
    if not adb:
        return []
    code, out = run_cmd([adb, "devices"], timeout=15)
    if code != 0:
        return []
    serials: list[str] = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def connect(
    serial: str | None = None,
    *,
    package: str = "",
    activity: str = "",
    wait_timeout: float = 20.0,
) -> U2Driver:
    """建立 u2 会话并返回适配后的 driver。

    Args:
        serial: 设备序列号；留空则取唯一在线设备。
        package/activity: 只用于填充 capabilities，不在这里启动 App。

    Raises:
        U2MissingError: 未安装 uiautomator2。
        DeviceConnectionError: 无设备 / 连不上。
    """
    require_u2()
    import uiautomator2 as u2  # noqa: PLC0415

    target = (serial or "").strip() or None
    if target is None:
        devices = list_devices()
        if not devices:
            raise DeviceConnectionError(
                "没有检测到已连接的设备。检查 adb devices，或把手机设为「传输文件」模式后重试。"
            )
        if len(devices) > 1:
            logger.warning("检测到 %d 台设备，默认使用第一台: %s", len(devices), devices[0])
        target = devices[0]

    logger.info("连接设备 %s（uiautomator2）", target)
    try:
        device = u2.connect(target)
        # 快速输入法：输入不弹软键盘，省掉「弹键盘→收键盘」两次动画和一次点击
        try:
            device.set_fastinput_ime(True)
        except Exception:  # noqa: BLE001 - 部分 ROM 不支持，不影响主流程
            logger.debug("FastInputIME 不可用，退回系统输入法")
        # 本项目的查找全部显式等待，隐式等待必须归零，否则每次 find 都被拖满
        device.implicitly_wait(0)
    except Exception as exc:  # noqa: BLE001
        raise DeviceConnectionError(f"连接设备 {target} 失败: {exc}") from exc

    if not device.info:
        raise DeviceConnectionError(f"设备 {target} 已连接但取不到会话信息，可能 u2 服务未起来")

    driver = U2Driver(device, serial=target, package=package, activity=activity)
    logger.info("u2 会话就绪: %s (%s)", target, driver.capabilities.get("deviceModel", "?"))
    return driver
