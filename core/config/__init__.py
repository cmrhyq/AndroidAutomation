"""配置层。

- ``config.py``    设备参数（UDID、包名、Activity），来自 ``.env``
- ``damai.py``     大麦抢票业务参数（抢哪场、哪个票档），来自 ``config/damai.jsonc``

两者互不覆盖：改设备看环境变量，改业务看 jsonc。
"""

from __future__ import annotations

from core.config.config import DeviceConfig

__all__ = ["DeviceConfig"]
