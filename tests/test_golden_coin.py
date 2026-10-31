"""淘金币链路冒烟（与 ``test_taobao.py`` 中的同名用例重复，保留作快速单点验证）。

运行::

    .venv/Scripts/python.exe -m pytest tests/test_golden_coin.py -v
"""

import pytest

from flows import TaobaoFlow

pytestmark = pytest.mark.device


def test_home_launch(dev):
    """启动淘宝后能进入首页（连通性冒烟）"""
    flow = TaobaoFlow(dev)
    home = flow.start(fresh=True)

    assert home.is_here(), f"启动后未进入淘宝首页：请确认 App 已安装、首页选择器未失效（当前 activity={home.activity}）"

def test_coin_entry_visible(dev):
    """推荐页「领淘金币」入口按活动位投放（未投放则跳过）"""
    flow = TaobaoFlow(dev)
    flow.start(fresh=True)

    found, text, shot = flow.probe_coin_entry()
    if not found:
        pytest.skip(f"推荐页当前未投放「领淘金币」挂件（活动位，双 11/618 期间会整块换皮肤）。现场截图={shot}")

    assert "金币" in text, f"金币入口 desc 异常：{text!r}（期望包含「金币」）"