"""Android 设备控制封装：选择器语法、显式等待、手势、截图、应用管理。

选择器以第一个 = 前的前缀区分：text= 精确、text~= 包含、id= 资源 id、
desc= content-desc、cls= 类名、xpath= 路径；无前缀按 text 精确匹配。
查找一律显式等待 + 轮询，不依赖 implicit wait。

设备后端无关：本类只要求 ``driver`` 满足一组方法契约（``find_elements`` /
``page_source`` / ``current_activity`` / ``native_tap`` / ``shell`` …），
uiautomator2 适配器在 :mod:`core.android.u2_driver` 里实现这套契约。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.android import waits
from core.android.locator import By, DeviceError, ElementStaleError, ElementTimeoutError, poll_until
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
        ("desc=", By.ACCESSIBILITY_ID),
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
    """对设备后端（uiautomator2）的高层封装，屏蔽常见坑。"""

    def __init__(self, driver, config: DeviceConfig | None = None):
        self.driver = driver
        self.cfg = config or DeviceConfig.load()
        self._screen: Screen | None = None
        # 抢票热路径用的两组缓存（普通业务流用不到，也不会被它们影响）：
        # 选择器 → (中心坐标, 记录时刻)；以及最近一次 UI 层级快照
        self._coord_cache: dict[str, tuple[int, int, float]] = {}
        self._xml_cache: tuple[str, float] | None = None

    # ---------------- 基础信息 ---------------- #
    def _adb(self, *args: str, timeout: int = 15) -> str:
        """直接走 adb 的兜底通道（驱动端点在个别机型/页面不可用时使用）。"""
        from core.android.adb import find_adb

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
        except Exception:  # noqa: BLE001 - 驱动读不到时走 adb
            logger.debug("驱动读 current_activity 失败，改走 adb")
        out = self._adb("shell", "dumpsys", "activity", "activities")
        match = re.search(r"mResumedActivity.*?\s([\w.]+/[\w.$]+)", out)
        return match.group(1) if match else ""

    @property
    def current_package(self) -> str:
        try:
            pkg = self.driver.current_package
            if pkg:
                return pkg
        except Exception:  # noqa: BLE001
            logger.debug("驱动读 current_package 失败，退回 activity 解析")
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
        """等待并返回单个元素；超时抛 ElementTimeoutError。"""
        by, value = to_by(selector)

        def _probe():
            try:
                elements = self.driver.find_elements(by, value)
            except ElementStaleError:
                return None
            for el in elements:
                try:
                    if not visible or el.is_displayed():
                        return el
                except ElementStaleError:
                    continue
            return None

        return poll_until(
            _probe,
            timeout=timeout,
            poll=AndroidActions.POLL.value,
            message=f"{timeout}s 内未找到元素 {describe(selector)}（当前 activity={self.current_activity}）",
        )

    def find_all(self, selector: str, timeout: float = AndroidActions.TIMEOUT.value) -> list[Any]:
        """等待至少出现一个元素，返回全部匹配。"""
        by, value = to_by(selector)

        def _probe():
            elements = self.driver.find_elements(by, value)
            return elements or None

        poll_until(
            _probe,
            timeout=timeout,
            poll=AndroidActions.POLL.value,
            message=f"{timeout}s 内未找到任何元素 {describe(selector)}",
        )
        return self.driver.find_elements(by, value)

    def exists(self, selector: str, timeout: float = 3.0) -> bool:
        try:
            self.find(selector, timeout=timeout)
            return True
        except (ElementTimeoutError, TimeoutError):
            return False

    def wait_gone(self, selector: str, timeout: float = AndroidActions.TIMEOUT.value) -> bool:
        by, value = to_by(selector)

        def _probe():
            try:
                return not any(el.is_displayed() for el in self.driver.find_elements(by, value))
            except ElementStaleError:
                return None

        try:
            return bool(poll_until(_probe, timeout=timeout, poll=AndroidActions.POLL.value))
        except ElementTimeoutError:
            return False

    def wait_activity(self, activity: str, timeout: float = AndroidActions.TIMEOUT.value) -> bool:
        """等待当前 Activity 命中（子串匹配）。"""
        if callable(getattr(self.driver, "wait_activity", None)):
            try:
                return bool(self.driver.wait_activity(activity, timeout=timeout))
            except Exception:  # noqa: BLE001 - 驱动实现不可靠时退回自己的轮询
                logger.debug("驱动 wait_activity 失败，退回轮询")

        def _probe():
            return activity in (self.current_activity or "")

        try:
            return bool(poll_until(_probe, timeout=timeout, poll=AndroidActions.POLL.value))
        except (ElementTimeoutError, DeviceError):
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

        不走 el.click()：uiautomator2 的 click 会先 waitForIdle，而页面常年播放视频
        动画、永不 idle，实测会永久挂起。确实需要原生 click 时传 use_coords=False。
        """
        el = self.find(selector, timeout=timeout)
        logger.info("点击 %s", describe(selector))
        if use_coords:
            try:
                rect = el.rect
                self.tap_xy(rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2)
                return
            except (ElementStaleError, KeyError, TypeError):
                logger.debug("取 rect 失败，退回原生 click")
        el.click()

    def tap_xy(self, x: int, y: int) -> None:
        logger.debug("坐标点击 (%s, %s)", x, y)
        self._xml_cache = None  # 点完 UI 可能就变了，快照必须作废
        native = getattr(self.driver, "native_tap", None)
        if callable(native):
            native(int(x), int(y))
            return
        self.driver.shell(f"input tap {int(x)} {int(y)}")

    def tap_relative(self, rx: float, ry: float) -> None:
        x, y = self.screen.point(rx, ry)
        self.tap_xy(x, y)

    def long_press(self, selector: str, duration: float = 1.0, timeout: float = AndroidActions.TIMEOUT.value) -> None:
        el = self.find(selector, timeout=timeout)
        rect = el.rect
        cx, cy = rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2
        logger.info("长按 %s (%.1fs)", describe(selector), duration)
        native = getattr(self.driver, "native_long_press", None)
        if callable(native):
            native(cx, cy, duration)
            return
        self.driver.shell(f"input swipe {cx} {cy} {cx} {cy} {int(duration * 1000)}")

    def input_text(
        self, selector: str, text: str, clear: bool = True, timeout: float = AndroidActions.TIMEOUT.value
    ) -> None:
        el = self.find(selector, timeout=timeout)
        if clear:
            try:
                el.clear()
            except (DeviceError, RuntimeError):
                # clear 在部分输入框上无效，用退格逐字删除
                current = el.text or ""
                for _ in range(max(len(current), 1)):
                    self.driver.press_keycode(67)  # KEYCODE_DEL
        el.send_keys(text)
        logger.info("输入 %s -> %r", describe(selector), text)
        self.hide_keyboard()

    def get_text(self, selector: str, timeout: float = AndroidActions.TIMEOUT.value) -> str:
        el = self.find(selector, timeout=timeout)
        return el.text or el.get_attribute("text") or ""

    # ---------------- 手势 ---------------- #
    def swipe(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int = 400) -> None:
        logger.debug("滑动 %s -> %s (%sms)", start, end, duration_ms)
        self._xml_cache = None
        native = getattr(self.driver, "native_swipe", None)
        if callable(native):
            native(start[0], start[1], end[0], end[1], duration_ms)
            return
        self.driver.shell(f"input swipe {start[0]} {start[1]} {end[0]} {end[1]} {int(duration_ms)}")

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
        except Exception:  # noqa: BLE001 - 收起键盘失败不该影响业务
            logger.debug("收起键盘失败（忽略）")

    # ---------------- 抢票热路径 ---------------- #
    # 下面几个方法只服务于「开售瞬间要把一次点击压到几十毫秒」的场景。
    # 普通业务流用 tap 就够了，不需要碰它们。
    def tap_cached(self, selector: str, *, ttl: float = 8.0, timeout: float | None = None) -> tuple[int, int]:
        """点击元素并记住它的中心坐标，``ttl`` 秒内再次调用不再查找元素。

        为什么值得：定位一次元素要「dump 层级 → 反序列化 → 取 bounds」，
        冷路径实测 3~4 次往返；热路径只剩一次 ``input tap``。开售时连续点
        「立即购买」正是这个形状 —— 同一颗按钮被反复点。

        Returns:
            命中元素的中心坐标 (x, y)。

        Raises:
            ElementTimeoutError: 首次定位失败（缓存过期后重定位失败也走这里）。
        """
        cached = self._coord_cache.get(selector)
        if cached and (time.monotonic() - cached[2]) < ttl:
            logger.debug("缓存点击 %s @ (%d, %d)", selector, cached[0], cached[1])
            self.tap_xy(cached[0], cached[1])
            return cached[0], cached[1]

        el = self.find(selector, timeout=timeout if timeout is not None else AndroidActions.TIMEOUT.value)
        rect = el.rect
        x = rect["x"] + rect["width"] // 2
        y = rect["y"] + rect["height"] // 2
        self._coord_cache[selector] = (x, y, time.monotonic())
        logger.info("定位并点击 %s @ (%d, %d)", describe(selector), x, y)
        self.tap_xy(x, y)
        return x, y

    def forget_coords(self, selector: str | None = None) -> None:
        """丢弃坐标缓存（页面变了 / 元素位置会漂时调用）。"""
        if selector is None:
            self._coord_cache.clear()
        else:
            self._coord_cache.pop(selector, None)

    def tap_burst(self, x: int, y: int, *, times: int = 3, interval_ms: int = 35) -> int:
        """在同一坐标连点若干次，尽量把 N 次点击合成一次 shell 调用。

        为什么能合成：把 ``input tap A ; sleep 0.035 ; input tap A ...`` 串成
        一条命令一次发出去，省掉每点一次的 HTTP 往返。

        Returns:
            实际点击次数。
        """
        if times <= 0:
            return 0
        x, y = int(x), int(y)
        shell = getattr(self.driver, "shell", None)
        if callable(shell):
            parts: list[str] = []
            for i in range(times):
                parts.append(f"input tap {x} {y}")
                if i < times - 1:
                    parts.append(f"sleep {interval_ms / 1000:.3f}")
            shell(" ; ".join(parts))
            logger.info("连点 %d 次 @ (%d, %d)", times, x, y)
            return times
        for _ in range(times):
            self.tap_xy(x, y)
            time.sleep(interval_ms / 1000)
        return times

    def ui_xml(self, *, ttl: float = 0.0) -> str:
        """取当前 UI 层级 XML；``ttl > 0`` 时复用上次快照。

        大麦详情页带视频/动画，``dump_hierarchy`` 实测 2s 以上。一轮流程里
        常常要查好几个元素，复用同一份 XML 比反复 dump 划算得多。
        任何点击之后都应调 :meth:`invalidate_ui`，否则会读到过期快照。
        """
        if ttl > 0 and self._xml_cache is not None:
            xml, stamp = self._xml_cache
            if (time.monotonic() - stamp) < ttl:
                return xml
        started = time.monotonic()
        xml = self.page_source()
        cost_ms = int((time.monotonic() - started) * 1000)
        if cost_ms > 800:
            logger.warning("UI 层级导出偏慢 %dms（动画/视频页常见），已复用快照降低后续开销", cost_ms)
        self._xml_cache = (xml, time.monotonic())
        return xml

    def invalidate_ui(self) -> None:
        """UI 快照失效（点击、跳转后调用）。"""
        self._xml_cache = None

    def burst_ready(self) -> bool:
        """当前后端是否支持把多次点击合成一次 shell（决定能否开极速连点）。"""
        return callable(getattr(self.driver, "shell", None))

    # ---------------- 按键 ---------------- #
    KEYCODES = {"back": 4, "home": 3, "recent": 187, "enter": 66, "menu": 82, "power": 26}

    def press(self, key: str) -> None:
        if key not in self.KEYCODES:
            raise ValueError(f"不支持的按键 {key!r}，可用: {list(self.KEYCODES)}")
        logger.info("按键 %s(%d)", key, self.KEYCODES[key])
        self._xml_cache = None
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
        except Exception as exc:  # noqa: BLE001 - 驱动截图失败要能降级
            logger.warning("驱动截图失败，改用 adb screencap 兜底: %s", exc)
        self._adb_screenshot(path)
        logger.info("截图已保存（adb 兜底）%s", path)
        return path

    def _adb_screenshot(self, path: Path, timeout: int = 30) -> None:
        """用 ``adb exec-out screencap`` 兜底截图（二进制直出）。

        为什么需要兜底：视频/自绘页面偶尔会把 UiAutomator2 服务拖崩，
        ``screenshot`` 接口直接不可用。而现场截图恰恰是这类页面**唯一能留下的
        证据**，不能把它压在已经挂掉的驱动上。
        """
        import subprocess

        from core.android.adb import find_adb

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


def _safe_name(name: str) -> str:
    keep = "-_"
    return "".join(c if c.isalnum() or c in keep else "_" for c in name)[:60] or "file"
