"""项目根 conftest：挂载 Allure 报告插件。

为什么放在根目录
----------------
pytest 7 起不允许在**非顶层** conftest.py 里定义 ``pytest_plugins``
（``tests/conftest.py`` 属于子目录，会直接报错）。所以要挂插件必须放在
rootdir 这一层的 conftest.py。

顺带的好处：pytest 以 ``prepend`` 模式导入 conftest 时会把它所在目录插入
``sys.path``，因此项目根自动可导入，``tests/conftest.py`` 里那段手动
``sys.path.insert`` 就成了兜底而非必需。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest_plugins = ["core.report.plugin"]
