"""淘宝购物车页面对象。

⚠️ 重要技术限制：购物车整页是 Weex 渲染
--------------------------------------
真机实测（PFGM00 / 淘宝 10.x）：购物车页无论空车还是有一车商品，
UI 层级里都**只有 22 个原生节点** —— 除了底部导航栏和框架容器，
一个商品都没有::

    icart_weex_root_view    FrameLayout   ← 整个购物车内容都在这里面
    tabhost / tabs / 底部 5 个 tab

也就是说，``text~=合计``、``text~=全选``、商品标题、金额，**全部读不到**。
这不是选择器没写对，是 Weex 不向 uiautomator 暴露节点。

因此本页面对象采取的策略是：

1. **页面到达校验**用原生 ``icart_weex_root_view``（可靠）。
2. **内容校验**不假装能做，提供两种真实可行手段：
   - :meth:`capture` 截图存档，报告里直接看图，人工/OCR 复核；
   - :meth:`item_count_by_ocr` 若本机装了 pytesseract 就自动 OCR 数字，
     没装则返回 ``-1`` 表示「不可用」，绝不返回假的 0。
3. **加购是否成功**不要依赖本页判断 —— 请用
   :meth:`pages.detail_page.DetailPage.add_to_cart` 的角标判据，那是原生可读的。
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from pages.base_page import BasePage


class CartPage(BasePage):
    """购物车页（Weex 渲染，内容层不可用原生选择器读取）。

    本页面对象只做「读」和「到达校验」，不提供删除/清空能力 —— 删除购物车
    数据是破坏性操作，不应由自动化用例擅自执行。
    """

    name = "cart"

    #: 购物车页最可靠的原生锚点：Weex 根容器
    WEEX_ROOT = "id=icart_weex_root_view"

    ready_selectors: tuple[str, ...] = (WEEX_ROOT,)

    #: 底部 tab 高亮态（辅助判断，不作主判据）
    TAB_SELECTORS: tuple[str, ...] = ("desc=购物车",)

    #: OCR 识别「合计 ¥xxx」
    _TOTAL_RE = re.compile(r"合计\s*[¥￥]\s*([\d,]+(?:\.\d+)?)")
    #: OCR 识别空车文案
    _EMPTY_HINTS = ("竟然是空的", "购物车还是空", "空空如也", "快去逛逛")

    def is_here(self) -> bool:
        """是否真的在购物车页（Weex 容器存在即算）。"""
        return bool(self.probe(self.WEEX_ROOT))

    def capture(self, tag: str = "cart") -> Path:
        """截图留证 —— Weex 内容读不到时，这是最实在的证据。"""
        path = self.screenshot(tag)
        self.log.info("购物车截图已存档: %s", path)
        return path

    # ------------------------------------------------------------------ #
    # 可选：OCR 读数
    # ------------------------------------------------------------------ #
    @staticmethod
    def ocr_available() -> bool:
        """本机是否有可用的 OCR 引擎（pytesseract + tesseract 二进制）。"""
        if shutil.which("tesseract") is None:
            return False
        try:
            import pytesseract  # noqa: F401
        except ImportError:
            return False
        return True

    def _ocr_text(self) -> str:
        """对当前屏幕做 OCR，返回识别出的文本；不可用时返回空串。"""
        if not self.ocr_available():
            return ""
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            return ""
        shot = self.screenshot("cart_ocr")
        try:
            with Image.open(shot) as img:
                return pytesseract.image_to_string(img, lang="chi_sim+eng")
        except Exception as exc:  # OCR 失败不应影响用例结论
            self.log.warning("OCR 识别失败: %s", exc)
            return ""

    def item_count_by_ocr(self) -> int:
        """OCR 读「购物车 (n)」里的件数。

        Returns:
            件数；无 OCR 环境或识别不到时返回 ``-1``（表示不可测，而不是 0）。
        """
        text = self._ocr_text()
        if not text:
            return -1
        match = re.search(r"购物车\s*[（(]\s*(\d+)\s*[)）]", text)
        return int(match.group(1)) if match else -1

    def total_price_by_ocr(self) -> str:
        """OCR 读底部「合计 ¥xxx」，读不到返回空串。"""
        text = self._ocr_text()
        if not text:
            return ""
        match = self._TOTAL_RE.search(text)
        return f"¥{match.group(1)}" if match else ""

    def is_empty_by_ocr(self) -> bool | None:
        """OCR 判断是否空车；不可判定时返回 None（三态，避免误导）。"""
        text = self._ocr_text()
        if not text:
            return None
        return any(hint in text for hint in self._EMPTY_HINTS)

    # ------------------------------------------------------------------ #
    # 报告辅助
    # ------------------------------------------------------------------ #
    def describe_limitation(self) -> str:
        """把技术限制说明清楚，便于写进用例失败信息。"""
        ocr_state = "可用" if self.ocr_available() else "不可用（未安装 pytesseract/tesseract）"
        return (
            "购物车页由 Weex 渲染，原生层级不含任何商品节点，"
            f"文字校验需依赖 OCR（当前 OCR {ocr_state}）。"
            "加购结果请改用 DetailPage.add_to_cart() 的购物车角标判据。"
        )

    def scroll_items(self, times: int = 1) -> None:
        self.scroll_down(times=times)
