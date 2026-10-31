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

import json
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
#: 文件后缀 → Allure 附件类型（决定报告里是图片预览、JSON 高亮还是纯文本）
_SUFFIX_TO_TYPE = {
    ".png": allure.attachment_type.PNG,
    ".jpg": allure.attachment_type.JPG,
    ".jpeg": allure.attachment_type.JPG,
    ".gif": allure.attachment_type.GIF,
    ".bmp": allure.attachment_type.BMP,
    ".webp": allure.attachment_type.PNG,
    ".svg": allure.attachment_type.SVG,
    ".html": allure.attachment_type.HTML,
    ".htm": allure.attachment_type.HTML,
    ".json": allure.attachment_type.JSON,
    ".xml": allure.attachment_type.XML,
    ".yaml": allure.attachment_type.YAML,
    ".yml": allure.attachment_type.YAML,
    ".csv": allure.attachment_type.CSV,
    ".tsv": allure.attachment_type.TSV,
    ".mp4": allure.attachment_type.MP4,
    ".webm": allure.attachment_type.WEBM,
    ".pdf": allure.attachment_type.PDF,
    ".zip": allure.attachment_type.ZIP,
}


def _warn(message: str) -> None:
    """附件链路的问题只告警 —— 报告出问题不能掩盖真正的用例结论。"""
    try:
        from core.logger import get_logger

        get_logger(__name__).warning(message)
    except Exception:  # noqa: BLE001 - 日志模块异常时也不能让附件调用炸掉
        pass


def _attach(body: object, name: str, attachment_type: object, extension: str | None = None) -> bool:
    """所有「内存里造出来的」附件都从这里进 Allure。

    返回是否真的挂上了：报告链路的问题在 CI 里通常是静默的，留一个可断言的
    返回值，离线用例才能把「附件没了」这种回归挡住。
    """
    try:
        allure.attach(body, name=name, attachment_type=attachment_type, extension=extension)
        return True
    except Exception as exc:  # noqa: BLE001
        _warn(f"附件「{name}」挂载失败：{exc}")
        return False


def _safe_tag(text: str) -> str:
    """把步骤名等自由文本压成安全文件名片段（保留中文，只清掉路径字符）。"""
    return "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(text))[:40]


def _screenshot_path(source: object, tag: str) -> Path | None:
    """让设备对象自己截一张图，返回落盘路径（失败返回 None）。"""
    take = getattr(source, "screenshot", None)
    if not callable(take):
        return None
    try:
        return Path(str(take(_safe_tag(tag))))
    except Exception as exc:  # noqa: BLE001 - 截不到图不该影响用例
        _warn(f"截图失败（{tag}）：{exc}")
        return None


def attach_image(source: object, name: str = "截图") -> bool:
    """把截图挂进 Allure。``source`` 支持三种形态，方便不同层直接调用：

    - ``AndroidDevice``：内部走 ``dev.screenshot(tag)`` 落盘后再挂（框架已有能力，不另造轮子）
    - ``str`` / ``Path``：本地图片文件，**不存在时静默跳过**（旧语义，脚本里常拿不到路径）
    - ``bytes``：已经是 PNG 字节流，直接挂，不落盘
    """
    if source is None:
        return False

    if isinstance(source, (bytes, bytearray)):
        return _attach(bytes(source), name, allure.attachment_type.PNG, extension="png")

    path = source if isinstance(source, Path) else None
    if path is None:
        if callable(getattr(source, "screenshot", None)):
            path = _screenshot_path(source, name)
            if path is None:
                return False
        else:
            path = Path(str(source))

    if not path.is_file():
        # 截图路径存在但文件没落盘（instrumentation 崩过）在真机上很常见，
        # 这类缺失属于可容忍损耗，不告警刷屏。
        return False
    return attach_file(path, name=name, attachment_type=allure.attachment_type.PNG)


def attach_file(
    path: object,
    name: str | None = None,
    attachment_type: object | None = None,
) -> bool:
    """把本地文件挂进报告，类型按后缀推断（``.json`` → JSON 高亮，``.png`` → 图片预览）。

    与 :func:`attach_image` 的差别：这里的路径是**调用方明确指定**的，
    文件不存在说明调用写错了，因此会告警而不是静默跳过。
    """
    file_path = Path(str(path)) if path else None
    if file_path is None or not file_path.is_file():
        _warn(f"附件文件不存在：{path}")
        return False

    suffix = file_path.suffix.lower()
    atype = attachment_type or _SUFFIX_TO_TYPE.get(suffix, allure.attachment_type.TEXT)
    try:
        allure.attach.file(
            str(file_path),
            name=name or file_path.name,
            attachment_type=atype,
            extension=suffix.lstrip(".") or None,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        _warn(f"附件「{file_path.name}」挂载失败：{exc}")
        return False


def attach_json(data: object, name: str = "JSON 数据") -> bool:
    """把 dict / list 序列化成缩进 JSON 挂进报告（中文不转义，报告里直接可读）。"""
    try:
        body = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        _warn(f"JSON 序列化失败（{name}）：{exc}")
        return False
    return _attach(body, name, allure.attachment_type.JSON, extension="json")


def attach_html(html: str, name: str = "HTML") -> bool:
    """挂一段 HTML（Allure 会渲染成可切换的 HTML 视图，适合贴校验报告片段）。"""
    if not html or not html.strip():
        return False
    return _attach(html, name, allure.attachment_type.HTML, extension="html")


def attach_text(body: str, name: str = "文本") -> bool:
    """挂一段纯文本（业务上下文、命令回放、日志片段等）。"""
    if not body or not body.strip():
        return False
    return _attach(body, name, allure.attachment_type.TEXT)


def attach_table(rows: dict[str, str], name: str = "业务上下文") -> bool:
    """把键值对挂成一个对齐的文本表。

    Allure 的 text 附件用等宽字体，但中文在等宽字体下占 **两列**。按字符数
    ``ljust`` 会让「角标」和「keyword」差出一截，所以这里按显示宽度补齐。
    """
    if not rows:
        return False
    width = max(_display_width(str(k)) for k in rows)
    body = "\n".join(
        f"{str(k)}{' ' * (width - _display_width(str(k)))} : {v}" for k, v in rows.items()
    )
    return attach_text(body, name)


def _attach_screenshot(dev: Any | None, tag: str) -> None:
    """尽力截一张图挂到当前步骤上。任何异常都吞掉 —— 报告不能掩盖真正的错误。"""
    if dev is None:
        return
    if attach_image(dev, name=f"步骤失败截图 · {tag}"):
        return
    _warn(f"步骤「{tag}」失败但未能留下截图")


def _display_width(text: str) -> int:
    """字符串的终端显示宽度（全角字符算 2 列）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
