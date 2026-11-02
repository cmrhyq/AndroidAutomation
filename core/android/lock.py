"""实例互斥锁：同一时刻只允许一个抢票进程操作设备。

（与业务无关的通用能力，放在 core 下；抢票脚本只是它当前唯一的使用者。）

为什么必须要这把锁：抢票脚本很容易被「手动跑一次 + 定时任务又一次」同时启动。
两个进程各自点票档、各自提交，最轻的结果是互相把对方的点击打断（都失败），
最坏的结果是**重复下单**。而重复下单在抢票场景里是要付违约金的。

实现用 OS 级排他锁（Windows ``msvcrt.locking`` / POSIX ``flock``）而不是
「写 pid 文件再判断」：进程被 Ctrl-C 或崩溃时，OS 会自动释放锁，不会留下
需要人工清理的僵尸锁。pid 文件那种做法在崩溃后必须靠猜 pid 是否还活着。

锁文件同时写入 pid 与启动时间，仅用于报错时告诉用户「被谁占了」。
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from pathlib import Path
from types import TracebackType

from core.config.config import RUN_DIR
from core.logger import get_logger

__all__ = ["AlreadyRunningError", "InstanceLock"]

logger = get_logger(__name__)

DEFAULT_LOCK_PATH = RUN_DIR / "damai.lock"


class AlreadyRunningError(RuntimeError):
    """已有另一个抢票进程持有锁。"""


class InstanceLock:
    """跨平台单实例锁，用作上下文管理器。

    ```python
    with InstanceLock():
        ...
    ```

    获取失败抛 :class:`AlreadyRunningError`；退出时自动释放（含异常路径）。
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else DEFAULT_LOCK_PATH
        self._handle: object | None = None
        self._locked = False

    # ------------------------------------------------------------------ #
    # 上下文协议
    # ------------------------------------------------------------------ #
    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    # ------------------------------------------------------------------ #
    # 获取 / 释放
    # ------------------------------------------------------------------ #
    def acquire(self) -> None:
        """尝试加锁；失败抛 :class:`AlreadyRunningError`。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self._path, "a+b")  # noqa: SIM115 — 需要跨方法持有句柄
        if not self._try_lock(handle):
            holder = self._read_holder()
            handle.close()
            raise AlreadyRunningError(
                f"已有抢票进程在运行（{self._path} 被占用"
                + (f"，持有者 {holder}" if holder else "")
                + "）。同时运行两个进程可能导致重复下单，已中止。"
            )

        self._handle = handle
        self._locked = True
        # 写持有者信息，仅用于下一次冲突时报错。先截断再写，避免残留旧内容。
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()} started={time.strftime('%Y-%m-%d %H:%M:%S')}\n".encode())
            handle.flush()
        except OSError as exc:  # pragma: no cover - 写入失败不影响加锁本身
            logger.debug("写入锁持有者信息失败: %s", exc)

    def release(self) -> None:
        """释放锁。重复释放是安全的。"""
        handle = self._handle
        self._handle = None
        self._locked = False
        if handle is None:
            return
        try:
            self._unlock(handle)  # type: ignore[arg-type]
        except OSError as exc:  # pragma: no cover
            logger.debug("解锁失败: %s", exc)
        finally:
            with contextlib.suppress(OSError):
                handle.close()  # type: ignore[attr-defined]

    @property
    def held(self) -> bool:
        return self._locked

    # ------------------------------------------------------------------ #
    # 平台相关
    # ------------------------------------------------------------------ #
    @staticmethod
    def _try_lock(handle: object) -> bool:
        # 两个平台都要「当前进程写满 1 字节再尝试独占」：
        # 空文件在 Windows 上无法建立锁定区域。
        try:
            handle.seek(0, os.SEEK_END)  # type: ignore[attr-defined]
            if handle.tell() == 0:  # type: ignore[attr-defined]
                handle.write(b"\0")  # type: ignore[attr-defined]
                handle.flush()  # type: ignore[attr-defined]
            handle.seek(0)  # type: ignore[attr-defined]
        except OSError:
            return False

        if sys.platform == "win32":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                return True
            except OSError:
                return False

        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
            return True
        except OSError:
            return False

    @staticmethod
    def _unlock(handle: object) -> None:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)  # type: ignore[attr-defined]
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
            return

        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]

    def _read_holder(self) -> str:
        try:
            return self._path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""
