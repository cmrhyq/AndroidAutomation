"""失败取证：把「出事那一刻的手机状态」原样落到磁盘。

抢票失败后最需要回答的问题是「当时屏幕上到底是什么」。日志只能告诉你
走到哪一步，答不了这个。所以失败路径统一留三样东西：

===================  ====================================================
文件                 用途
===================  ====================================================
``*_shot.png``       现场截图，人眼一眼就能看出卡在哪
``*_page.xml``       UI 层级，用于离线复算选择器是否失效
``*_meta.json``      环境元数据（时间/场景/设备/App 版本/异常摘要）
===================  ====================================================

三条实现要求：

1. **取证函数本身绝不能抛异常**。它总在 ``except`` 块里被调用，一抛就把
   原始异常盖掉，现场和病因一起丢。连 adb 都可能不可用，所以每一步都兜住。
2. **截图优先级**：驱动截图失败时用 ``adb exec-out screencap`` 兜底 ——
   大麦详情页带视频，UiAutomator2 服务被拖崩是常事，恰恰那才是要留证的时刻。
3. **文件名带毫秒时间戳**，同一秒内多次失败不会互相覆盖。
"""

from __future__ import annotations

import contextlib
import json
import re
import subprocess
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.logger import get_logger

logger = get_logger(__name__)

_TZ_SHANGHAI = timezone(timedelta(hours=8))

__all__ = ["app_version", "capture_failure"]


def _target_dir(root: str | Path | None) -> Path:
    if root is not None:
        return Path(root)
    from core.config.config import FAILURE_DIR

    return FAILURE_DIR


def app_version(package: str, serial: str = "") -> str:
    """读已安装 App 的版本号，读不到返回空串。

    用于事后判断「这次失败是不是因为大麦升级改了结构」。adb 不可用时返回空串
    而不是抛异常 —— 取证路径上任何异常都是有害的。
    """
    from core.android.adb import find_adb
    from core.android.proc import run_cmd

    adb = find_adb()
    if not adb or not package:
        return ""
    args = [adb] + (["-s", serial] if serial else []) + ["shell", "dumpsys", "package", package]
    try:
        code, out = run_cmd(args, timeout=10)
    except Exception:  # noqa: BLE001 - 取证不能因为这里失败而中断
        return ""
    if code != 0 or not out:
        return ""
    match = re.search(r"versionName=(\S+)", out)
    return match.group(1) if match else ""


def _grab_xml(dev) -> str:
    try:
        return dev.page_source() or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("取证：导出 UI 层级失败 %s", exc)
        return ""


def _grab_screenshot(dev, path: Path) -> bool:
    """截图，驱动失败时用 adb 兜底。"""
    try:
        dev.driver.save_screenshot(str(path))
        if path.exists() and path.stat().st_size > 0:
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("取证：驱动截图失败，尝试 adb 兜底 %s", exc)

    from core.android.adb import find_adb

    adb = find_adb()
    serial = getattr(getattr(dev, "cfg", None), "udid", "") or ""
    if not adb or not serial:
        return False
    try:
        result = subprocess.run(  # noqa: S603 - 参数全来自本机配置
            [adb, "-s", serial, "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception:  # noqa: BLE001
        return False
    if result.returncode != 0 or not result.stdout:
        return False
    path.write_bytes(result.stdout)
    return True


def capture_failure(
    dev,
    scene: str,
    *,
    error: BaseException | None = None,
    extra: dict[str, Any] | None = None,
    root: str | Path | None = None,
) -> dict[str, str]:
    """落盘现场证据，返回 ``{kind: path}``。

    Args:
        dev: :class:`core.android.actions.AndroidDevice`。
        scene: 场景标签，进文件名（如 ``run_ticket`` / ``submit_unverified``）。
        error: 触发取证的异常，会被记进元数据。
        extra: 额外要记的字段（如 attempt、mode、reason）。
        root: 落盘根目录，默认 ``artifacts/failures``。

    Returns:
        形如 ``{"screenshot": ..., "xml": ..., "meta": ...}`` 的路径字典。
        某一步失败时对应键缺席，但函数本身不抛异常。
    """
    target = _target_dir(root)
    stamp = datetime.now(tz=_TZ_SHANGHAI).strftime("%Y%m%d_%H%M%S_%f")[:-3]
    safe_scene = re.sub(r"[^\w.-]+", "_", scene)[:40] or "scene"
    prefix = target / f"{safe_scene}_{stamp}"
    written: dict[str, str] = {}

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("取证目录不可写（%s）：%s", target, exc)
        return written

    xml_path = prefix.with_suffix(".xml")
    xml_text = _grab_xml(dev)
    if xml_text:
        try:
            xml_path.write_text(xml_text, encoding="utf-8")
            written["xml"] = str(xml_path)
        except OSError as exc:
            logger.warning("取证：XML 落盘失败 %s", exc)

    shot_path = prefix.with_suffix(".png")
    if _grab_screenshot(dev, shot_path):
        written["screenshot"] = str(shot_path)

    serial = ""
    try:
        serial = str(dev.cfg.udid or "") if getattr(dev, "cfg", None) else ""
    except Exception:  # noqa: BLE001
        serial = ""
    package = ""
    try:
        package = str(getattr(dev.cfg, "app_package", "") or "")
    except Exception:  # noqa: BLE001
        package = ""

    metadata: dict[str, Any] = {
        "scene": safe_scene,
        "timestamp": datetime.now(tz=_TZ_SHANGHAI).isoformat(timespec="milliseconds"),
        "device": serial or "unknown",
        "app_package": package or "unknown",
        "app_version": "unknown",
        "activity": "",
        "xml_path": written.get("xml"),
        "screenshot": written.get("screenshot"),
        "screenshot_failed": "screenshot" not in written,
    }
    if package:
        # 读版本要跑 adb，而 adb 恰好也可能是这次失败的原因之一。
        # 取证函数在 except 块里被调用，再抛一次就把原始异常盖掉了。
        try:
            metadata["app_version"] = app_version(package, serial) or "unknown"
        except Exception as exc:  # noqa: BLE001
            logger.debug("取证：读取 App 版本失败 %s", exc)
    with contextlib.suppress(Exception):
        metadata["activity"] = dev.current_activity
    if error is not None:
        metadata["error_type"] = type(error).__name__
        metadata["error"] = str(error)[:500]
        metadata["traceback"] = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )[-2000:]
    if extra:
        metadata["extra"] = extra

    meta_path = prefix.with_suffix(".json")
    try:
        meta_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        written["meta"] = str(meta_path)
    except OSError as exc:
        logger.warning("取证：元数据落盘失败 %s", exc)

    logger.warning("失败取证已保存: %s", {key: Path(value).name for key, value in written.items()})
    return written
