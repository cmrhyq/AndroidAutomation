"""运行结局、退出码契约与机器可读摘要。

退出码是自动化（定时任务/CI）唯一可靠的判据，因此固定成常量并单测锁定。
特别强调 :data:`EXIT_TERMINAL_FAILURE`：它代表「已提交但验不到结果」等
不可重试的终态，**编排器绝不可自动重启**，否则可能重复下单。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.config.config import RUN_DIR

__all__ = [
    "DEFAULT_RESULT_JSON",
    "EXIT_CONFIG_OR_DEVICE_ERROR",
    "EXIT_INTERRUPTED",
    "EXIT_LOCK_CONFLICT",
    "EXIT_RETRIES_EXHAUSTED",
    "EXIT_SUCCESS",
    "EXIT_TERMINAL_FAILURE",
    "TERMINAL_REASONS",
    "GrabResult",
    "write_run_summary",
]

logger = logging.getLogger("androidauto.damai.run_report")

EXIT_SUCCESS = 0
EXIT_RETRIES_EXHAUSTED = 10
EXIT_TERMINAL_FAILURE = 11
EXIT_CONFIG_OR_DEVICE_ERROR = 12
EXIT_LOCK_CONFLICT = 13
EXIT_INTERRUPTED = 130

DEFAULT_RESULT_JSON = RUN_DIR / "damai_last_run.json"

#: 出现即停止重试的终态原因。``submit_unverified`` 尤其关键：
#: 提交后没能确认结果时宁可停下让人工检查，也不能重试造成重复下单。
TERMINAL_REASONS: frozenset[str] = frozenset(
    {
        "submit_unverified",
        "sold_out",
        "reservation_only",
        "session_not_found",
        "attendee_unselected",
        "session_invalid",
        "captcha",
    }
)

_TZ_SHANGHAI = timezone(timedelta(hours=8))


@dataclass(slots=True)
class GrabResult:
    """一轮 ``run_with_retry`` 的完整结局。"""

    success: bool = False
    outcome: str = "unknown"
    exit_code: int = EXIT_RETRIES_EXHAUSTED
    attempts: int = 0
    duration_ms: int = 0
    terminal_reason: str | None = None
    mode: str = "probe"
    artifacts: dict[str, str] = field(default_factory=dict)
    summary_path: Path | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "success": self.success,
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "attempts": self.attempts,
            "duration_ms": self.duration_ms,
            "terminal_reason": self.terminal_reason,
            "mode": self.mode,
            "artifacts": dict(self.artifacts),
            "summary_path": str(self.summary_path) if self.summary_path else None,
            "finished_at": datetime.now(tz=_TZ_SHANGHAI).isoformat(timespec="seconds"),
        }


def write_run_summary(
    path: str | Path,
    payload: Mapping[str, object],
    *,
    with_result: GrabResult | None = None,
) -> bool:
    """原子写入运行摘要，返回是否成功。

    先写同目录 ``.part`` 再 ``os.replace``，避免进程被中断时留下半个 JSON。
    写失败只告警、不影响退出码 —— 摘要是辅助信息，不该让抢票结果失真。
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
