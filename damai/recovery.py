"""恢复：把 App 拉回可继续操作的位置，以及失败现场取证。

这是移植时**唯一改动了逻辑**的模块，原因是一个真实 bug：

源项目 ``RecoveryHelper.recover_to_detail_page`` 里写的是
``state.get("page", "unknown")``，但 ``PageProbe.probe()`` 返回的字典键是
``"state"``。于是 ``page`` 永远是默认值 ``"unknown"``——

1. 第一层「已经在目标页就立刻返回」永远不会命中；
2. 后面每一层判断也全部落空；
3. 结果每次都白按 8 次返回键，从详情页一路退到首页，再走正向导航重来。

表面看只是「恢复慢」，实际在抢票重试链路里等于每轮多花十几秒。移植时统一
改成 ``.get("state")``，并且**只认一次 dump**，不在循环里反复探测。

分层策略（保持源项目设计）：

1. 已经在目标页 → 直接返回
2. 深退：最多 8 次返回键，每次探测，撞到首页就停
3. 正向导航：交给 ``EventNavigator`` 重新搜过去
4. 全部失败 → 返回最后一次探测结果，绝不抛异常、绝不无限循环
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.config.config import FAILURE_DIR
from core.logger import get_logger, log_event
from damai.env_snapshot import detect_app_version

logger = get_logger("damai.recovery")

TARGET_STATES = frozenset({"detail_page", "sku_page"})
NAVIGABLE_STATES = frozenset({"homepage", "search_page", "detail_page", "sku_page"})
MAX_BACK_STEPS = 8
BACK_DELAY = 0.15

_TS_TZ = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------- #
# 导航起点恢复
# ---------------------------------------------------------------------- #
def recover_to_navigation_start(
    host: Any, page_probe: dict[str, Any], max_back_steps: int = 3
) -> dict[str, Any]:
    """退到可以开始导航的页面（首页/搜索页/详情页）。

    停在购物车、订单列表这类深层页面时，连搜索入口都找不到，必须先退出来。
    退不出来就重启 App —— 这是唯一可靠的兜底。
    """
    current = page_probe
    if current["state"] in NAVIGABLE_STATES:
        return current

    for _ in range(max_back_steps):
        if not host._press_keycode_safe(4, context="恢复导航起点"):
            break
        time.sleep(0.4)
        current = host.probe_current_page()
        if current["state"] in NAVIGABLE_STATES:
            return current

    try:
        host.d.app_start(host.config.app_package, stop=False)
        time.sleep(1)
    except Exception as exc:  # noqa: BLE001
        logger.debug("重启 App 失败: %s", exc)

    return host.probe_current_page()


def exit_non_target_event_context(
    host: Any, page_probe: dict[str, Any], max_back_steps: int = 4, back_delay: float = 0.5
) -> dict[str, Any]:
    """从一个「不是目标」的详情页退出来。

    停在别人的演出详情页时，直接返回会回到搜索结果列表（可接受），
    但如果一路退到首页就得重新搜。所以这里退到「非详情页」就停。
    """
    current = page_probe
    for _ in range(max_back_steps):
        if current["state"] not in TARGET_STATES:
            return current
        if not host._press_keycode_safe(4, context="退出非目标演出页"):
            break
        time.sleep(back_delay)
        host.dismiss_startup_popups()
        current = host.probe_current_page()
    return current


def recover_to_detail_page_for_local_retry(
    host: Any,
    navigator: Any,
    page_probe: dict[str, Any] | None = None,
    max_back_steps: int = 8,
    back_delay: float = 0.15,
) -> dict[str, Any]:
    """本地快速回到当前演出的详情页，不重建会话、不重新搜索。"""
    retryable = TARGET_STATES
    current = page_probe or host.probe_current_page(fast=True)
    if current["state"] in retryable and navigator.current_page_matches_target(current):
        return current

    host.dismiss_startup_popups()
    current = host.probe_current_page()
    if current["state"] in retryable and navigator.current_page_matches_target(current):
        return current

    for _ in range(max_back_steps):
        if not host._press_keycode_safe(4, context="本地快速回退"):
            break
        time.sleep(back_delay)
        # 回退途中只做轻量探测：每步省约 2s
        current = host.probe_current_page(fast=True)
        if current["state"] in retryable and navigator.current_page_matches_target(current):
            return current

    if current["state"] == "homepage":
        logger.info("已退到首页，转正向导航回详情页")
        navigator.navigate_to_target_event()
        current = host.probe_current_page()

    return current


# ---------------------------------------------------------------------- #
# 分层恢复
# ---------------------------------------------------------------------- #
class RecoveryHelper:
    """分层恢复到大麦详情/SKU 页。"""

    def __init__(self, host: Any, navigator: Any) -> None:
        self._host = host
        self._navigator = navigator

    def recover_to_detail_page(self) -> dict[str, Any]:
        """尽最大努力回到详情页，返回最后一次探测结果（永不抛异常）。"""
        # 第 1 层：已经在目标页
        page_probe = self._host.probe_current_page(fast=True)
        state = page_probe.get("state", "unknown")
        if state in TARGET_STATES:
            logger.info("恢复: 已在目标页 (%s)", state)
            return page_probe

        logger.info("恢复: 当前页 '%s'，开始逐级返回", state)

        # 第 2 层：深退
        for step in range(1, MAX_BACK_STEPS + 1):
            if not self._host._press_keycode_safe(4, context="恢复深退"):
                break
            self._host.probe.invalidate()
            page_probe = self._host.probe_current_page(fast=True)
            state = page_probe.get("state", "unknown")
            time.sleep(BACK_DELAY)

            if state in TARGET_STATES:
                logger.info("恢复: 返回 %d 次后到达目标页 (%s)", step, state)
                return page_probe
            if state == "homepage":
                logger.warning("恢复: 返回 %d 次后到达首页，转正向导航", step)
                break
        else:
            logger.warning("恢复: 返回 %d 次仍未到目标页或首页（当前 %s）", MAX_BACK_STEPS, state)

        # 第 3 层：正向导航
        logger.info("恢复: 尝试正向导航到目标演出")
        self._navigator.navigate_to_target_event()
        self._host.probe.invalidate()
        page_probe = self._host.probe_current_page(fast=True)
        state = page_probe.get("state", "unknown")

        if state in TARGET_STATES:
            logger.info("恢复: 正向导航到达目标页 (%s)", state)
            return page_probe

        # 第 4 层：放弃，返回现状
        logger.error("恢复: 所有策略均失败，停在 '%s'", state)
        return page_probe


# ---------------------------------------------------------------------- #
# 失败现场取证
# ---------------------------------------------------------------------- #
def _slugify(scene: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in scene.lower())
    return (cleaned.strip("_-") or "scene")[:48]


def _config_hash(config: Any) -> str:
    """配置的短哈希。用于判断「这两次失败是不是同一套配置」。"""
    if config is None:
        return "none"
    try:
        if hasattr(config, "to_dict"):
            payload = config.to_dict()
        elif hasattr(config, "__dict__"):
            payload = {k: repr(v) for k, v in vars(config).items() if not k.startswith("_")}
        else:
            payload = {"repr": repr(config)}
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        encoded = repr(config)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


def capture_failure_artifacts(
    host: Any,
    scene: str,
    *,
    error: BaseException | None = None,
    extra: dict[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """把失败现场的 XML / 截图 / 元数据落盘。

    输出 ``artifacts/failures/<YYYYmmdd-HHMMSS-mmm>_<scene>.{xml,png,json}``。

    全程 best-effort：调用这个函数时通常已经在 ``except`` 里，取证失败绝不能
    把原始异常盖掉。
    """
    out_root = root or FAILURE_DIR
    try:
        out_root.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        log_event(
            logger, "failure_capture_skipped", level=logging.WARNING,
            scene=scene, reason="mkdir_failed", root=str(out_root),
        )
        return {"xml": None, "png": None, "json": None}

    ts = datetime.now(tz=_TS_TZ).strftime("%Y%m%d-%H%M%S-%f")[:-3]
    base = out_root / f"{ts}_{_slugify(scene)}"
    xml_path, png_path, json_path = (
        base.with_suffix(".xml"),
        base.with_suffix(".png"),
        base.with_suffix(".json"),
    )

    written: dict[str, Any] = {"xml": None, "png": None, "json": None}
    screenshot_failed = False
    device = getattr(host, "d", None)

    try:
        if device is not None and hasattr(device, "dump_hierarchy"):
            xml_str = device.dump_hierarchy()
            if isinstance(xml_str, bytes):
                xml_path.write_bytes(xml_str)
            else:
                xml_path.write_text(xml_str or "", encoding="utf-8")
            written["xml"] = str(xml_path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("取证时 dump_hierarchy 失败: %s", exc)

    try:
        if device is not None and hasattr(device, "screenshot"):
            shot = device.screenshot()
            if shot is None:
                screenshot_failed = True
            elif hasattr(shot, "save"):
                shot.save(str(png_path))
                written["png"] = str(png_path)
            elif isinstance(shot, (bytes, bytearray)):
                png_path.write_bytes(bytes(shot))
                written["png"] = str(png_path)
            else:
                screenshot_failed = True
        else:
            screenshot_failed = True
    except Exception as exc:  # noqa: BLE001
        screenshot_failed = True
        logger.debug("取证时截图失败: %s", exc)

    config = getattr(host, "config", None)
    serial = getattr(config, "serial", None)
    # 版本探测走 adb，可能因为没有 adb / 设备掉线而抛。取证函数绝不能因此崩溃
    # ——调用方此刻已经在 except 块里，再抛一次就把原始异常盖掉了。
    try:
        damai_version = detect_app_version(serial=serial)
    except Exception as exc:  # noqa: BLE001
        logger.debug("取证时读取大麦版本失败: %s", exc)
        damai_version = None

    metadata: dict[str, Any] = {
        "timestamp": ts,
        "scene": scene,
        "device": serial or "unknown",
        "damai_version": damai_version or "unknown",
        "config_hash": _config_hash(config),
        "screenshot_failed": screenshot_failed,
        "xml_path": written["xml"],
        "png_path": written["png"],
    }
    if error is not None:
        metadata["error_type"] = type(error).__name__
        metadata["error_message"] = str(error)
    if extra:
        metadata.update(extra)

    try:
        json_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        written["json"] = str(json_path)
    except Exception as exc:  # noqa: BLE001
        logger.debug("取证元数据写入失败: %s", exc)

    log_event(
        logger, "failure_captured", level=logging.WARNING,
        scene=scene,
        xml=written["xml"] is not None,
        png=written["png"] is not None,
        json=written["json"] is not None,
        screenshot_failed=screenshot_failed,
    )
    return written
