"""运行摘要落盘：每次抢票留下一份机器可读的结局记录。

为什么单独落盘而不是只打日志：定时任务/CI 读的是文件不是日志。日志里一行
``outcome=success exit_code=0`` 用 grep 也能读，但配置指纹、耗时、取证路径
这些结构化字段拼起来就是半个别造的 JSON 了。

写文件用「先写 ``.part`` 再 ``os.replace``」：进程被 Ctrl-C 或系统杀掉时，
目标路径上永远不会留下半个 JSON —— 定时任务读到坏 JSON 会连带失败。

与 ``data.constant.damai.TERMINAL_REASONS`` 的分工：那边定义「哪些原因不可
重试」，这边只负责把结论写下来，不参与决策。
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from core.logger import get_logger
from data.entity.damai import GrabResult

logger = get_logger(__name__)

__all__ = ["DEFAULT_RESULT_JSON", "write_run_summary"]


def _default_path() -> Path:
    from core.config.config import RUN_DIR

    return RUN_DIR / "damai_last_run.json"


#: 默认摘要路径（首次访问时才解析目录常量，避免模块级依赖创建目录）
DEFAULT_RESULT_JSON = _default_path()


def write_run_summary(
    path: str | Path,
    payload: Mapping[str, object],
    *,
    with_result: GrabResult | None = None,
) -> bool:
    """原子写入运行摘要，返回是否成功。

    写失败只告警、不抛异常 —— 摘要是辅助信息，不该因为它让抢票结果失真。
    """
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True, default=str)
        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=target.name, suffix=".part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body + "\n")
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
    except OSError as exc:
        logger.warning("运行摘要写入失败（%s）：%s", target, exc)
        return False

    if with_result is not None:
        with_result.summary_path = target
    return True
