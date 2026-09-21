"""agent_lab.report —— 经营分析报告生成 + 数字对账（防幻觉）。

【核心设计：数字由代码产生，模型不碰数值】
- 报告用 Jinja2 模板渲染，模板里**只有占位符**，没有任何计算
- 所有数值在 Python 里算好后以**字符串**传给模板（模板无法"顺手算错"）
- LLM 若参与，只写「结论与建议」的自然语言，且**输入是已算好的结构化结果**，不给它原始明细

【两道对账（这是本模块的重点）】
1. **文本级对账** `verify_numbers`：把渲染后的报告里**每一个数字**抽出来，
   逐个比对"是否来自分析结果"。抽不到来源的 → 违规，报告不可发布。
2. **数据库级复算** `reconcile_db`：把报告的头部指标（实付额/订单数/客单价/退款率/折扣率/环比）
   **重新从 MySQL 算一遍**再比对 —— 这是比文本对账更强的保证。

用法：
    python agent_lab/report.py                      # 生成 2025-11 报告（对比 2025-10）
    python agent_lab/report.py --period-b 2025-06 --period-a 2025-05
    python agent_lab/report.py --with-llm           # 额外让 LLM 写结论，并对账其文本
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from jinja2 import Template  # noqa: E402

from agent_lab.anomaly import detect  # noqa: E402
from agent_lab.db import query  # noqa: E402
from agent_lab.tools import (  # noqa: E402
    DIMENSIONS,
    METRICS,
    contribution_breakdown,
    query_metrics,
    rank_dimension,
)

TEMPLATE_PATH = Path(__file__).with_name("templates") / "business_report.md.j2"
OUT_DIR = Path(__file__).with_name("reports")
KPI_VERSION = "v1 (2026-09-15)"

# 报告里合法出现的"非数据常量"（判据参数等），显式白名单，避免误判为幻觉
CONSTANT_ALLOWED = {"3.5", "30", "5"}


# ------------------------------------------------------------------ 格式化（模板只拿字符串）
def _month_range(ym: str) -> tuple[str, str]:
    """'YYYY-MM' → (当月第一天, 当月最后一天)，喂给 SQL 的 BETWEEN。"""
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{ym}-01", f"{ym}-{calendar.monthrange(y, m)[1]:02d}"


def _money(v) -> str:
    """金额格式：千分位 + 两位小数。None → '—'（不写成 0，避免把缺失当零）。"""
    return f"{float(v):,.2f}" if v is not None else "—"


def _int(v) -> str:
    """整数格式：千分位。"""
    return f"{int(v):,}" if v is not None else "—"


def _pct_from_rate(v) -> str:
    """比例（0.1335）→ 百分数字符串（'13.35%'）。口径表白纸黑字写明比例是 0~1。"""
    return f"{float(v) * 100:.2f}%" if v is not None else "—"


def _signed_pct(v) -> str:
    """带符号百分比（' +15.92%' / '-5.87%'），用于环比列。"""
    return f"{float(v):+.2f}%" if v is not None else "—"


# ------------------------------------------------------------------ 收集分析结果
def collect(period_a: str, period_b: str, dimension: str = "platform") -> dict:
    """收集报告所需的全部数据，构成**唯一数据源**（授权数字清单也来自这里）。

    包含：两期概览指标、量价分解、维度排名、对比期异常、订单总行数、派生值（环比等）。
    报告里出现的每个数字都必须能在这份结果里找到 —— 这是文本对账的前提。
    """
    sa, ea = _month_range(period_a)
    sb, eb = _month_range(period_b)

    def overview(start: str, end: str) -> dict:
        """取一期（月）的 5 个核心指标：实付额/订单数/客单价/退款率/折扣率。"""
        out = {}
        for metric in ("revenue", "orders", "aov", "refund_rate", "discount_rate"):
            out[metric] = query_metrics(metric, start, end)["value"]
        return out

    ov_a, ov_b = overview(sa, ea), overview(sb, eb)
    dec = contribution_breakdown(dimension, period_a, period_b)
    ranks = rank_dimension(dimension, "revenue", sb, eb, top_n=6)

    # 异常：全年日粒度序列上检测，再筛出对比期
    anomalies = [a.as_dict() for a in detect(dimension, freq="day", min_orders=30,
                                            shift_pct=25.0)
                 if a.period.startswith(period_b)]

    rows = query("SELECT COUNT(*) AS c FROM orders")[0]["c"]

    # 【授权数字清单】所有会出现在报告里的数字都必须在这里 —— 派生值也不例外。
    # 只把"报告确实会打印"的数字放进来，清单越精确，对账越有意义。
    result = {
        "period_a": period_a, "period_b": period_b, "dimension": dimension,
        "rows": rows, "ov_a": ov_a, "ov_b": ov_b,
        "mom": {
            "revenue_pct": _mom(ov_a["revenue"], ov_b["revenue"]),
            "orders_pct": _mom(ov_a["orders"], ov_b["orders"]),
            "aov_pct": _mom(ov_a["aov"], ov_b["aov"]),
        },
        "decomposition": dec, "ranks": ranks["top"], "anomalies": anomalies,
        "anomaly_count": len(anomalies),
        "rank_count": len(ranks["top"]),
        "breakdown_count": len(dec["breakdown"]),
        "losers_count": len([r for r in dec["breakdown"] if (r["delta"] or 0) < 0]),
    }
    return result


def _mom(a, b) -> float | None:
    """环比百分比 =（本期-上期）/上期×100。分母为 0 或缺失时返回 None（不编造 0）。"""
    if a in (None, 0) or b is None:
        return None
    return (float(b) - float(a)) / float(a) * 100


# ------------------------------------------------------------------ 组装视图
def build_view(analysis: dict) -> dict:
    """把分析结果转成"全是字符串"的视图，供模板直接渲染。

    关键：所有格式化（千分位、百分比、符号）都发生在 Python 里，
    模板拿到的已经是成品字符串 —— 模板无法"顺手算错"。
    """
    ov_a, ov_b, dec = analysis["ov_a"], analysis["ov_b"], analysis["decomposition"]
    dim = analysis["dimension"]
    dim_label = {"platform": "平台", "channel": "渠道", "product": "商品"}[dim]

    conclusions = []
    rev_pct = analysis["mom"]["revenue_pct"]
    conclusions.append(
        f"实付额 {_money(ov_b['revenue'])} 元，环比 {_signed_pct(rev_pct)}"
        f"（{analysis['period_a']} → {analysis['period_b']}）"
    )
    vol, price = dec["total_volume_effect"], dec["total_price_effect"]
    driver = "订单量" if abs(vol) >= abs(price) else "客单价"
    conclusions.append(
        f"增长/变化主要由**{driver}**驱动：量效应 {_money(vol)} 元、"
        f"价效应 {_money(price)} 元、交互项 {_money(dec['total_interaction'])} 元"
    )
    if dec["breakdown"]:
        top = dec["breakdown"][0]
        conclusions.append(
            f"贡献最大的{dim_label}是「{top['dim_value']}」：变化 {_money(top['delta'])} 元，"
            f"占总变化 {top['delta_share_pct']}%"
        )
        losers = [r for r in dec["breakdown"] if (r["delta"] or 0) < 0]
        if losers:
            conclusions.append(
                f"拖累项 {analysis['losers_count']} 个，最大为「{losers[0]['dim_value']}」"
                f"（{_money(losers[0]['delta'])} 元）"
            )
    conclusions.append(
        f"客单价 {_money(ov_b['aov'])} 元/单（环比 {_signed_pct(analysis['mom']['aov_pct'])}），"
        f"折扣率 {_pct_from_rate(ov_b['discount_rate'])}、退款金额率 {_pct_from_rate(ov_b['refund_rate'])}"
    )
    conclusions.append(
        f"{analysis['period_b']} 检出 {analysis['anomaly_count']} 个日粒度异常"
        "（判据见第四节；已做周内效应校正与最小支持度过滤）"
    )

    return {
        "title": f"{analysis['period_b']} 经营分析报告",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "data_source": "MySQL orders（真实电商订单）",
        "rows": _int(analysis["rows"]),
        "kpi_version": KPI_VERSION,
        "period_a": analysis["period_a"], "period_b": analysis["period_b"],
        "dimension_label": {"platform": "平台", "channel": "渠道", "product": "商品"}[dim],
        "top_n": len(dec["breakdown"]),
        "ov_a": {k: (_pct_from_rate(v) if "rate" in k else (_money(v) if k != "orders" else _int(v)))
                 for k, v in ov_a.items()},
        "ov_b": {k: (_pct_from_rate(v) if "rate" in k else (_money(v) if k != "orders" else _int(v)))
                 for k, v in ov_b.items()},
        "mom": {
            "revenue_pct": _signed_pct(analysis["mom"]["revenue_pct"]),
            "orders_pct": _signed_pct(analysis["mom"]["orders_pct"]),
            "aov_pct": _signed_pct(analysis["mom"]["aov_pct"]),
        },
        "dec": {
            "total_delta": _money(dec["total_delta"]),
            "total_volume_effect": _money(dec["total_volume_effect"]),
            "total_price_effect": _money(dec["total_price_effect"]),
            "total_interaction": _money(dec["total_interaction"]),
            "total_decompose_check": f"{dec['total_decompose_check']:.2f}",
            "breakdown": [{
                "dim_value": r["dim_value"], "revenue_a": _money(r["revenue_a"]),
                "revenue_b": _money(r["revenue_b"]), "delta": _money(r["delta"]),
                "delta_share_pct": _signed_pct(r["delta_share_pct"]),
                "volume_effect": _money(r["volume_effect"]),
                "price_effect": _money(r["price_effect"]),
            } for r in dec["breakdown"][:6]],
        },
        "ranks": [{
            "dim_value": r["dim_value"], "value": _money(r["value"]),
            "share_pct": f"{r['share_pct']:.2f}%", "orders_cnt": _int(r["orders_cnt"]),
        } for r in analysis["ranks"]],
        "anomalies": [{
            "dim_value": a["dim_value"], "period": a["period"], "method": a["method"],
            "deviation_pct": f"{a['deviation_pct']:+.2f}%",
            "robust_z": ("—" if a["robust_z"] is None else f"{a['robust_z']:.2f}"),
            "orders": _int(a["orders"]),
        } for a in analysis["anomalies"]],
        "z_threshold": "3.5",
        "min_orders": "30",
        "conclusions": conclusions,
    }


def render(view: dict) -> str:
    """用 Jinja2 渲染报告模板（模板里只有占位符，没有任何计算）。"""
    template = Template(TEMPLATE_PATH.read_text(encoding="utf-8"))
    return template.render(**view)


# ------------------------------------------------------------------ 对账 ①：文本级
DATE_RE = re.compile(r"\d{4}-\d{2}(-\d{2})?")
TIME_RE = re.compile(r"\d{1,2}:\d{2}(:\d{2})?")
NUM_RE = re.compile(r"(?<![\w.])(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)(%?)")


def _walk_numbers(obj) -> list[float]:
    """递归收集任意嵌套结构里的所有数字 —— 构成"授权数字清单"。"""
    out: list[float] = []
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            out.extend(_walk_numbers(v))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out.extend(_walk_numbers(v))
    return out


def _variants(v: float) -> tuple[set[str], set[str]]:
    """返回 (普通形式集合, 百分数形式集合)。覆盖各种四舍五入与 万/亿 表示。"""
    plain, pct = set(), set()
    for nd in (0, 1, 2, 3):
        plain.add(f"{v:.{nd}f}")
        pct.add(f"{v * 100:.{nd}f}")
    plain.add(f"{v / 1e4:.2f}")   # 万
    plain.add(f"{v / 1e8:.2f}")   # 亿
    plain.add(f"{abs(v):.{2}f}")
    pct.add(f"{abs(v) * 100:.2f}")
    return plain, pct


def verify_numbers(text: str, source: dict) -> dict:
    """把报告中每个数字抽出来，逐个核对"是否来自分析结果"。"""
    plain, pct = set(), set()
    for v in _walk_numbers(source):
        p, q = _variants(v)
        plain |= p
        pct |= q

    cleaned = TIME_RE.sub(" ", DATE_RE.sub(" ", text))
    violations, checked = [], 0
    for token, is_pct in NUM_RE.findall(cleaned):
        normalized = token.replace(",", "")
        checked += 1
        if normalized in CONSTANT_ALLOWED:
            continue
        # 百分数 token 同时接受两种口径：源里存小数（×100）或源里已存百分数（原值）
        pool = (plain | pct) if is_pct else plain
        if normalized in pool:
            continue
        violations.append({"token": token + is_pct, "type": "百分比" if is_pct else "数值"})
    return {
        "checked": checked,
        "violations": violations,
        "pass_rate": round(1 - len(violations) / checked, 4) if checked else 1.0,
        "passed": not violations,
    }


# ------------------------------------------------------------------ 对账 ②：数据库级复算
def selftest_verifier(report: str, analysis: dict) -> dict:
    """【对账器自测】故意在报告里插一个假数字，确认对账器真的能抓到它。

    为什么必须做：报告"0 违规"本身是不可信的 —— 万一是对账器太宽松（或坏了）呢？
    只有先证明"注入假数字会被抓"，"0 违规"才有意义。这就是 test the test。
    """
    fake_value = "999,999.99"
    tampered = report.replace("## 一、经营概览",
                              f"## 一、经营概览\n\n（自测注入假数字：{fake_value} 元）", 1)
    result = verify_numbers(tampered, analysis)
    caught = [v for v in result["violations"] if fake_value.replace(",", "") in v["token"].replace(",", "")]
    return {"injected_value": fake_value, "caught": bool(caught),
            "violations": result["violations"],
            "conclusion": "对账器有效（能抓到注入的假数字）" if caught else "对账器失效！"}


def reconcile_db(analysis: dict) -> dict:
    """把头部指标重新从 MySQL 算一遍，与报告用的值比对（比文本对账更强）。"""
    pa, pb = analysis["period_a"], analysis["period_b"]
    checks = []
    for label, period, stored in (("period_a", pa, analysis["ov_a"]),
                                  ("period_b", pb, analysis["ov_b"])):
        s, e = _month_range(period)
        fresh = {
            "revenue": query_metrics("revenue", s, e)["value"],
            "orders": query_metrics("orders", s, e)["value"],
            "aov": query_metrics("aov", s, e)["value"],
            "refund_rate": query_metrics("refund_rate", s, e)["value"],
            "discount_rate": query_metrics("discount_rate", s, e)["value"],
        }
        for key, val in fresh.items():
            old = stored.get(key)
            diff = abs(float(old) - float(val)) if old is not None and val is not None else None
            checks.append({"period": period, "metric": key, "report_value": old,
                           "recomputed": val, "abs_diff": diff,
                           "ok": diff is not None and diff < 1e-6})

    dec = analysis["decomposition"]
    sum_ok = abs(dec["total_volume_effect"] + dec["total_price_effect"]
                 + dec["total_interaction"] - dec["total_delta"]) < 1e-6
    checks.append({"period": f"{pa}→{pb}", "metric": "量价分解精确加总",
                   "report_value": dec["total_delta"],
                   "recomputed": dec["total_volume_effect"] + dec["total_price_effect"]
                   + dec["total_interaction"], "abs_diff": dec["total_decompose_check"],
                   "ok": sum_ok})
    return {"all_ok": all(c["ok"] for c in checks), "checks": checks}


# ------------------------------------------------------------------ 可选：LLM 写结论并对账
def llm_narrative(view: dict) -> str:
    """（可选）让 LLM 写「结论与建议」。

    只把它**已算好**的结构化结果喂进去，并要求"不得引入结果里没有的数字"；
    返回的文本随后仍要过一遍 `verify_numbers` —— 模型的话不能免检。
    """
    from agent_lab.p1_react import call_llm

    facts = {k: view[k] for k in ("period_a", "period_b", "ov_a", "ov_b", "mom", "dec")}
    content, pt, ct = call_llm([
        {"role": "system", "content":
            "你是经营分析师。只根据给定的**已算好**的数据写 3 条结论。"
            "**禁止引入任何数据里没有的数字**；可以引用数据里的数字，也可以完全不写数字。"
            "输出 3 行，每行以 '- ' 开头，不要标题。"},
        {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
    ])
    print(f"   [LLM 结论] tokens {pt}+{ct}")
    return content


def main() -> int:
    """命令行入口：收集 → 渲染 → 文本级对账 → 对账器自测 → 数据库级复算 →（可选）LLM 对账。

    只有全部通过才打印"报告可发布"；任一失败即返回非 0，**不允许带着未对账的数字发布**。
    """
    ap = argparse.ArgumentParser(description="经营分析报告生成 + 数字对账")
    ap.add_argument("--period-a", default="2025-10")
    ap.add_argument("--period-b", default="2025-11")
    ap.add_argument("--dimension", default="platform", choices=list(DIMENSIONS))
    ap.add_argument("--with-llm", action="store_true", help="额外让 LLM 写结论并对其文本对账")
    args = ap.parse_args()

    print(f"=== 收集分析结果（{args.period_a} → {args.period_b}，维度 {args.dimension}）===")
    analysis = collect(args.period_a, args.period_b, args.dimension)
    view = build_view(analysis)
    report = render(view)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / f"经营分析报告_{args.period_b}.md"
    report_path.write_text(report, encoding="utf-8")

    print("\n=== 对账 ① 文本级：报告里每个数字是否都来自分析结果 ===")
    v1 = verify_numbers(report, analysis)
    print(f"  抽取数字 {v1['checked']} 个，违规 {len(v1['violations'])} 个，"
          f"通过率 {v1['pass_rate']:.2%} {'✅' if v1['passed'] else '❌'}")
    for bad in v1["violations"][:10]:
        print(f"    违规：{bad}")

    print("\n=== 对账 ①-b 对账器自测：注入假数字必须被抓到 ===")
    st = selftest_verifier(report, analysis)
    print(f"  注入 {st['injected_value']} → {'✅ 已抓到' if st['caught'] else '❌ 漏抓！'} "
          f"（{st['conclusion']}）")

    print("\n=== 对账 ② 数据库级：头部指标重新从 MySQL 复算 ===")
    v2 = reconcile_db(analysis)
    for c in v2["checks"]:
        flag = "✅" if c["ok"] else "❌"
        print(f"  {flag} {c['period']:<16} {c['metric']:<14} "
              f"报告={c['report_value']}  复算={c['recomputed']}  Δ={c['abs_diff']}")
    print(f"  全部一致：{'✅' if v2['all_ok'] else '❌'}")

    llm_check = None
    if args.with_llm:
        print("\n=== 对账 ③ LLM 写的结论里有没有编造数字 ===")
        text = llm_narrative(view)
        print(text)
        llm_check = verify_numbers(text, analysis)
        print(f"  LLM 文本抽取 {llm_check['checked']} 个数字，违规 "
              f"{len(llm_check['violations'])} 个 {'✅ 未编造' if llm_check['passed'] else '❌ 有编造'}")
        for bad in llm_check["violations"][:10]:
            print(f"    编造：{bad}")

    recon = {"period": f"{args.period_a}->{args.period_b}",
             "text_level": v1, "verifier_selftest": st,
             "db_level": v2, "llm_text_level": llm_check}
    (OUT_DIR / f"reconciliation_{args.period_b}.json").write_text(
        json.dumps(recon, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"\n✅ 报告：{report_path}")
    print(f"✅ 对账：{OUT_DIR / f'reconciliation_{args.period_b}.json'}")
    publishable = (v1["passed"] and v2["all_ok"] and st["caught"]
                   and (llm_check is None or llm_check["passed"]))
    print(f"\n{'✅ 报告可发布' if publishable else '❌ 对账未通过，禁止发布'}")
    return 0 if publishable else 1


if __name__ == "__main__":
    raise SystemExit(main())
