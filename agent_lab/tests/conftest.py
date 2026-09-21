"""agent_lab 单测的公共夹具。

===============================================================================
【这组测试的硬保证】**不连 MySQL、不调 LLM、不发任何网络请求。**

这不是靠"大家自觉"实现的，而是靠下面这个 autouse 夹具：
它把 `pymysql.connect` 和 `urllib.request.urlopen` 换成"一调用就 AssertionError"。
任何测试只要不小心引入了外部依赖，会**立刻响亮地失败**，
而不是悄悄连上数据库、跑到一半才因为环境不同而红/绿飘忽。

为什么要在意这一点：
一个"必须有 MySQL 才能跑"的测试套件，在面试官的机器上、在 CI 里、
在你换电脑后的第一天，都是跑不起来的 —— 那它就不是资产，是负债。
===============================================================================
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 双保险：即使有人单独 `pytest agent_lab/tests/xxx.py` 而没走 rootdir 的 conftest
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _forbid_external_io(monkeypatch):
    """禁止真实数据库连接与网络请求（详见模块 docstring）。"""
    import urllib.request

    import pymysql

    def _no_db(*args, **kwargs):
        raise AssertionError(
            "单测禁止连数据库：这条测试应当只覆盖纯逻辑。"
            "需要数据库的部分请放到 api_smoke_test.py / evaluate_anomaly.py 之类的集成脚本里。"
        )

    def _no_net(*args, **kwargs):
        raise AssertionError(
            "单测禁止发网络请求：LLM 调用必须被 monkeypatch 掉（见 test_react_loop.py）。"
        )

    monkeypatch.setattr(pymysql, "connect", _no_db)
    monkeypatch.setattr(urllib.request, "urlopen", _no_net)
    yield
