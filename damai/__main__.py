"""``python -m damai`` 入口：只负责把参数交给 :func:`damai.cli.main` 并回传退出码。"""

from __future__ import annotations

import sys

from damai.cli import main

if __name__ == "__main__":
    sys.exit(main())
