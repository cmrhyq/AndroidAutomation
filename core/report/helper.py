"""Allure 附件工具类 AllureHelper。

底层全部委托 core.report.collector，此处只做签名适配，不重复实现。"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import allure

from core import get_logger
from core.report import collector

#: 合法的严重度取值（Allure 只认这五档，写错会被前端忽略且不报错）
_SEVERITIES = frozenset({"blocker", "critical", "normal", "minor", "trivial"})


logger = get_logger(__name__)



def _dynamic(name: str, *args: Any, **kwargs: Any) -> bool:
    """调 ``allure.dynamic.xxx``，把「报告元信息设置失败」降级成告警。"""
    try:
        getattr(allure.dynamic, name)(*args, **kwargs)
        return True
    except Exception as exc:  # noqa: BLE001 - 元信息挂不上不该让用例失败
        logger.warning(f"allure.dynamic.{name} 调用失败：{exc}")
        return False


class AllureHelper:
    """Allure 报告辅助工具类（全部静态方法，直接 ``AllureHelper.xxx()`` 调用）。

    所有方法都返回 ``bool``（是否真的挂上了），方便在用例里断言「证据链完整」；
    不关心返回值时当过程调用用即可。
    """

    # ------------------------------------------------------------------ #
    # 截图
    # ------------------------------------------------------------------ #
    @staticmethod
    def attach_screenshot(screenshot: object, name: str = "截图") -> bool:
        """挂一张截图。

        screenshot 支持三种形态：AndroidDevice（内部调 dev.screenshot 落盘后再挂）、
        str/Path（已有图片文件，不存在则跳过）、bytes（PNG 字节流，直接挂不落盘）。

            AllureHelper.attach_screenshot(dev, "金币中心现场")
        """
        return collector.attach_image(screenshot, name=name)

    # ------------------------------------------------------------------ #
    # 文本类
    # ------------------------------------------------------------------ #
    @staticmethod
    def attach_text(text_content: str, name: str = "文本") -> bool:
        """挂一段纯文本（补充说明、命令回放等）。"""
        return collector.attach_text(text_content, name=name)

    @staticmethod
    def attach_log(log_content: str, name: str = "日志") -> bool:
        """挂一段日志。与 :meth:`attach_text` 等价，保留这个名字是为了贴合外部习惯。"""
        return collector.attach_text(log_content, name=name)

    @staticmethod
    def attach_html(html_content: str, name: str = "HTML") -> bool:
        """挂一段 HTML，Allure 会渲染成可切换的 HTML 视图。"""
        return collector.attach_html(html_content, name=name)

    @staticmethod
    def attach_table(rows: dict[str, str], name: str = "业务上下文") -> bool:
        """挂一张按显示宽度对齐的键值表（中文占两列，``ljust`` 会错位）。"""
        return collector.attach_table(rows, name=name)

    # ------------------------------------------------------------------ #
    # 结构化数据 / 文件
    # ------------------------------------------------------------------ #
    @staticmethod
    def attach_json(json_data: object, name: str = "JSON 数据") -> bool:
        """把 dict / list 序列化成缩进 JSON 挂进报告（``ensure_ascii=False``，中文可读）。"""
        return collector.attach_json(json_data, name=name)

    @staticmethod
    def attach_file(
        file_path: object,
        name: str | None = None,
        attachment_type: object | None = None,
    ) -> bool:
        """挂本地文件。``attachment_type`` 不传时按后缀推断（``.json`` 会高亮）。

        文件不存在属于调用写错，这里会告警并返回 ``False``（不像截图那样静默跳过）。
        """
        return collector.attach_file(file_path, name=name, attachment_type=attachment_type)

    # ------------------------------------------------------------------ #
    # 步骤
    # ------------------------------------------------------------------ #
    @staticmethod
    @contextmanager
    def step(step_name: str, screenshot_on_error: Any | None = None) -> Iterator[None]:
        """步骤上下文管理器，等价于 :func:`core.report.step`。

        Args:
            step_name: 步骤名（中文短句，报告里直接展示）。
            screenshot_on_error: 传 ``AndroidDevice`` 时，步骤抛异常会先截图再上抛。

        注意：没有挂 pytest 插件（脚本里单独跑业务流）时是**透明 no-op**，
        不会因为缺 Allure 监听器而报错。
        """
        with collector.step(step_name, screenshot_on_error=screenshot_on_error):
            yield

    # ------------------------------------------------------------------ #
    # 运行期元信息
    # ------------------------------------------------------------------ #
    @staticmethod
    def add_title(title: str) -> bool:
        """运行期改用例标题。

        优先级低于 ``@allure.title`` —— 静态装饰器在**收集阶段**就把标题写进了
        ``__allure_display_name__``，这里改只影响「本次运行」。

        ⚠️ 不要在 ``pytest_runtest_setup`` 这类 setup 钩子里调：allure-pytest 的该
        钩子是 hookwrapper，``yield`` 之后才写 ``test_result.name``，setup 里设置的
        一定会被覆盖（框架插件踩过这个坑，见 ``core/report/plugin.py`` 的注释）。
        """
        return _dynamic("title", title)

    @staticmethod
    def add_description(description: str) -> bool:
        """运行期改用例描述（优先级低于 ``@allure.description``）。"""
        return _dynamic("description", description)

    @staticmethod
    def add_severity(severity: object) -> bool:
        """运行期改严重度。接受 ``"critical"`` 或 ``allure.severity_level.CRITICAL``。

        非法取值直接告警并返回 ``False`` —— Allure 前端对写错的严重度是静默忽略的，
        不校验的话报告里会莫名其妙少一档颜色。
        """
        level = getattr(severity, "value", severity)
        text = str(level).strip().lower()
        if text not in _SEVERITIES:
            logger.warning(f"非法严重度 {severity!r}，可选：{sorted(_SEVERITIES)}")
            return False
        return _dynamic("severity", text)

    @staticmethod
    def add_tag(tag: str) -> bool:
        """运行期加一个标签。pytest marker 会被 allure-pytest 自动转成 tag，别重复加。"""
        return _dynamic("tag", tag)

    @staticmethod
    def add_link(url: str, link_type: str = "link", name: str | None = None) -> bool:
        """加一条外链，``link_type`` 取 ``link`` / ``issue`` / ``tms``。"""
        if not url:
            return False
        return _dynamic("link", url, link_type=link_type, name=name or url)


@contextmanager
def allure_step(step_name: str, screenshot_on_error: Any | None = None) -> Iterator[None]:
    """:meth:`AllureHelper.step` 的函数式写法，方便 ``from core.report import allure_step``。"""
    with AllureHelper.step(step_name, screenshot_on_error=screenshot_on_error):
        yield


__all__ = ["AllureHelper", "allure_step"]
