"""子进程调用工具（Windows 上 npm 全局命令是 .cmd，需经 cmd /c 转手）。"""

from __future__ import annotations

import os
import shutil
import subprocess


def resolve_executable(cmd: str) -> list[str]:
    """把命令名解析成可直接交给 subprocess 的前缀。

    Windows 上 npm 全局装出来的 appiums 是 appiums.CMD，shell=False 直接执行会
    FileNotFoundError，必须先经 cmd /c 转一手。
    """
    path = shutil.which(cmd)
    if path is None:
        return [cmd]
    if os.name == "nt" and path.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", path]
    return [path]


def run_cmd(args: list[str], timeout: int = 20, env: dict | None = None) -> tuple[int, str]:
    """执行命令并返回 (退出码, 合并后的输出)。找不到命令返回 (-1, message)。"""
    full_args = resolve_executable(args[0]) + list(args[1:])
    try:
        proc = subprocess.run(
            full_args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            env=env or os.environ.copy(),
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return -1, f"command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {timeout}s: {' '.join(args)}"
