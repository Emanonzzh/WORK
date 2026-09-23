"""多层下钻的单测：全部用**假工具**驱动，不连 MySQL。

===============================================================================
为什么可以不用真库：`drill_down` 自己的逻辑只有三件事——
① 入参守卫；② 把上一层选中的维度值传成下一层的 filter；③ 某层空了就停。
真正的算术在 `contribution_breakdown` 里，那边已有 238 项测试与真库不变量兜底。
所以这里测的是**接线**，用假数据反而更准：可以构造"某层为空""某层只有一行"这类真库里拿不到的形状。
===============================================================================
"""
from __future__ import annotations

import pytest

from agent_lab import attribution as at
from agent_lab.tools import ToolError


def _fake_result(dimension, rows):
    """造一个与 contribution_breakdown 同形状的最小返回体。"""
    total_delta = round(sum(d for _, d in rows), 2)
    return {
        "dimension": dimension,
        "filter": None,
        "total_delta": total_delta,
        "total_volume_effect": total_delta,
        "total_price_effect": 0.0,
        "total_interaction": 0.0,
        "total_decompose_check": 0.0,
        "breakdown": [
            {"dim_value": v, "delta": d, "delta_share_pct": 100.0, "orders_a": 1, "orders_b": 2}
            for v, d in rows
        ],
    }


# ---------------------------------------------------------------- 入参守卫
def test_empty_levels_rejected():
    with pytest.raises(ToolError, match="不能为空"):
        at.drill_down("2025-10", "2025-11", levels=())


def test_top_n_must_be_positive():
    with pytest.raises(ToolError, match="top_n"):
        at.drill_down("2025-10", "2025-11", levels=("platform",), top_n=0)


def test_duplicate_consecutive_level_rejected():
    """连续两层同维度没有信息量，而且底层工具也会拒 —— 在这里提前拒得更明白。"""
    with pytest.raises(ToolError, match="上一层同"):
        at.drill_down("2025-10", "2025-11", levels=("platform", "platform"))


# ---------------------------------------------------------------- 接线正确性
def test_second_level_inherits_parent_filter(monkeypatch):
    """核心不变量：第二层必须带上第一层选中值作为 filter，否则下钻就是假的。"""
    calls = []

    def fake_call_tool(name, args):
        calls.append((name, dict(args)))
        if args["dimension"] == "platform":
            return _fake_result("platform", [("APP", 700.0), ("web网站", -100.0)])
        return _fake_result("product", [("PR000001", 650.0)])

    monkeypatch.setattr(at, "call_tool", fake_call_tool)
    drill = at.drill_down("2025-10", "2025-11", levels=("platform", "product"))

    assert len(calls) == 2
    first_name, first_args = calls[0]
    second_name, second_args = calls[1]
    assert first_name == second_name == "contribution_breakdown"
    assert "filter_dimension" not in first_args, "第一层不该带过滤"
    assert second_args["filter_dimension"] == "platform"
    assert second_args["filter_value"] == "APP", "应继承上一层 |delta| 最大的那一行"
    chain = drill["chain"]
    assert chain[1]["parent"] == {"platform": "APP"}
    assert chain[1]["subset_total_delta"] == 650.0


def test_top_n_keeps_only_requested_branches(monkeypatch):
    def fake_call_tool(name, args):
        if args["dimension"] == "platform":
            return _fake_result("platform", [("APP", 700.0), ("微信公众号", 600.0), ("淘宝", 50.0)])
        return _fake_result("product", [("PR000001", 10.0)])

    monkeypatch.setattr(at, "call_tool", fake_call_tool)
    drill = at.drill_down("2025-10", "2025-11", levels=("platform", "product"), top_n=2)
    assert [t["value"] for t in drill["chain"][0]["top"]] == ["APP", "微信公众号"]
    # 只有 top_n 里的第 1 个会继续往下钻
    assert drill["chain"][1]["parent"] == {"platform": "APP"}


def test_chain_stops_on_empty_level(monkeypatch):
    """某层查不到数据就停，不要带着 None 继续钻出假结论。"""
    seen = []

    def fake_call_tool(name, args):
        seen.append(args["dimension"])
        if args["dimension"] == "platform":
            return _fake_result("platform", [("APP", 700.0)])
        return _fake_result("product", [])          # 该平台内没有任何商品

    monkeypatch.setattr(at, "call_tool", fake_call_tool)
    drill = at.drill_down("2025-10", "2025-11", levels=("platform", "product", "channel"))
    assert seen == ["platform", "product"], "空层之后不应再往下钻"
    assert len(drill["chain"]) == 2


def test_no_sql_is_written_here() -> None:
    """下钻模块必须只走工具，不许自己拼 SQL —— 否则就绕过了只读/白名单/口径那几层保障。"""
    import inspect

    src = inspect.getsource(at)
    for kw in ("SELECT ", "pymysql", "FROM orders", "db.connect", "query("):
        assert kw not in src, f"attribution.py 里出现了 {kw!r}，取数必须只经 call_tool"
