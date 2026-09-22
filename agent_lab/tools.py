"""agent_lab.tools —— Agent 统一工具集（纯函数 + 真实 MySQL 数据）。

【W1 学习点：工具粒度设计】
1. 一个工具只做一件事，参数少且是枚举/日期 → 模型容易选对，人也容易评测；
2. **不做"万能 SQL 工具"**：那等于让模型写 SQL，正确性无法用规则判定。
   本项目主线选择"确定性取数 + LLM 只在两端"，根因就在这里；
3. 返回值里必须带**口径说明**（definition/unit），否则模型会误读数字
   （例如把 refund_rate 的 0-1 比例当成百分数）；
4. 所有工具只读（仅 SELECT）+ 参数白名单校验 —— 这是 Agent 安全的第一层，
   和原项目后端 `sql_guard.py` 的思路一致。

【口径来源】与《项目二_数据能力盘点与Phase0业务定义.md》的 KPI 口径表一一对应。
注意：本数据集**没有成本字段**，因此不存在任何毛利类指标（这是数据约束，不是遗漏）。
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_lab.db import connect as _connect  # noqa: E402  连接参数统一在 db.py，含 .env 加载

# ---------------------------------------------------------------- 口径表
# 企业里这张表应该版本化到数据库（kpi_definitions），让报告能标注"用了哪版口径"。
METRICS: dict[str, dict[str, str]] = {
    "revenue": {
        "sql": "SUM(payment_amount)",
        "label": "实付额",
        "definition": "销售额默认口径 = SUM(payment_amount)，用户实际支付金额（已扣优惠）",
        "unit": "元",
    },
    "orders": {
        "sql": "COUNT(*)",
        "label": "订单数",
        "definition": "订单行数；本数据集一单一品，订单数 == 行数",
        "unit": "单",
    },
    "aov": {
        "sql": "SUM(payment_amount) / COUNT(*)",
        "label": "客单价",
        "definition": "实付额 / 订单数",
        "unit": "元/单",
    },
    "refund_rate": {
        "sql": "SUM(CASE WHEN is_refund = '是' THEN payment_amount ELSE 0 END) / NULLIF(SUM(payment_amount), 0)",
        "label": "退款金额率",
        "definition": "退款订单金额 / 实付额，取值 0~1（不是百分数！）",
        "unit": "比例",
    },
    "discount_rate": {
        "sql": "SUM(discount_amount) / NULLIF(SUM(order_amount), 0)",
        "label": "折扣率",
        "definition": "优惠金额 / 下单额（优惠前），取值 0~1",
        "unit": "比例",
    },
}

DIMENSIONS: dict[str, str] = {
    "platform": "platform_type",
    "channel": "channel_id",
    "product": "product_id",
}


class ToolError(Exception):
    """工具参数/执行错误。会被当作 observation 回传给模型，让它自我修正。"""


def _num(value: Any) -> Any:
    """Decimal → float，方便 JSON 序列化。"""
    if isinstance(value, Decimal):
        return float(value)
    return value


def _rows(sql: str, args: tuple = ()) -> list[dict]:
    """执行查询并返回 dict 列表，同时把 Decimal 转成 float（便于 JSON 序列化）。"""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return [{k: _num(v) for k, v in row.items()} for row in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------- 参数校验
def _check_metric(metric: str) -> None:
    """指标名必须在口径表白名单里（不在就报错，而不是猜一个默认值）。"""
    if metric not in METRICS:
        raise ToolError(f"未知指标 '{metric}'，可用指标：{list(METRICS)}")


def _check_dimension(dimension: str) -> None:
    """维度名必须是 platform/channel/product 之一（白名单，防注入）。"""
    if dimension not in DIMENSIONS:
        raise ToolError(f"未知维度 '{dimension}'，可用维度：{list(DIMENSIONS)}")


def _check_date(value: str, field: str) -> None:
    """日期校验。

    【D2 压测抓出的 bug】原实现只用正则校验格式，于是 "2025-13-01" 被放行，
    MySQL 把它当无效日期返回空值，模型看到 null/0 行只能自己去猜"13 月不存在"。
    **本该由工具明确报错**（错误信息越明确，模型自我修正越可靠）。

    【补修】原实现只检查 day 在 01~31，于是 "2025-02-31"、"2025-04-31" 仍被放行 ——
    和 "2025-13" 是同一类 bug（只校格式/粗范围，不校真实日历）。
    改用 calendar.monthrange 按当月实际天数校验，闰年由标准库负责。
    """
    import calendar
    import re

    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    if not m:
        raise ToolError(f"{field} 必须是 YYYY-MM-DD 格式，收到 '{value}'")
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not 1 <= month <= 12:
        raise ToolError(f"{field} 的月份必须在 01~12 之间，收到 '{value}'（不存在 13 月这种月份）")
    if not 2020 <= year <= 2030:
        raise ToolError(f"{field} 的年份 {year} 超出本数据集范围（数据仅覆盖 2025 年）")
    last_day = calendar.monthrange(year, month)[1]
    if not 1 <= day <= last_day:
        raise ToolError(
            f"{field} 的日期必须在 01~{last_day:02d} 之间（{year} 年 {month} 月实际只有 {last_day} 天），"
            f"收到 '{value}'"
        )


def _check_month(value: str, field: str) -> None:
    """月份校验：必须 YYYY-MM，且月份在 01~12、年份在数据集范围内。"""
    import re

    m = re.fullmatch(r"(\d{4})-(\d{2})", value or "")
    if not m:
        raise ToolError(f"{field} 必须是 YYYY-MM 格式，收到 '{value}'")
    month = int(m.group(2))
    if not 1 <= month <= 12:
        raise ToolError(f"{field} 的月份必须在 01~12 之间，收到 '{value}'")
    if not 2020 <= int(m.group(1)) <= 2030:
        raise ToolError(f"{field} 的年份 {m.group(1)} 超出本数据集范围（数据仅覆盖 2025 年）")


# ---------------------------------------------------------------- 工具实现
def query_metrics(
    metric: str,
    start_date: str,
    end_date: str,
    dimension: str | None = None,
    dimension_value: str | None = None,
) -> dict:
    """查询**单个指标**在某个时间区间内的值（可选按某个维度过滤）。"""
    _check_metric(metric)
    _check_date(start_date, "start_date")
    _check_date(end_date, "end_date")

    where = "WHERE order_date BETWEEN %s AND %s"
    args: list[Any] = [start_date, end_date]
    if dimension:
        _check_dimension(dimension)
        where += f" AND {DIMENSIONS[dimension]} = %s"
        args.append(dimension_value)

    row = _rows(f"SELECT {METRICS[metric]['sql']} AS value, COUNT(*) AS rows_cnt FROM orders {where}", tuple(args))[0]
    return {
        "metric": metric,
        "label": METRICS[metric]["label"],
        "definition": METRICS[metric]["definition"],
        "unit": METRICS[metric]["unit"],
        "period": f"{start_date} ~ {end_date}",
        "filter": f"{dimension}={dimension_value}" if dimension else "无",
        "value": round(float(row["value"]), 4) if row["value"] is not None else None,
        "sample_rows": row["rows_cnt"],
    }


def monthly_trend(metric: str, months: int = 12) -> dict:
    """查询最近 N 个月的**月度趋势**（按自然月聚合），用于看走势与环比。"""
    _check_metric(metric)
    months = int(months)
    if not 1 <= months <= 24:
        raise ToolError(f"months 必须在 1~24 之间，收到 {months}")

    rows = _rows(
        f"""
        SELECT DATE_FORMAT(order_date, '%%Y-%%m') AS ym,
               {METRICS[metric]['sql']} AS value,
               COUNT(*) AS orders_cnt
        FROM orders
        GROUP BY ym ORDER BY ym DESC LIMIT %s
        """,
        (months,),
    )
    rows.reverse()
    series = [{"month": r["ym"], "value": round(float(r["value"]), 4)} for r in rows]
    # 环比：让模型不必自己算（也避免它算错）—— 代码负责精确计算，模型负责解读
    for i, item in enumerate(series):
        if i == 0:
            item["mom_pct"] = None
        else:
            prev = series[i - 1]["value"]
            item["mom_pct"] = round((item["value"] - prev) / prev * 100, 2) if prev else None
    return {
        "metric": metric,
        "label": METRICS[metric]["label"],
        "unit": METRICS[metric]["unit"],
        "months_returned": len(series),
        "series": series,
        "note": "mom_pct 为环比百分比，已由代码算好，请直接引用，不要自己重算",
    }


def rank_dimension(
    dimension: str,
    metric: str = "revenue",
    start_date: str = "2025-01-01",
    end_date: str = "2025-12-31",
    top_n: int = 5,
) -> dict:
    """按某个维度（platform/channel/product）对指标做**排名**，返回 TOP N 与占比。"""
    _check_dimension(dimension)
    _check_metric(metric)
    _check_date(start_date, "start_date")
    _check_date(end_date, "end_date")
    top_n = int(top_n)
    if not 1 <= top_n <= 20:
        raise ToolError(f"top_n 必须在 1~20 之间，收到 {top_n}")

    col = DIMENSIONS[dimension]
    rows = _rows(
        f"""
        SELECT COALESCE({col}, '未知') AS dim_value,
               {METRICS[metric]['sql']} AS value,
               COUNT(*) AS orders_cnt
        FROM orders
        WHERE order_date BETWEEN %s AND %s
        GROUP BY dim_value
        ORDER BY value DESC
        """,
        (start_date, end_date),
    )
    total = sum(float(r["value"] or 0) for r in rows)
    out = []
    for i, r in enumerate(rows[:top_n], 1):
        v = float(r["value"] or 0)
        out.append({
            "rank": i,
            "dim_value": r["dim_value"],
            "value": round(v, 4),
            "share_pct": round(v / total * 100, 2) if total else None,
            "orders_cnt": r["orders_cnt"],
        })
    return {
        "dimension": dimension,
        "metric": metric,
        "label": METRICS[metric]["label"],
        "unit": METRICS[metric]["unit"],
        "period": f"{start_date} ~ {end_date}",
        "total_value": round(total, 4),
        "distinct_values": len(rows),
        "top": out,
        "note": (
            f"该维度共 {len(rows)} 个取值，仅返回 TOP{top_n}。"
            "长尾取值订单量极小，做统计判断时不要采信（本项目规定订单数 < 30 不参与异常判定）"
        ),
    }


# ---------------------------------------------------------------- 纯计算（可脱离数据库单测）
def decompose(rev0: float, n0: int, rev1: float, n1: int) -> tuple[float, float, float]:
    """量价三因子分解：返回 (量效应, 价效应, 交互项)，三项之和**恒等于** ΔR = rev1 - rev0。

    数学（Revenue = 订单数 N × 客单价 AOV）：
        ΔR = (N1-N0)·AOV0        量效应
           + N0·(AOV1-AOV0)      价效应
           + (N1-N0)·(AOV1-AOV0) 交互项

    这个恒等式是精确的（不是近似），所以可以用 `sum - ΔR == 0` 当断言。
    原先是 `contribution_breakdown` 里的嵌套函数，为了能独立单测才提到模块级 ——
    **嵌套函数在 Python 里 import 不到，等于不可测**。
    """
    aov0 = rev0 / n0 if n0 else 0.0
    aov1 = rev1 / n1 if n1 else 0.0
    volume = (n1 - n0) * aov0
    price = n0 * (aov1 - aov0)
    inter = (n1 - n0) * (aov1 - aov0)
    return volume, price, inter


def contribution_breakdown(dimension: str, period_a: str, period_b: str) -> dict:
    """对比两个自然月的**量价结构分解 + 贡献度**（回答"为什么涨/跌"的核心工具）。

    数学（Revenue = 订单数 N × 客单价 AOV）：
        ΔR = (N1-N0)·AOV0        量效应
           + N0·(AOV1-AOV0)      价效应
           + (N1-N0)·(AOV1-AOV0) 交互项
    三项**精确加总等于 ΔR**（tools 里做了断言校验，误差 < 1e-6）。
    """
    _check_dimension(dimension)
    _check_month(period_a, "period_a")
    _check_month(period_b, "period_b")

    col = DIMENSIONS[dimension]

    def agg(period: str) -> dict[str, dict]:
        """按维度聚合某一期的实付额与订单数 → {维度值: {rev, n}}。"""
        rows = _rows(
            f"""
            SELECT COALESCE({col}, '未知') AS dim_value,
                   SUM(payment_amount) AS rev, COUNT(*) AS n
            FROM orders
            WHERE DATE_FORMAT(order_date, '%%Y-%%m') = %s
            GROUP BY dim_value
            """,
            (period,),
        )
        return {r["dim_value"]: {"rev": float(r["rev"] or 0), "n": int(r["n"] or 0)} for r in rows}

    a, b = agg(period_a), agg(period_b)
    keys = sorted(set(a) | set(b))

    total_a = sum(v["rev"] for v in a.values())
    total_b = sum(v["rev"] for v in b.values())
    total_na = sum(v["n"] for v in a.values())
    total_nb = sum(v["n"] for v in b.values())

    delta_total = total_b - total_a
    items = []
    for k in keys:
        ra, rb = a.get(k, {"rev": 0.0, "n": 0}), b.get(k, {"rev": 0.0, "n": 0})
        vol, pri, inter = decompose(ra["rev"], ra["n"], rb["rev"], rb["n"])
        delta = rb["rev"] - ra["rev"]
        items.append({
            "dim_value": k,
            "revenue_a": round(ra["rev"], 2),
            "revenue_b": round(rb["rev"], 2),
            "delta": round(delta, 2),
            "delta_share_pct": round(delta / abs(delta_total) * 100, 2) if delta_total else None,
            "orders_a": ra["n"], "orders_b": rb["n"],
            "aov_a": round(ra["rev"] / ra["n"], 2) if ra["n"] else None,
            "aov_b": round(rb["rev"] / rb["n"], 2) if rb["n"] else None,
            "volume_effect": round(vol, 2),
            "price_effect": round(pri, 2),
            "interaction": round(inter, 2),
            "decompose_sum_check": round(vol + pri + inter - delta, 8),  # 应恒为 0
        })
    items.sort(key=lambda x: abs(x["delta"]), reverse=True)

    vol_t, pri_t, inter_t = decompose(total_a, total_na, total_b, total_nb)
    return {
        "dimension": dimension,
        "period_a": period_a, "period_b": period_b,
        "total_revenue_a": round(total_a, 2),
        "total_revenue_b": round(total_b, 2),
        "total_delta": round(delta_total, 2),
        "total_mom_pct": round(delta_total / total_a * 100, 2) if total_a else None,
        "orders_a": total_na, "orders_b": total_nb,
        "aov_a": round(total_a / total_na, 2) if total_na else None,
        "aov_b": round(total_b / total_nb, 2) if total_nb else None,
        "total_volume_effect": round(vol_t, 2),
        "total_price_effect": round(pri_t, 2),
        "total_interaction": round(inter_t, 2),
        "total_decompose_check": round(vol_t + pri_t + inter_t - delta_total, 8),
        "breakdown": items,
        "reading_guide": (
            "total_volume_effect>0 表示靠单量增长；total_price_effect<0 表示客单价拖累；"
            "breakdown 里 delta_share_pct 是各维度对总变化的贡献占比（正=拉动，负=拖累）；"
            "total_decompose_check 必须为 0，否则说明计算有误"
        ),
    }


# ---------------------------------------------------------------- 工具注册表
TOOL_SPECS: dict[str, dict[str, Any]] = {
    "query_metrics": {
        "func": query_metrics,
        "desc": "查询单个指标在时间区间的值",
        "params": {
            "metric": "revenue|orders|aov|refund_rate|discount_rate（必填）",
            "start_date": "YYYY-MM-DD（必填）",
            "end_date": "YYYY-MM-DD（必填）",
            "dimension": "platform|channel|product（可选，按维度过滤）",
            "dimension_value": "维度取值（可选，配合 dimension 使用）",
        },
    },
    "monthly_trend": {
        "func": monthly_trend,
        "desc": "查询最近 N 个月的月度趋势（含环比，已算好）",
        "params": {"metric": "同上（必填）", "months": "1~24，默认 12"},
    },
    "rank_dimension": {
        "func": rank_dimension,
        "desc": "按维度排名，返回 TOP N 与占比",
        "params": {
            "dimension": "platform|channel|product（必填）",
            "metric": "同上，默认 revenue",
            "start_date": "YYYY-MM-DD，默认 2025-01-01",
            "end_date": "YYYY-MM-DD，默认 2025-12-31",
            "top_n": "1~20，默认 5",
        },
    },
    "contribution_breakdown": {
        "func": contribution_breakdown,
        "desc": "对比两个自然月的量价结构分解 + 各维度贡献度（回答'为什么涨/跌'）",
        "params": {
            "dimension": "platform|channel|product（必填）",
            "period_a": "基期 YYYY-MM（必填）",
            "period_b": "对比期 YYYY-MM（必填）",
        },
    },
}


def call_tool(name: str, args: dict) -> dict:
    """统一入口：未知工具/参数错误都返回结构化错误，由调用方回传给模型自我修正。"""
    if name not in TOOL_SPECS:
        raise ToolError(f"不存在工具 '{name}'，可用工具：{list(TOOL_SPECS)}")
    if not isinstance(args, dict):
        raise ToolError(f"参数必须是 JSON 对象，收到 {type(args).__name__}")
    return TOOL_SPECS[name]["func"](**args)


def tool_catalog_text() -> str:
    """给模型看的工具说明书（工具名 + 说明 + 参数）。"""
    lines = []
    for name, spec in TOOL_SPECS.items():
        lines.append(f"- {name}: {spec['desc']}")
        for p, d in spec["params"].items():
            lines.append(f"    - {p}: {d}")
    return "\n".join(lines)


if __name__ == "__main__":
    # 自检：直接跑这个文件可以验证工具与数据库连通（不花任何 LLM 费用）
    print("工具清单：")
    print(tool_catalog_text())
    print("\n--- query_metrics ---")
    print(query_metrics("revenue", "2025-11-01", "2025-11-30"))
    print("\n--- monthly_trend(revenue, 4) ---")
    print(monthly_trend("revenue", 4))
    print("\n--- rank_dimension(platform) ---")
    print(rank_dimension("platform", "revenue", "2025-11-01", "2025-11-30", 3))
    print("\n--- contribution_breakdown(platform, 2025-10, 2025-11) ---")
    r = contribution_breakdown("platform", "2025-10", "2025-11")
    print({k: v for k, v in r.items() if not isinstance(v, list)})
    print("breakdown 前 3 项：")
    for it in r["breakdown"][:3]:
        print("   ", it)
