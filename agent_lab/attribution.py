"""agent_lab.attribution —— 自动多层下钻：总量 → 平台 → 该平台的商品。

【它解决什么】
`contribution_breakdown` 一次只能回答"某一维度上谁贡献最大"。但业务真正问的是
"涨了 138 万，**具体是谁**"——这需要一层层钻下去：先看平台，锁定 APP 贡献了 53%，
再看 APP 内部是哪个商品把它的量拉起来的。手工做这件事，每换一个问题就要重写一遍 SQL；
这里把它固化成一个函数。

【设计约束（刻意的）】
1. **不写任何 SQL**。全部通过 `tools.call_tool` 走既有工具 —— 于是自动继承
   只读、参数白名单、口径随值返回、加总闭合断言这四层保障。
   一旦这里出现第二条 SQL 路径，上面那些保障就都不覆盖了。
2. 每层只取 `top_n` 个分支继续钻，否则 996 个商品 × 6 个平台会炸开。
3. 每一层都带 `parent` 和 `share_of_parent_pct`，让结论可以被逐层对账：
   子层的 `total_delta` 必须等于父层被选中那一行的 `delta`（`tests/test_attribution.py` 钉的就是这条）。

用法：
    python -m agent_lab.attribution                # 默认 2025-10 -> 2025-11，平台 -> 商品
    python -m agent_lab.attribution --period-a 2025-09 --period-b 2025-10
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_lab.tools import ToolError, call_tool  # noqa: E402

DEFAULT_LEVELS = ("platform", "product")


def drill_down(
    period_a: str,
    period_b: str,
    levels: tuple[str, ...] = DEFAULT_LEVELS,
    top_n: int = 1,
) -> dict[str, Any]:
    """逐层下钻，返回一条可对账的链。

    levels 例：("platform", "product") = 先按平台分解，取贡献最大的平台，再在该平台内按商品分解。
    top_n：每层继续往下钻的分支数（默认 1，只看最大那个）。
    """
    if not levels:
        raise ToolError("levels 不能为空，至少要有一层维度")
    if top_n < 1:
        raise ToolError(f"top_n 必须 >= 1，收到 {top_n}")
    for i, dim in enumerate(levels):
        if i and dim == levels[i - 1]:
            raise ToolError(f"levels 第 {i} 层与上一层同为 '{dim}'，下钻没有新信息，请换更细的维度")

    chain: list[dict[str, Any]] = []
    parent_dim: str | None = None
    parent_value: str | None = None

    for depth, dim in enumerate(levels):
        args: dict[str, Any] = {"dimension": dim, "period_a": period_a, "period_b": period_b}
        if parent_dim is not None:
            args.update({"filter_dimension": parent_dim, "filter_value": parent_value})
        result = call_tool("contribution_breakdown", args)

        picked = result["breakdown"][:top_n]
        step = {
            "depth": depth,
            "dimension": dim,
            "parent": None if parent_dim is None else {parent_dim: parent_value},
            "subset_total_delta": result["total_delta"],
            "subset_volume_effect": result["total_volume_effect"],
            "subset_price_effect": result["total_price_effect"],
            "subset_interaction": result["total_interaction"],
            "decompose_check": result["total_decompose_check"],
            "groups": len(result["breakdown"]),
            "top": [
                {
                    "value": item["dim_value"],
                    "delta": item["delta"],
                    "share_of_subset_pct": item["delta_share_pct"],
                    "orders_a": item["orders_a"],
                    "orders_b": item["orders_b"],
                }
                for item in picked
            ],
        }
        chain.append(step)

        if not picked:
            break                      # 这一层没有任何数据，再钻下去没有意义
        parent_dim, parent_value = dim, picked[0]["dim_value"]

    return {
        "period_a": period_a,
        "period_b": period_b,
        "levels": list(levels),
        "top_n": top_n,
        "chain": chain,
        "reading_guide": (
            "每一层的 subset_total_delta 必须等于上一层被选中那一行的 delta，"
            "否则下钻链断了；decompose_check 必须为 0；"
            "share_of_subset_pct 是该分支占**本层子集**变化的比例，不是占全市场"
        ),
    }


def _text_report(drill: dict[str, Any]) -> str:
    """把下钻链打成人能读的一段话（报告与命令行共用）。"""
    lines = [f"下钻链 {drill['period_a']} -> {drill['period_b']}"]
    for step in drill["chain"]:
        parent = "全市场" if step["parent"] is None else str(step["parent"])
        lines.append(
            f"  [{step['depth']}] {step['dimension']}（范围：{parent}，{step['groups']} 组）"
            f" Δ={step['subset_total_delta']:.2f} 量={step['subset_volume_effect']:.2f}"
            f" 价={step['subset_price_effect']:.2f} 交互={step['subset_interaction']:.2f}"
            f" check={step['decompose_check']}"
        )
        for t in step["top"]:
            lines.append(
                f"        {t['value']}: Δ={t['delta']:.2f} 占本层 {t['share_of_subset_pct']}%"
                f"（订单 {t['orders_a']} -> {t['orders_b']}）"
            )
    return "\n".join(lines)


def main() -> int:
    """命令行入口：跑一次下钻并把链条打成可读文本（ToolError 回退出码 1 而不是抛栈）。"""
    ap = argparse.ArgumentParser(description="销售经营指标的多层下钻")
    ap.add_argument("--period-a", default="2025-10")
    ap.add_argument("--period-b", default="2025-11")
    ap.add_argument("--levels", default=",".join(DEFAULT_LEVELS), help="逗号分隔的维度链，如 platform,product")
    ap.add_argument("--top-n", type=int, default=1)
    args = ap.parse_args()

    try:
        drill = drill_down(args.period_a, args.period_b, tuple(args.levels.split(",")), args.top_n)
    except ToolError as e:
        print(f"ToolError: {e}")
        return 1
    print(_text_report(drill))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
