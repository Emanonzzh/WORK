"""仓库级 pytest 配置：把仓库根目录放进 sys.path。

原因：agent_lab 内部用的是 `from agent_lab.tools import ...` 这种**绝对导入**，
所以运行测试时必须保证仓库根目录在 sys.path 上。放在 conftest.py 里做，
比要求每个人记住设 PYTHONPATH 可靠。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
