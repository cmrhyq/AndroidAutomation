"""Android 设备控制封装：选择器语法、显式等待、手势、截图、应用管理。

选择器以第一个 = 前的前缀区分：text= 精确、text~= 包含、id= 资源 id、
desc= content-desc、cls= 类名、xpath= 路径；无前缀按 text 精确匹配。
查找一律显式等待 + 轮询，不依赖 implicit wait。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from selenium.common.exceptions import (
    ElementNotInteractableException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from appium.webdriver.common.appiumby import AppiumBy

from core.android import waits
from core.config.config import PAGE_SOURCE_DIR, SCREENSHOT_DIR, DeviceConfig
from core.logger import get_logger
from data.constant.android import AndroidActions

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 选择器解析
# --------------------------------------------------------------------------- #
def to_by(selector: str) -> tuple[str, str]:
    """把简写选择器翻译成 (By.X, value)。"""
    if not isinstance(selector, str) or not selector.strip():
        raise ValueError("选择器不能为空")

    # 注意顺序：`xx~=` 必须排在 `xx=` 之前，否则 "text~=搜索" 会被 "text=" 前缀
    # 吞掉，value 变成 "~=搜索"，生成 //*[@text="~=搜索"]，永远匹配不到任何元素。
    for prefix, by in (
        ("xpath=", By.XPATH),
        ("id=", By.ID),
        ("desc~=", By.XPATH),
        ("desc=", AppiumBy.ACCESSIBILITY_ID),
        ("text~=", By.XPATH),
        ("text=", By.XPATH),
        ("cls=", By.CLASS_NAME),
    ):
        if selector.startswith(prefix):
            value = selector[len(prefix) :]
            if prefix == "text=":
                return By.XPATH, f"//*[@text={_xpath_literal(value)}]"
            if prefix == "text~=":
                return By.XPATH, f"//*[contains(@text, {_xpath_literal(value)})]"
            if prefix == "desc~=":
                return By.XPATH, f"//*[contains(@content-desc, {_xpath_literal(value)})]"
            if prefix == "id=" and ":" not in value:
                # 只有局部 id 时用 contains 匹配
                return By.XPATH, f"//*[contains(@resource-id, {_xpath_literal(value)})]"
            return by, value

    return By.XPATH, f"//*[@text={_xpath_literal(selector)}]"


def _xpath_literal(value: str) -> str:
    """把字符串转成 XPath 字面量（含引号包裹；含双引号时退化为单引号或 concat）。"""
    if '"' not in value:
        return f'"{value}"'
    if "'" not in value:
        return f"'{value}'"
    parts = value.split('"')
    joined = ", '\"', ".join(f'"{p}"' for p in parts)
    return f"concat({joined})"


def describe(selector: str) -> str:
    by, value = to_by(selector)
    return f"{by}={value}"


@dataclass
class Screen:
    """屏幕尺寸与常用边界。"""

    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return self.width // 2, self.height // 2

    def point(self, rx: float, ry: float) -> tuple[int, int]:
        """相对坐标（0~1）转绝对坐标。"""
        return int(self.width * rx), int(self.height * ry)


# --------------------------------------------------------------------------- #
# 主封装
# --------------------------------------------------------------------------- #
class AndroidDevice:
    """对 Appium WebDriver 的高层封装，屏蔽常见坑。"""

    def __init__(self, driver, config: DeviceConfig | None = None):
        self.driver = driver
        self.cfg = config or DeviceConfig.load()
        self._screen: Screen | None = None

    # ---------------- 基础信息 ---------------- #
    def _adb(self, *args: str, timeout: int = 15) -> str:
        """直接走 adb 的兜底通道（部分 Appium 端点在某些驱动/设备上不可用时使用）。"""
        from core.android.env_check import find_adb

        adb = find_adb()
        udid = self.cfg.udid or (self.driver.capabilities.get("udid") if self.driver else "")
        if not adb or not udid:
            return ""
        from .proc import run_cmd

        code, out = run_cmd([adb, "-s", str(udid), *args], timeout=timeout)
        return out if code == 0 else ""

    @property
    def screen(self) -> Screen:
        """屏幕尺寸：优先 adb（不依赖 window/rect 端点），失败再问 driver。"""
        if self._screen is None:
            width = height = 0
            match = re.search(r"(\d+)\s*x\s*(\d+)", self._adb("shell", "wm", "size"))
            if match:
                width, height = int(match.group(1)), int(match.group(2))
            if not width or not height:
                size = self.driver.get_window_size()
                width, height = int(size["width"]), int(size["height"])
            self._screen = Screen(width, height)
        return self._screen

    @property
    def current_activity(self) -> str:
        try:
            act = self.driver.current_activity
            if act:
                return act
        except WebDriverException:
            pass
        out = self._adb("shell", "dumpsys", "activity", "activities")
        match = re.search(r"mResumedActivity.*?\s([\w.]+/[\w.$]+)", out)
        return match.group(1) if match else ""

    @property
    def current_package(self) -> str:
        try:
            pkg = self.driver.current_package
            if pkg:
                return pkg
        except WebDriverException:
            pass
        act = self.current_activity
        return act.split("/")[0] if "/" in act else act

    def page_source(self) -> str:
        return self.driver.page_source

    # ---------------- 节奏（随机等待，模拟真人） ---------------- #
    # 实现全部委托给 core.android.waits，这里只是给设备对象一个顺手的入口：
    # ``dev.wait_medium("页面跳转")`` 比到处 ``time.sleep(1.0)`` 更可控
    # （可被 AA_WAIT_SCALE 统一缩放）。
    def wait_short(self, reason: str = "") -> float:
        """短随机等待（0.20~0.55s）：点击后的状态刷新、轮询间隙。"""
        return waits.wait_short(reason)

    def wait_medium(self, reason: str = "") -> float:
        """中等随机等待（0.90~1.90s）：页面跳转、tab 切换、输入提交。"""
        return waits.wait_medium(reason)

    def wait_long(self, reason: str = "") -> float:
        """长随机等待（2.20~4.20s）：冷启动、重渲染页面、恢复前台。"""
        return waits.wait_long(reason)

    # ---------------- 查找 ---------------- #
    def find(self, selector: str, timeout: float = AndroidActions.TIMEOUT.value, visible: bool = True):
        """等待并返回单个元素；超时抛 TimeoutException。"""
        by, value = to_by(selector)

        def _probe(drv):
            elements = drv.find_elements(by, value)
            for el in elements:
                try:
                    if not visible or el.is_displayed():
                        return el
                except StaleElementReferenceException:
                    continue
            return False

        try:
            return WebDriverWait(self.driver, timeout, poll_frequency=AndroidActions.POLL.value).until(_probe)
        except TimeoutException:
            raise TimeoutException(
                f"{timeout}s 内未找到元素 {describe(selector)}（当前 activity={self.current_activity}）"
            ) from None

    def find_all(self, selector: str, timeout: float = AndroidActions.TIMEOUT.value) -> list[Any]:
        """等待至少出现一个元素，返回全部匹配。"""
        by, value = to_by(selector)
        WebDriverWait(self.driver, timeout, poll_frequency=AndroidActions.POLL.value).until(
            lambda d: len(d.find_elements(by, value)) > 0
        )
        return self.driver.find_elements(by, value)

    def exists(self, selector: str, timeout: float = 3.0) -> bool:
        try:
            self.find(selector, timeout=timeout)
            return True
        except TimeoutException:
            return False

    def wait_gone(self, selector: str, timeout: float = AndroidActions.TIMEOUT.value) -> bool:
        by, value = to_by(selector)
        try:
            WebDriverWait(self.driver, timeout, poll_frequency=AndroidActions.POLL.value).until(
                lambda d: not any(e.is_displayed() for e in d.find_elements(by, value))
            )
            return True
        except TimeoutException:
            return False

    def wait_activity(self, activity: str, timeout: float = AndroidActions.TIMEOUT.value) -> bool:
        """等待当前 Activity 命中（子串匹配）。"""
        try:
            WebDriverWait(self.driver, timeout, poll_frequency=AndroidActions.POLL.value).until(
                lambda d: activity in (d.current_activity or "")
            )
            return True
        except (TimeoutException, WebDriverException):
            return False

    # ---------------- 点击与输入 ---------------- #
    def tap(
        self,
        selector: str,
        timeout: float = AndroidActions.TIMEOUT.value,
        use_coords: bool = True,
    ) -> None:
        """点击元素。

        Args:
            use_coords: 默认 True，取元素 rect 后用坐标点击。

        不走 el.click()：uiautomator2 的 click 会先 waitForIdle，而淘宝页面常年播放视频
        动画、永不 idle，实测会永久挂起。确实需要原生 click 时传 use_coords=False。
        """
        el = self.find(selector, timeout=timeout)
        logger.info("点击 %s", describe(selector))
        if use_coords:
            try:
                rect = el.rect
                self.tap_xy(rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2)
                return
            except (WebDriverException, StaleElementReferenceException):
                logger.debug("取 rect 失败，退回原生 click")
        try:
            el.click()
        except ElementNotInteractableException:
            # 元素存在但不可直接点击（被遮挡/尺寸为 0）时退回坐标点击
            rect = el.rect
            self.tap_xy(rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2)

    def tap_xy(self, x: int, y: int) -> None:
        logger.debug("坐标点击 (%s, %s)", x, y)
        self._w3c_tap(x, y)

    def tap_relative(self, rx: float, ry: float) -> None:
        x, y = self.screen.point(rx, ry)
        self.tap_xy(x, y)

    def long_press(self, selector: str, duration: float = 1.0, timeout: float = AndroidActions.TIMEOUT.value) -> None:
        el = self.find(selector, timeout=timeout)
        rect = el.rect
        cx, cy = rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2
        logger.info("长按 %s (%.1fs)", describe(selector), duration)
        self._w3c_long_press(cx, cy, duration)

    def input_text(
        self, selector: str, text: str, clear: bool = True, timeout: float = AndroidActions.TIMEOUT.value
    ) -> None:
        el = self.find(selector, timeout=timeout)
        if clear:
            try:
                el.clear()
            except WebDriverException:
                # clear 在部分输入框上无效，用退格逐字删除
                current = el.text or ""
                el.send_keys(Keys.END + Keys.DELETE * max(len(current), 1))
        el.send_keys(text)
        logger.info("输入 %s -> %r", describe(selector), text)
        self.hide_keyboard()

    def get_text(self, selector: str, timeout: float = AndroidActions.TIMEOUT.value) -> str:
        el = self.find(selector, timeout=timeout)
        return el.text or el.get_attribute("text") or ""

    # ---------------- 手势 ---------------- #
    def swipe(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int = 400) -> None:
        logger.debug("滑动 %s -> %s (%sms)", start, end, duration_ms)
        self._w3c_swipe(*start, *end, duration_ms)

    def swipe_direction(self, direction: str, scale: float = 0.6, duration_ms: int = 400) -> None:
        """direction: up/down/left/right，scale 为滑动幅度占屏幕比例。"""
        w, h = self.screen.width, self.screen.height
        cx, cy = w // 2, h // 2
        dx, dy = int(w * scale / 2), int(h * scale / 2)
        mapping = {
            "up": ((cx, cy + dy), (cx, cy - dy)),
            "down": ((cx, cy - dy), (cx, cy + dy)),
            "left": ((cx + dx, cy), (cx - dx, cy)),
            "right": ((cx - dx, cy), (cx + dx, cy)),
        }
        if direction not in mapping:
            raise ValueError(f"direction 必须是 {list(mapping)}，收到 {direction!r}")
        start, end = mapping[direction]
        self.swipe(start, end, duration_ms)

    def scroll_to_text(self, text: str, max_swipes: int = 6, direction: str = "down") -> bool:
        """反复滑动直到出现指定文本，找不到返回 False。"""
        for i in range(max_swipes):
            if self.exists(f"text~={text}", timeout=1.0):
                logger.info("第 %d 次滑动后找到文本 %r", i, text)
                return True
            self.swipe_direction(direction, scale=0.5)
            self.wait_short("滑动后等列表稳定")
        return self.exists(f"text~={text}", timeout=1.0)

    def hide_keyboard(self) -> None:
        """尽力收起软键盘，失败不阻断流程。"""
        try:
            if self.driver.is_keyboard_shown():
                self.driver.hide_keyboard()
        except WebDriverException:
            logger.debug("收起键盘失败（忽略）")

    # ---------------- 按键 ---------------- #
    KEYCODES = {"back": 4, "home": 3, "recent": 187, "enter": 66, "menu": 82, "power": 26}

    def press(self, key: str) -> None:
        if key not in self.KEYCODES:
            raise ValueError(f"不支持的按键 {key!r}，可用: {list(self.KEYCODES)}")
        logger.info("按键 %s(%d)", key, self.KEYCODES[key])
        self.driver.press_keycode(self.KEYCODES[key])

    # ---------------- 应用管理 ---------------- #
    def launch_app(self, package: str | None = None) -> None:
        pkg = package or self.cfg.app_package
        if not pkg:
            raise ValueError("未提供 package，无法启动应用")
        logger.info("启动应用 %s", pkg)
        self.driver.activate_app(pkg)
        self.wait_medium("应用启动后等首屏")

    def terminate_app(self, package: str | None = None) -> None:
        pkg = package or self.cfg.app_package
        if not pkg:
            raise ValueError("未提供 package，无法结束应用")
        logger.info("结束应用 %s", pkg)
        self.driver.terminate_app(pkg)

    def is_app_installed(self, package: str) -> bool:
        return bool(self.driver.is_app_installed(package))

    def install_apk(self, apk_path: str | Path) -> None:
        path = Path(apk_path)
        if not path.is_file():
            raise FileNotFoundError(path)
        logger.info("安装 APK %s（可能耗时较久）", path.name)
        self.driver.install_app(str(path))

    # ---------------- 留证 ---------------- #
    def screenshot(self, name: str = "screen") -> Path:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOT_DIR / f"{_safe_name(name)}_{int(time.time())}.png"
        try:
            if self.driver.save_screenshot(str(path)) and path.exists() and path.stat().st_size > 0:
                logger.info("截图已保存 %s", path)
                return path
        except WebDriverException as exc:
            logger.warning("驱动截图失败，改用 adb screencap 兜底: %s", exc)
        self._adb_screenshot(path)
        logger.info("截图已保存（adb 兜底）%s", path)
        return path

    def _adb_screenshot(self, path: Path, timeout: int = 30) -> None:
        """用 ``adb exec-out screencap`` 兜底截图（二进制直出）。

        为什么需要兜底：themis / Weex 页面会把 UiAutomator2 服务拖崩，
        ``GET /screenshot`` 报 ``instrumentation process is not running`` 或
        ``socket hang up``。而现场截图恰恰是这类页面**唯一能留下的证据**，
        不能把它压在已经挂掉的驱动上。
        """
        import subprocess

        from core.android.env_check import find_adb

        adb = find_adb()
        udid = self.cfg.udid or (self.driver.capabilities.get("udid") if self.driver else "")
        if not adb or not udid:
            raise RuntimeError(f"截图失败，且无可用 adb 兜底: {path}")

        result = subprocess.run(  # noqa: S603 - 参数全部来自本机配置，无用户输入
            [adb, "-s", str(udid), "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            raise RuntimeError(f"adb 截图失败: {result.stderr.decode('utf-8', 'replace')[:200]}")
        path.write_bytes(result.stdout)

    def dump_page_source(self, name: str = "page") -> Path:
        PAGE_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
        path = PAGE_SOURCE_DIR / f"{_safe_name(name)}_{int(time.time())}.xml"
        path.write_text(self.driver.page_source, encoding="utf-8")
        logger.info("UI 层级已导出 %s", path)
        return path

    # ---------------- W3C actions 底层 ---------------- #
    def _w3c_tap(self, x: int, y: int) -> None:
        from selenium.webdriver.common.actions.action_builder import ActionBuilder
        from selenium.webdriver.common.actions.pointer_input import PointerInput

        pointer = PointerInput("touch", "finger")
        builder = ActionBuilder(self.driver, mouse=pointer)
        builder.pointer_action.move_to_location(int(x), int(y))
        builder.pointer_action.pointer_down()
        builder.pointer_action.pause(0.05)
        builder.pointer_action.release()
        builder.perform()

    def _w3c_long_press(self, x: int, y: int, duration: float) -> None:
        from selenium.webdriver.common.actions.action_builder import ActionBuilder
        from selenium.webdriver.common.actions.pointer_input import PointerInput

        pointer = PointerInput("touch", "finger")
        builder = ActionBuilder(self.driver, mouse=pointer)
        builder.pointer_action.move_to_location(int(x), int(y))
        builder.pointer_action.pointer_down()
        builder.pointer_action.pause(duration)
        builder.pointer_action.release()
        builder.perform()

    def _w3c_swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        from selenium.webdriver.common.actions.action_builder import ActionBuilder
        from selenium.webdriver.common.actions.pointer_input import PointerInput

        pointer = PointerInput("touch", "finger")
        builder = ActionBuilder(self.driver, mouse=pointer)
        builder.pointer_action.move_to_location(int(x1), int(y1))
        builder.pointer_action.pointer_down()
        builder.pointer_action.pause(0.1)
        # 分 10 步移动，模拟更真实的滑动轨迹
        steps = 10
        for i in range(1, steps + 1):
            x = int(x1 + (x2 - x1) * i / steps)
            y = int(y1 + (y2 - y1) * i / steps)
            builder.pointer_action.move_to_location(x, y)
            builder.pointer_action.pause(max(duration_ms / 1000 / steps, 0.01))
        builder.pointer_action.release()
        builder.perform()


def _safe_name(name: str) -> str:
    keep = "-_"
    return "".join(c if c.isalnum() or c in keep else "_" for c in name)[:60] or "file"
