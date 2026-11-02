"""uiautomator2 底层交互原语：查找、点击、坐标、XML 解析。

源项目的 ``ui_primitives.py`` 同时兼容 Appium 与 u2 两套驱动，几乎每个方法
都有 ``if self._using_u2(): ... else: <Appium 分支>``。本仓库只跑 u2
（Appium 那条路在 ``core/`` 里另有实现），因此所有分支都收敛掉，
``_using_u2()`` 直接删除。

``By`` 仍然从 selenium 借来当**路由键**使用（``By.ID`` 就是字符串 ``"id"``），
不建立任何 WebDriver 会话 —— 这样移植过来的调用点 ``self._find(By.ID, "cn.damai:id/x")``
可以原样保留，不必把上千处调用改写成 u2 kwargs。

「热路径」的两个关键手段，改动时务必保留：
- ``_cached_tap``：首次查找把坐标记进缓存，之后一次手势就完事（1 次 HTTP vs 4 次）
- ``_burst_click_coordinates``：25~30ms 间隔连点，抢票靠这个抢时间窗
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from typing import Any

from selenium.webdriver.common.by import By

from core.logger import get_logger

__all__ = ["ANDROID_UIAUTOMATOR", "ElementTimeoutError", "UIPrimitives"]

logger = get_logger("damai.primitives")

#: Appium UiSelector 表达式的路由键（沿用源项目常量名，便于对照）
ANDROID_UIAUTOMATOR = "android_uiautomator"


class ElementTimeoutError(RuntimeError):
    """等元素超时。源项目借用 selenium 的 TimeoutException，这里自建避免语义混用。"""


class UIPrimitives:
    """所有子模块共用的底层原语。

    依赖宿主类提供：
    - ``self.d``       uiautomator2 device
    - ``self.config``  含 ``app_package``（裸 resource-id 补全用）
    - ``self._cached_hot_path_coords``  ``{key: (x, y)}`` 坐标缓存
    """

    # ------------------------------------------------------------------ #
    # 查找
    # ------------------------------------------------------------------ #
    @staticmethod
    def _xpath_literal(value: str) -> str:
        escaped = str(value).replace('"', '\\"')
        return f'"{escaped}"'

    def _to_u2_selector(self, by: str, value: str) -> Any:
        """把 ``(By.X, value)`` 路由成 u2 selector 对象。"""
        if by == By.ID:
            return self.d(resourceId=self._qualify_resource_id(value))
        if by == By.CLASS_NAME:
            return self.d(className=value)
        if by == By.XPATH:
            return self.d.xpath(value)
        if by == ANDROID_UIAUTOMATOR or by in {"android uiautomator"}:
            return self._parse_uiselector(value)
        raise ValueError(f"不支持的 by 类型: {by}")

    #: 兼容源项目命名
    _appium_selector_to_u2 = _to_u2_selector

    def _parse_uiselector(self, uiselector_str: str) -> Any:
        """把 ``new UiSelector().text("x")`` 翻成 u2 selector kwargs。"""
        kwargs: dict[str, Any] = {}
        for pattern, key in (
            (r'\.text\("([^"]+)"\)', "text"),
            (r'\.textContains\("([^"]+)"\)', "textContains"),
            (r'\.textMatches\("([^"]+)"\)', "textMatches"),
            (r'\.className\("([^"]+)"\)', "className"),
        ):
            match = re.search(pattern, uiselector_str)
            if match:
                kwargs[key] = match.group(1)

        match = re.search(r"\.clickable\((true|false)\)", uiselector_str)
        if match:
            kwargs["clickable"] = match.group(1) == "true"

        # UiSelector 里 instance(N) 与 index(N) 都出现过
        match = re.search(r"\.(instance|index)\((\d+)\)", uiselector_str)
        if match:
            kwargs["instance"] = int(match.group(2))

        if not kwargs:
            raise ValueError(f"无法解析 UiSelector: {uiselector_str!r}")
        return self.d(**kwargs)

    def _find(self, by: str, value: str) -> Any:
        return self._to_u2_selector(by, value)

    def _find_all(self, by: str, value: str) -> list[Any]:
        if by in (By.ID, By.CLASS_NAME):
            attr = "resource-id" if by == By.ID else "class"
            lookup = self._qualify_resource_id(value) if by == By.ID else value
            try:
                matches = self.d.xpath(f"//*[@{attr}={self._xpath_literal(lookup)}]").all()
                return list(matches)
            except Exception:
                # 回退到 instance 扫描，兼容桩对象
                key = "resourceId" if by == By.ID else "className"
                results: list[Any] = []
                for index in range(24):
                    selector = self.d(**{key: lookup, "instance": index})
                    if not self._selector_exists(selector):
                        break
                    try:
                        info = getattr(selector, "info", {}) or {}
                    except Exception:
                        info = {}
                    actual = info.get(key)
                    if actual and actual != lookup:
                        if index == 0:
                            return []
                        break
                    results.append(selector)
                return results

        selector = self._to_u2_selector(by, value)
        if hasattr(selector, "all"):
            return list(selector.all())
        try:
            return list(selector)
        except TypeError:
            return [selector] if self._selector_exists(selector) else []

    @staticmethod
    def _selector_exists(selector: Any) -> bool:
        exists = getattr(selector, "exists", None)
        if callable(exists):
            try:
                return bool(exists(timeout=0))
            except TypeError:
                return bool(exists())
            except Exception:
                return False
        if isinstance(exists, bool):
            return exists
        if hasattr(selector, "wait"):
            try:
                return bool(selector.wait(timeout=0))
            except Exception:
                return False
        return False

    def _has_element(self, by: str, value: str) -> bool:
        """瞬时存在性判断，不等待。"""
        try:
            return self._selector_exists(self._find(by, value))
        except Exception:
            return False

    def _has_any_element(self, selectors: list[tuple[str, str]]) -> bool:
        return any(self._has_element(by, value) for by, value in selectors)

    def _wait_for_element(self, by: str, value: str, timeout: float = 1.5) -> Any:
        selector = self._find(by, value)
        if hasattr(selector, "wait") and selector.wait(timeout=timeout):
            getter = getattr(selector, "get", None)
            if callable(getter):
                try:
                    return getter()
                except Exception:
                    pass
            return selector
        raise ElementTimeoutError(f"等待元素超时: by={by}, value={value}, timeout={timeout}s")

    # ------------------------------------------------------------------ #
    # 点击
    # ------------------------------------------------------------------ #
    def _click_coordinates(self, x: int, y: int, duration: int = 50) -> None:
        """按绝对坐标点击。``duration<=50`` 用轻点，否则长按。"""
        x, y = int(x), int(y)
        if duration <= 50:
            self.d.click(x, y)
        else:
            self.d.long_click(x, y, max(duration / 1000, 0.05))

    def _click_element_center(self, element: Any, duration: int = 50) -> None:
        rect = self._element_rect(element)
        if rect["width"] > 0 and rect["height"] > 0:
            self._click_coordinates(
                rect["x"] + rect["width"] // 2, rect["y"] + rect["height"] // 2, duration=duration
            )
            return
        click = getattr(element, "click", None)
        if callable(click):
            click()
            return
        raise RuntimeError("无法定位元素中心点用于点击")

    def _burst_click_coordinates(
        self, x: int, y: int, count: int = 2, interval_ms: int = 35, duration: int = 30
    ) -> None:
        """坐标连点。大麦的自绘按钮经常吞掉首次点击，连点是必要手段。"""
        for attempt in range(count):
            self._click_coordinates(x, y, duration=duration)
            if attempt < count - 1 and interval_ms > 0:
                time.sleep(interval_ms / 1000)

    def _burst_click_element_center(
        self, element: Any, count: int = 2, interval_ms: int = 35, duration: int = 30
    ) -> None:
        for attempt in range(count):
            self._click_element_center(element, duration=duration)
            if attempt < count - 1 and interval_ms > 0:
                time.sleep(interval_ms / 1000)

    def ultra_fast_click(self, by: str, value: str, timeout: float = 1.5) -> bool:
        """等元素出现并点其中心；超时返回 False 而不抛。"""
        try:
            element = self._wait_for_element(by, value, timeout=timeout)
            self._click_element_center(element, duration=50)
            return True
        except ElementTimeoutError:
            return False

    def _cached_tap(self, cache_key: str, by: str, value: str, timeout: float = 0.5) -> bool:
        """带坐标缓存的一次性点击。

        冷路径（缓存未命中）：selector.wait + selector.info + click = 3 次 HTTP 往返。
        热路径（缓存命中）：一次坐标点击 = 1 次往返，跳过所有元素查找。
        重试时省下的这几百毫秒，在开售瞬间就是能不能抢到的差别。
        """
        cached = self._cached_hot_path_coords.get(cache_key)
        if cached:
            self._click_coordinates(*cached)
            return True
        try:
            selector = self._to_u2_selector(by, value)
            if not selector.wait(timeout=timeout):
                return False
            info = selector.info
            bounds = info.get("bounds") if isinstance(info, dict) else {}
            if isinstance(bounds, dict) and "left" in bounds:
                x = (int(bounds["left"]) + int(bounds["right"])) // 2
                y = (int(bounds["top"]) + int(bounds["bottom"])) // 2
                self._cached_hot_path_coords[cache_key] = (x, y)
                self._click_coordinates(x, y)
                return True
            self._click_element_center(selector.get(), duration=50)
            return True
        except Exception:
            return False

    def batch_click(self, elements_info: list[tuple[str, str]], delay: float = 0.1) -> None:
        for by, value in elements_info:
            if self.ultra_fast_click(by, value):
                if delay > 0:
                    time.sleep(delay)
            else:
                logger.warning("点击失败: %s", value)

    def ultra_batch_click(self, elements_info: list[tuple[str, str]], timeout: float = 2.0) -> int:
        """批量收集坐标后连点 —— 勾选多个观演人时用。"""
        coordinates: list[tuple[int, int, str]] = []
        for by, value in elements_info:
            try:
                element = self._wait_for_element(by, value, timeout=timeout)
                rect = self._element_rect(element)
                coordinates.append(
                    (
                        rect["x"] + rect["width"] // 2,
                        rect["y"] + rect["height"] // 2,
                        value,
                    )
                )
            except ElementTimeoutError:
                logger.warning("超时未找到: %s", value)
            except Exception as exc:
                logger.error("查找失败 %s: %s", value, exc)

        logger.info("成功定位 %d 个目标点", len(coordinates))
        for index, (x, y, value) in enumerate(coordinates):
            self._click_coordinates(x, y, duration=30)
            if index < len(coordinates) - 1:
                time.sleep(0.01)
            logger.debug("已点击: %s", value)
        return len(coordinates)

    def smart_wait_and_click(
        self, by: str, value: str, backup_selectors: list[tuple[str, str]] | None = None, timeout: float = 1.5
    ) -> bool:
        """依次尝试主选择器与备用选择器，任一命中即点击。"""
        selectors = [(by, value), *(backup_selectors or [])]
        for selector_by, selector_value in selectors:
            try:
                element = self._wait_for_element(selector_by, selector_value, timeout=timeout)
                self._click_element_center(element, duration=50)
                return True
            except ElementTimeoutError:
                continue
        return False

    def smart_wait_for_element(
        self, by: str, value: str, backup_selectors: list[tuple[str, str]] | None = None, timeout: float = 1.5
    ) -> bool:
        selectors = [(by, value), *(backup_selectors or [])]
        for selector_by, selector_value in selectors:
            try:
                self._wait_for_element(selector_by, selector_value, timeout=timeout)
                return True
            except ElementTimeoutError:
                continue
        return False

    def _press_keycode_safe(self, keycode: int, context: str = "") -> bool:
        """按 Android keycode，失败只告警不抛。"""
        try:
            u2_key = {4: "back", 66: "enter", 3: "home"}.get(keycode, keycode)
            self.d.press(u2_key)
            return True
        except Exception as exc:
            suffix = f"（{context}）" if context else ""
            logger.warning("按键失败%s: keycode=%s, err=%s", suffix, keycode, exc)
            return False

    # ------------------------------------------------------------------ #
    # 元素属性
    # ------------------------------------------------------------------ #
    def _element_rect(self, element: Any) -> dict[str, int]:
        """统一成 ``{'x','y','width','height'}``，兼容多种 u2 元素形态。"""
        rect = getattr(element, "rect", None)
        if isinstance(rect, dict):
            return rect
        if isinstance(rect, (list, tuple)) and len(rect) == 4:
            return self._rect_from_bounds(*rect)

        bounds_tuple = getattr(element, "bounds", None)
        if isinstance(bounds_tuple, (list, tuple)) and len(bounds_tuple) == 4:
            return self._rect_from_bounds(*bounds_tuple)

        info = getattr(element, "info", None) or {}
        bounds = info.get("bounds") or {}
        left = int(bounds.get("left", 0))
        top = int(bounds.get("top", 0))
        return {
            "x": left,
            "y": top,
            "width": max(0, int(bounds.get("right", left)) - left),
            "height": max(0, int(bounds.get("bottom", top)) - top),
        }

    @staticmethod
    def _rect_from_bounds(left: int, top: int, right: int, bottom: int) -> dict[str, int]:
        left, top, right, bottom = int(left), int(top), int(right), int(bottom)
        return {
            "x": left,
            "y": top,
            "width": max(0, right - left),
            "height": max(0, bottom - top),
        }

    @staticmethod
    def _is_clickable(element: Any) -> bool:
        try:
            return bool((getattr(element, "info", None) or {}).get("clickable", False))
        except Exception:
            return False

    @staticmethod
    def _is_checked(element: Any) -> bool:
        try:
            return bool((getattr(element, "info", None) or {}).get("checked", False))
        except Exception:
            return False

    def _container_find_elements(self, container: Any, by: str, value: str) -> list[Any]:
        """容器内查找。容器为 device 本身时退化为全局查找。"""
        if container is self.d:
            return self._find_all(by, value)

        if by not in (By.ID, By.CLASS_NAME):
            if by == By.XPATH and value == ".//*":
                return self._collect_descendant_texts(container, return_text=False)
            return []

        attr = "resource-id" if by == By.ID else "class"
        lookup = self._qualify_resource_id(value) if by == By.ID else value
        container_elem = getattr(container, "elem", None)
        if container_elem is not None and hasattr(container_elem, "iter"):
            try:
                return [node for node in container_elem.iter() if node.get(attr) == lookup]
            except Exception:
                logger.debug("容器内 XML 检索失败，回退到 child 扫描")

        key = "resourceId" if by == By.ID else "className"
        results: list[Any] = []
        for index in range(24):
            child = container.child(**{key: lookup, "instance": index})
            if not self._selector_exists(child):
                break
            try:
                info = getattr(child, "info", {}) or {}
            except Exception:
                info = {}
            actual = info.get(key)
            if actual and actual != lookup:
                if index == 0:
                    return []
                break
            results.append(child)
        return results

    def _read_element_text(self, element: Any) -> str:
        for getter in (
            lambda: getattr(element, "text", ""),
            lambda: element.get_text() if hasattr(element, "get_text") else "",
            lambda: (getattr(element, "info", None) or {}).get("text", ""),
        ):
            try:
                value = getter()
            except Exception:
                continue
            if isinstance(value, str) and value.strip():
                return value
        return ""

    @staticmethod
    def _normalize_element_text(value: object) -> str:
        return value.strip() if isinstance(value, str) else ""

    def _safe_element_text(self, container: Any, by: str, value: str) -> str:
        try:
            elements = self._container_find_elements(container, by, value)
        except Exception:
            return ""
        for element in elements:
            text = self._normalize_element_text(self._read_element_text(element))
            if text:
                return text
        return ""

    def _safe_element_texts(self, container: Any, by: str, value: str) -> list[str]:
        try:
            elements = self._container_find_elements(container, by, value)
        except Exception:
            return []
        texts: list[str] = []
        seen: set[str] = set()
        for element in elements:
            text = self._normalize_element_text(self._read_element_text(element))
            if not text or text in seen:
                continue
            texts.append(text)
            seen.add(text)
        return texts

    def _collect_descendant_texts(
        self, container: Any, return_text: bool = True, xml_root: ET.Element | None = None
    ) -> list[Any]:
        """收集容器 bounds 内的所有后代文本。

        ``xml_root`` 可传入已解析的层级树，避免重复 ``dump_hierarchy``
        （单次 dump 可能几百毫秒到数秒，绝不能在同一轮里做两次）。
        """
        try:
            container_bounds = (getattr(container, "info", None) or {}).get("bounds")
            if not container_bounds:
                return []
            outer = (
                int(container_bounds.get("left", 0)),
                int(container_bounds.get("top", 0)),
                int(container_bounds.get("right", 0)),
                int(container_bounds.get("bottom", 0)),
            )
            root = xml_root if xml_root is not None else ET.fromstring(self.d.dump_hierarchy())
        except Exception:
            return []

        nodes = []
        for node in root.iter("node"):
            parsed = self._parse_bounds(node.get("bounds", ""))
            if parsed is None or not self._bounds_inside(parsed, outer):
                continue
            nodes.append(node)

        if not return_text:
            return nodes

        texts: list[str] = []
        seen: set[str] = set()
        for node in nodes:
            text = self._normalize_element_text(node.get("text", ""))
            if not text or text in seen:
                continue
            texts.append(text)
            seen.add(text)
        return texts

    # ------------------------------------------------------------------ #
    # XML / 坐标工具
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_bounds(bounds_text: str) -> tuple[int, int, int, int] | None:
        match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_text or "")
        if not match:
            return None
        left, top, right, bottom = map(int, match.groups())
        return left, top, right, bottom

    @staticmethod
    def _bounds_inside(inner: tuple[int, int, int, int], outer: tuple[int, int, int, int]) -> bool:
        return (
            inner[0] >= outer[0]
            and inner[1] >= outer[1]
            and inner[2] <= outer[2]
            and inner[3] <= outer[3]
        )

    def _qualify_resource_id(self, value: str) -> str:
        """裸 ID 补全包名：``img_jia`` → ``cn.damai:id/img_jia``。"""
        if not value or ":id/" in value:
            return value
        package = getattr(self.config, "app_package", None) or "cn.damai"
        return f"{package}:id/{value}"

    def _dump_hierarchy_xml(self) -> ET.Element | None:
        """解析当前层级树；失败返回 ``None``。"""
        from damai.device import safe_dump_xml

        xml = safe_dump_xml(self.d)
        if not xml:
            return None
        try:
            return ET.fromstring(xml)
        except ET.ParseError as exc:
            logger.warning("层级 XML 解析失败：%s", exc)
            return None

    def _get_current_activity(self) -> str:
        try:
            return (self.d.app_current() or {}).get("activity", "") or ""
        except Exception:
            return ""

    def _extract_coords_from_xml_node(self, node: ET.Element) -> tuple[int, int] | None:
        bounds = self._parse_bounds(node.get("bounds", ""))
        if not bounds:
            return None
        left, top, right, bottom = bounds
        return ((left + right) // 2, (top + bottom) // 2)

    @staticmethod
    def _xml_find_text_by_resource_id(xml_root: ET.Element | None, resource_id: str) -> str:
        if xml_root is None:
            return ""
        for node in xml_root.iter("node"):
            if node.get("resource-id") == resource_id:
                text = (node.get("text") or "").strip()
                if text:
                    return text
        return ""
