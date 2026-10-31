"""项目根 conftest：挂载 Allure 报告插件。

pytest 7+ 不允许在子目录 conftest 里定义 pytest_plugins，必须放在 rootdir 这一层。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest_plugins = ["core.report.plugin"]
