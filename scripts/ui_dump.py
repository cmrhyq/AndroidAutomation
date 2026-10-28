"""UI 层级导出 + 可交互元素清单，写用例前先跑它来选选择器。

用法:
    python scripts/ui_dump.py                 # 导出当前界面
    python scripts/ui_dump.py --grep 设置      # 只看含关键字的元素
    python scripts/ui_dump.py --clickable     # 只看可点击元素
"""

from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET

from _bootstrap import bootstrap, hr


def _attr(node: ET.Element, name: str) -> str:
    return node.attrib.get(name, "") or ""


def collect_elements(xml_text: str, grep: str = "", clickable_only: bool = False) -> list[dict]:
    root = ET.fromstring(xml_text)
    rows: list[dict] = []
    for node in root.iter():
        if node is root:
            continue
        text = _attr(node, "text").strip()
        desc = _attr(node, "content-desc").strip()
        res_id = _attr(node, "resource-id")
        cls = _attr(node, "class")
        bounds = _attr(node, "bounds")
        clickable = _attr(node, "clickable") == "true"
        enabled = _attr(node, "enabled") == "true"
        if not (text or desc or res_id):
            continue
        if clickable_only and not clickable:
            continue
        if grep and grep.lower() not in f"{text} {desc} {res_id} {cls}".lower():
            continue
        rows.append(
            {
                "text": text,
                "desc": desc,
                "id": res_id,
                "class": cls.rsplit(".", 1)[-1],
                "bounds": bounds,
                "clickable": "Y" if clickable else "",
                "enabled": "" if enabled else "disabled",
            }
        )
    return rows


def suggest_selector(row: dict) -> str:
    """给出可直接粘进代码的选择器建议。"""
    if row["id"]:
        return f"id={row['id']}"
    if row["text"]:
        return f"text={row['text']}"
    if row["desc"]:
        return f"desc={row['desc']}"
    return f"cls={row['class']}"


def main() -> int:
    parser = argparse.ArgumentParser(description="导出当前界面 UI 层级与元素清单")
    parser.add_argument("--grep", default="", help="按关键字过滤（匹配 text/desc/id/class）")
    parser.add_argument("--clickable", action="store_true", help="只列出可点击元素")
    parser.add_argument("--limit", type=int, default=80, help="最多显示条数")
    parser.add_argument("--launch", default="", help="先启动该包名再导出")
    args = parser.parse_args()

    bootstrap()

    from core.android.actions import AndroidDevice
    from core.config.config import DeviceConfig
    from core.appiums.driver_factory import session

    cfg = DeviceConfig.load()
    if args.launch:
        cfg.app_package = args.launch

    with session(cfg) as driver:
        dev = AndroidDevice(driver, cfg)
        if args.launch:
            dev.launch_app(args.launch)

        xml_path = dev.dump_page_source("ui")
        shot = dev.screenshot("ui")
        rows = collect_elements(driver.page_source, args.grep, args.clickable)

        print(hr("="))
        print(f" 当前 Activity: {dev.current_activity}")
        print(f" 元素数: {len(rows)}（过滤条件: grep={args.grep!r} clickable={args.clickable}）")
        print(f" 层级文件: {xml_path}")
        print(f" 截图: {shot}")
        print(hr("-"))

        if not rows:
            print(" 没有匹配元素。去掉 --grep / --clickable 再看全量。")
            print(hr("="))
            return 0

        text_w = min(24, max((len(r["text"]) for r in rows), default=4) + 1)
        id_w = min(34, max((len(r["id"]) for r in rows), default=4) + 1)
        header = f"{'#'.ljust(4)}{'text'.ljust(text_w)}{'resource-id'.ljust(id_w)}{'class'.ljust(22)}{'click'.ljust(6)}selector"
        print(header)
        print(hr("-"))
        for i, row in enumerate(rows[: args.limit], 1):
            text = (row["text"] or row["desc"])[: text_w - 1]
            rid = row["id"].split("/")[-1][: id_w - 1]
            print(
                f"{str(i).ljust(4)}{text.ljust(text_w)}{rid.ljust(id_w)}"
                f"{row['class'][:21].ljust(22)}{row['clickable'].ljust(6)}{suggest_selector(row)}"
            )
        if len(rows) > args.limit:
            print(f"... 其余 {len(rows) - args.limit} 条请加 --limit 或 --grep 查看")
        print(hr("="))
        print(" 提示: 表格最后一列的选择器可直接用于 dev.tap()/dev.input_text() 等")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
