"""步骤埋点：把业务流的 ``with step(...)`` 直接映射成 Allure 的 step。

为什么还要这一层
----------------
业务代码里已经写了 ``with step("加入购物车"):``，如果直接调用 ``allure.step``
会有两个问题：

1. **脱离 pytest 就崩**：脚本单独跑业务流（``scripts/`` 下那些）时没有 Allure
   监听器，``allure.step`` 会记一堆 warning。
2. **失败截图没人拍**：Allure 只知道「这一步抛异常了」，不知道当时的屏幕长什么样。

所以这里做一个很薄的封装：插件在 session 开始时 ``enable()``，没开启时全部退化成
no-op；开启时转发给 Allure，并在步骤失败时补一张现场截图。

``note()`` 收集的键值不会立刻丢进报告，而是攒到用例结束一次性 attach 成一张
「业务上下文」表 —— 十几个零散附件会淹没真正重要的失败截图。
"""

from __future__ import annotations

import threading
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import allure

#: 由 pytest 插件在会话开始时打开；脚本里单独跑业务流时保持关闭
_enabled = False

#: 每个线程当前用例积累的业务上下文（key → value）
_local = threading.local()


def enable() -> None:
    """打开埋点（由 ``core.report.plugin`` 在 pytest 会话里调用）。"""
    global _enabled
    _enabled = True


def disable() -> None:
    global _enabled
    _enabled = False
    clear_notes()


def is_enabled() -> bool:
    return _enabled


# ---------------------------------------------------------------------- #
# step
# ---------------------------------------------------------------------- #
@contextmanager
def step(name: str, screenshot_on_error: Any | None = None) -> Iterator[None]:
    """包一个业务步骤。

    Args:
        name: 步骤名，Allure 报告里直接展示，建议用中文短句。
        screenshot_on_error: 失败时用于截图的 ``AndroidDevice``（可选）。传了就自动
            留证，没传就只依赖 Allure 自带的异常记录 —— 业务流不必强依赖设备对象。
    """
    if not _enabled:
        yield
        return

    with allure.step(name):
        try:
            yield
        except Exception:
            # 先截图再往上抛：此刻 driver 还活着，teardown 之后就没得拍了
            _attach_screenshot(screenshot_on_error, name)
            raise


# ---------------------------------------------------------------------- #
# note
# ---------------------------------------------------------------------- #
def note(**context: str) -> None:
    """记录业务上下文（搜索词、商品标题、角标变化…），用例结束时汇总成表格。"""
    if not _enabled:
        return
    bucket: dict[str, str] = getattr(_local, "notes", None) or {}
    bucket.update({k: str(v) for k, v in context.items() if v not in (None, "")})
    _local.notes = bucket


def drain_notes() -> dict[str, str]:
    """取走当前线程累积的上下文（插件在用例结束时调用）。"""
    bucket: dict[str, str] = getattr(_local, "notes", None) or {}
    _local.notes = {}
    return bucket


def clear_notes() -> None:
    _local.notes = {}


# ---------------------------------------------------------------------- #
# 附件
# ---------------------------------------------------------------------- #
def _attach_screenshot(dev: Any | None, tag: str) -> None:
    """尽力截一张图挂到当前步骤上。任何异常都吞掉 —— 报告不能掩盖真正的错误。"""
    if dev is None:
        return
    take = getattr(dev, "screenshot", None)
    if not callable(take):
        return
    try:
        safe_tag = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in tag)[:40]
        path = take(f"step_{safe_tag}")
    except Exception:  # noqa: BLE001
        return
    attach_image(path, name=f"步骤失败截图 · {tag}")


def attach_image(path: object, name: str = "截图") -> None:
    """把本地图片挂进 Allure（文件不存在时静默跳过）。"""
    if not path:
        return
    file_path = Path(str(path))
    if not file_path.is_file():
        return
    try:
        allure.attach.file(
            str(file_path),
            name=name,
            attachment_type=allure.attachment_type.PNG,
            extension="png",
        )
    except Exception:  # noqa: BLE001 - 附件失败不该影响用例结论
        pass


def attach_text(body: str, name: str) -> None:
    """挂一段纯文本（业务上下文、命令回放等）。"""
    if not body.strip():
        return
    try:
        allure.attach(body, name=name, attachment_type=allure.attachment_type.TEXT)
    except Exception:  # noqa: BLE001
        pass


def attach_table(rows: dict[str, str], name: str = "业务上下文") -> None:
    """把键值对挂成一个对齐的文本表。

    Allure 的 text 附件用等宽字体，但中文在等宽字体下占 **两列**。按字符数
    ``ljust`` 会让「角标」和「keyword」差出一截，所以这里按显示宽度补齐。
    """
    if not rows:
        return
    width = max(_display_width(str(k)) for k in rows)
    body = "\n".join(
        f"{str(k)}{' ' * (width - _display_width(str(k)))} : {v}" for k, v in rows.items()
    )
    attach_text(body, name)


def _display_width(text: str) -> int:
    """字符串的终端显示宽度（全角字符算 2 列）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
