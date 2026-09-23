"""连接层唯一性断言（不需要 MySQL、不需要 LLM）。

【为什么值得一条测试】
2026-09-23 之前，`db.py` 号称统一连接层，但 `tools.py` 一直自带一份 `_connect()` 站在层外——
这种"抽了层但主模块没接上"的漂移，靠人记住是不可能的，只能靠一条会红的测试钉住。

【为什么用读源码的方式而不是调用】
真实调用会碰数据库，而本测试套件的硬保证是"不连 MySQL"。
所以这里做的是**静态断言**：扫源码，确认 `pymysql.connect(` 只出现在 db.py 一处。
"""
from __future__ import annotations

from pathlib import Path

import agent_lab.db as db
import agent_lab.tools as tools

LAB_DIR = Path(tools.__file__).resolve().parent
FORBIDDEN = "pymysql.connect("


def _sources_with_connect() -> list[str]:
    """返回 agent_lab 下（排除 tests）出现 pymysql.connect( 的文件名。"""
    hits = []
    for py in sorted(LAB_DIR.glob("*.py")):
        if FORBIDDEN in py.read_text(encoding="utf-8"):
            hits.append(py.name)
    return hits


def test_only_db_module_opens_connections() -> None:
    """连接只能由 db.py 开。别处出现 pymysql.connect( 就说明有人又绕过统一层。"""
    assert _sources_with_connect() == ["db.py"], (
        f"以下模块绕过了 agent_lab.db 直接建连接：{_sources_with_connect()}"
    )


def test_tools_uses_the_shared_layer() -> None:
    """tools 用的就是 db.connect 本身，不是长得一样的另一份实现。"""
    assert tools._connect is db.connect


def test_db_layer_has_no_credentials_baked_in() -> None:
    """凭据一律走环境变量，源码里不得出现密码字面量（历史上泄漏过一次真实 key）。"""
    text = (LAB_DIR / "db.py").read_text(encoding="utf-8")
    assert "password=os.getenv" in text
    assert 'password="' not in text and "password='" not in text
