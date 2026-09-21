"""agent_lab.evaluate_anomaly —— 异常检测评测：差分口径 + 事件化后处理。

【两个方法论要点（这是本项目最值钱的部分，面试重点讲）】

1. **差分口径**：真实数据本身就有真实异常（元旦暴涨、春节下滑）。
   如果把「注入表检出的全部异常」直接和注入清单比对，真实事件会被冤枉成误报。
   所以只统计**注入表新出现、干净表没有**的异常。

2. **事件化后处理**：一个**持续性**异常天然会产生「进入点 + 内部点 + 恢复点」多个检出，
   但注入清单里它只是一个事件。用一对一匹配会把多余的正确检出算成误报
   —— 第一版就是这个坑：P 只有 35%，11 个"误报"全部能归因到注入事件本身。
   修正：先把间隔 ≤7 天的检出合并成一个事件，再做一对一匹配。

用法：
    python agent_lab/inject_anomalies.py   # 先造评测集
    python agent_lab/evaluate_anomaly.py   # 再评测
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_lab.anomaly import Anomaly, detect  # noqa: E402

EVAL_DIR = Path(__file__).with_name("eval")
GT_PATH = EVAL_DIR / "injected_anomalies.json"
CLEAN_TABLE = "orders"
INJECTED_TABLE = "orders_injected"
DIMENSION = "platform"
STRICT_TOLERANCE_DAYS = 3   # 严格口径：检出必须落在注入区间前后 3 天内
EVENT_GAP_DAYS = 7          # 事件化：间隔 ≤7 天的检出合并为一个事件
EVENT_TOLERANCE_DAYS = 7    # 事件化口径的匹配容差
BASE_MIN_ORDERS = 30


# ------------------------------------------------------------------ 匹配与打分
def key_of(a: Anomaly) -> tuple:
    """异常的唯一标识：维度+取值+期间+方法。差分比对与去重都用它。"""
    return (a.dimension, a.dim_value, a.period, a.method)


def matches(a: Anomaly, inj: dict, tol: int) -> bool:
    """检出是否落在某个注入事件的 [start-tol, end+tol] 区间内（且维度取值相同）。"""
    if a.dim_value != inj["value"]:
        return False
    d = date.fromisoformat(a.period)
    return (date.fromisoformat(inj["start"]) - timedelta(days=tol)
            <= d
            <= date.fromisoformat(inj["end"]) + timedelta(days=tol))


def _prf(tp: int, fp: int, fn: int) -> dict:
    """由 TP/FP/FN 算查准率 P、查全率 R、F1（分母为 0 时返回 0，不抛异常）。"""
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def score(detections: list[Anomaly], injections: list[dict], *,
          only_new: bool = False, clean_keys: set | None = None,
          tol: int = STRICT_TOLERANCE_DAYS) -> dict:
    """严格口径：检出级一对一匹配（一个注入只能被匹配一次）。"""
    pool = [a for a in detections if not only_new or key_of(a) not in (clean_keys or set())]
    hit_ids: set[str] = set()
    tp, fp = [], []
    for a in pool:
        target = next((inj for inj in injections
                       if inj["id"] not in hit_ids and matches(a, inj, tol)), None)
        if target:
            hit_ids.add(target["id"])
            tp.append({"anomaly": a.as_dict(), "matched": target["id"]})
        else:
            fp.append(a.as_dict())
    fn = [inj for inj in injections if inj["id"] not in hit_ids]
    return {"pool_size": len(pool), **_prf(len(tp), len(fp), len(fn)),
            "true_positives": tp, "false_positives": fp,
            "missed": [{"id": i["id"], "desc": i["desc"]} for i in fn]}


def merge_events(detections: list[Anomaly], gap_days: int = EVENT_GAP_DAYS) -> list[dict]:
    """【事件化后处理】把同维度值、间隔 ≤ gap_days 的检出合并成一个事件。"""
    by_value: dict[str, list[Anomaly]] = {}
    for a in detections:
        by_value.setdefault(a.dim_value, []).append(a)

    events: list[dict] = []
    for value, items in by_value.items():
        items.sort(key=lambda x: x.period)
        cur: list[Anomaly] = []
        for a in items:
            if cur and (date.fromisoformat(a.period)
                        - date.fromisoformat(cur[-1].period)).days > gap_days:
                events.append(_pack_event(value, cur))
                cur = []
            cur.append(a)
        if cur:
            events.append(_pack_event(value, cur))
    return events


def _pack_event(value: str, items: list[Anomaly]) -> dict:
    """把一组相邻检出打包成一个"事件"（记录起止、方法集合、检出点数、最大偏离）。"""
    deviations = [a.deviation_pct for a in items if a.deviation_pct is not None]
    return {
        "dim_value": value,
        "start": items[0].period,
        "end": items[-1].period,
        "methods": sorted({a.method for a in items}),
        "n_detections": len(items),
        "max_deviation_pct": max(deviations, key=abs) if deviations else None,
    }


def score_events(events: list[dict], injections: list[dict],
                 tol: int = EVENT_TOLERANCE_DAYS) -> dict:
    """事件级一对一匹配：一个注入事件最多被一个检出事件匹配。"""
    hit_ids: set[str] = set()
    tp, fp = [], []
    for ev in events:
        s, e = date.fromisoformat(ev["start"]), date.fromisoformat(ev["end"])
        target = next((inj for inj in injections
                       if inj["id"] not in hit_ids
                       and inj["value"] == ev["dim_value"]
                       and s - timedelta(days=tol) <= date.fromisoformat(inj["end"])
                       and e + timedelta(days=tol) >= date.fromisoformat(inj["start"])), None)
        if target:
            hit_ids.add(target["id"])
            tp.append({"event": ev, "matched": target["id"]})
        else:
            fp.append(ev)
    fn = [inj for inj in injections if inj["id"] not in hit_ids]
    return {"n_events": len(events), **_prf(len(tp), len(fp), len(fn)),
            "true_positives": tp, "false_positives": fp,
            "missed": [{"id": i["id"], "desc": i["desc"]} for i in fn]}


def attribute_fp(a: Anomaly | None, injections: list[dict]) -> str:
    """把一个误报挂到「最近的注入事件」并给出距离 —— 判断它是边界/恢复点还是真乱报。"""
    if a is None:
        return "?"
    d = date.fromisoformat(a.period)
    best, best_dist = None, None
    for inj in injections:
        if inj["value"] != a.dim_value:
            continue
        s, e = date.fromisoformat(inj["start"]), date.fromisoformat(inj["end"])
        dist = 0 if s <= d <= e else min(abs((d - s).days), abs((d - e).days))
        if best_dist is None or dist < best_dist:
            best, best_dist = inj["id"], dist
    return f"{best}(±{best_dist}天)" if best else "无关联"


# ------------------------------------------------------------------ 运行与报告
def run_once(min_orders: int) -> dict:
    """干净表与注入表各跑一次检测，并算出"差分池"（注入表新出现、干净表没有的异常）。"""
    clean = detect(DIMENSION, table=CLEAN_TABLE, freq="day", min_orders=min_orders)
    injected = detect(DIMENSION, table=INJECTED_TABLE, freq="day", min_orders=min_orders)
    clean_keys = {key_of(a) for a in clean}
    pool = [a for a in injected if key_of(a) not in clean_keys]
    return {"clean": clean, "injected": injected, "clean_keys": clean_keys, "pool": pool}


def main() -> int:
    """跑完整评测：差分指标 + 事件化指标 + 误报归因 + 门槛扫描，并落盘报告。"""
    if not GT_PATH.exists():
        raise SystemExit(f"❌ 找不到 ground truth：{GT_PATH}\n"
                         f"请先运行 python agent_lab/inject_anomalies.py")
    injections = json.loads(GT_PATH.read_text(encoding="utf-8"))["injections"]

    print(f"=== 默认参数（min_orders={BASE_MIN_ORDERS}）===")
    base = run_once(BASE_MIN_ORDERS)
    strict = score(base["injected"], injections, only_new=True, clean_keys=base["clean_keys"])
    ev = score_events(merge_events(base["pool"]), injections)
    raw = score(base["injected"], injections, only_new=False)

    print(f"  干净表检出 {len(base['clean'])} 个（真实业务事件，如元旦三平台暴涨）")
    print(f"  注入表检出 {len(base['injected'])} 个；其中差分（新出现）{len(base['pool'])} 个")
    print(f"  【事件化后处理·主】P={ev['precision']:.2%} R={ev['recall']:.2%} F1={ev['f1']:.2%} "
          f"(TP={ev['tp']} FP={ev['fp']} FN={ev['fn']}, 事件数={ev['n_events']})")
    print(f"  【严格一对一·对照】P={strict['precision']:.2%} R={strict['recall']:.2%} "
          f"F1={strict['f1']:.2%} (TP={strict['tp']} FP={strict['fp']} FN={strict['fn']})")
    print(f"  【原始口径·偏悲观】P={raw['precision']:.2%} R={raw['recall']:.2%} F1={raw['f1']:.2%}")

    pool_index = {(a.dim_value, a.period, a.method): a for a in base["pool"]}
    fp_attr = [{**fp, "attribution": attribute_fp(
        pool_index.get((fp["dim_value"], fp["period"], fp["method"])), injections)}
        for fp in strict["false_positives"]]
    unrelated = [x for x in fp_attr if x["attribution"].startswith("无关联")]
    print(f"  差分误报中「与所有注入都无关」的：{len(unrelated)} 个 "
          f"{'→ 没有凭空乱报 ✅' if not unrelated else '→ 需人工核查'}")

    print("\n=== 逐条注入的检出情况 ===")
    detected = {tp["matched"] for tp in ev["true_positives"]}
    for inj in injections:
        print(f"  {'✅ 检出' if inj['id'] in detected else '❌ 漏检'}  {inj['id']}  {inj['desc']}")

    print("\n=== 门槛取舍实验（min_orders 扫描）===")
    sweep = []
    for m in (10, 30, 50, 80):
        r = run_once(m)
        d = score(r["injected"], injections, only_new=True, clean_keys=r["clean_keys"])
        e = score_events(merge_events(r["pool"]), injections)
        sweep.append({"min_orders": m, "clean_detected": len(r["clean"]), **d,
                      "event_precision": e["precision"], "event_recall": e["recall"],
                      "event_f1": e["f1"]})
        print(f"  min_orders={m:>3}  干净表检出 {len(r['clean']):>3}  |  "
              f"严格 P={d['precision']:.2%} R={d['recall']:.2%}  |  "
              f"事件化 P={e['precision']:.2%} R={e['recall']:.2%} F1={e['f1']:.2%}")

    report = {
        "params": {"dimension": DIMENSION, "freq": "day", "min_orders": BASE_MIN_ORDERS,
                   "strict_tolerance_days": STRICT_TOLERANCE_DAYS,
                   "event_gap_days": EVENT_GAP_DAYS,
                   "event_tolerance_days": EVENT_TOLERANCE_DAYS,
                   "clean_table": CLEAN_TABLE, "injected_table": INJECTED_TABLE},
        "clean_table_detections": len(base["clean"]),
        "event_level": ev, "strict": strict, "raw": raw,
        "false_positive_attribution": fp_attr, "unrelated_fp": len(unrelated),
        "threshold_sweep": sweep, "injections": injections,
    }
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    (EVAL_DIR / "anomaly_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (EVAL_DIR / "anomaly_report.md").write_text(render_md(report), encoding="utf-8")
    print(f"\n✅ 报告已写入 {EVAL_DIR / 'anomaly_report.md'}")
    return 0


def render_md(report: dict) -> str:
    """把评测结果渲染成 Markdown：方法 / 结果 / 逐条检出 / 门槛扫描 / 误报归因 / 局限。"""
    ev, strict, raw, sweep = (report["event_level"], report["strict"],
                              report["raw"], report["threshold_sweep"])
    n_inj = len(report["injections"])
    lines = [
        "# 异常检测评测报告（注入集 · 差分口径 + 事件化后处理）",
        "",
        "## 方法",
        "",
        "- **数据**：仓库自带 102,287 行真实订单。注入写进副本表 `orders_injected`，`orders` 保留为干净基线",
        f"- **干净基线检出**：{report['clean_table_detections']} 个真实业务事件（如元旦三平台同时暴涨 +143%/+187%/+192%）"
        "—— 它们是「噪声地板」，不该被算成误报",
        "- **差分口径**：只统计「注入表新出现、干净表没有」的异常",
        "- **事件化后处理**：把同维度值、间隔 ≤ 7 天的检出合并为一个「事件」"
        "（持续性异常天然产生「进入+内部+恢复」多个检出点）",
        "",
        "## 结果",
        "",
        "| 口径 | 查准率 P | 查全率 R | F1 | TP | FP | FN |",
        "|---|---|---|---|---|---|---|",
        f"| **事件化后处理（主指标）** | **{ev['precision']:.2%}** | **{ev['recall']:.2%}** | "
        f"**{ev['f1']:.2%}** | {ev['tp']} | {ev['fp']} | {ev['fn']} |",
        f"| 严格一对一（对照） | {strict['precision']:.2%} | {strict['recall']:.2%} | "
        f"{strict['f1']:.2%} | {strict['tp']} | {strict['fp']} | {strict['fn']} |",
        f"| 原始口径（含真实事件，偏悲观） | {raw['precision']:.2%} | {raw['recall']:.2%} | "
        f"{raw['f1']:.2%} | {raw['tp']} | {raw['fp']} | {raw['fn']} |",
        "",
        f"- **事件级召回：{n_inj - len(ev['missed'])}/{n_inj}** 个注入事件被检出",
        f"- **差分误报中「与所有注入都无关」的：{report['unrelated_fp']} 个**（0 = 没有凭空乱报）",
        f"- 差分检出合并为 **{ev['n_events']}** 个检出事件（原始差分检出点更多，见误报归因）",
        "",
        "## 逐条注入检出情况",
        "",
        "| 注入 ID | 类型 | 描述 | 是否检出 |",
        "|---|---|---|---|",
    ]
    detected = {tp["matched"] for tp in ev["true_positives"]}
    for inj in report["injections"]:
        lines.append(f"| {inj['id']} | {inj['type']} | {inj['desc']} "
                     f"| {'✅' if inj['id'] in detected else '❌ 漏检'} |")

    lines += ["", "## 门槛取舍实验（min_orders 扫描）", "",
              "| min_orders | 干净表检出 | 严格 P | 严格 R | 事件化 P | 事件化 R | 事件化 F1 |",
              "|---|---|---|---|---|---|---|"]
    for s in sweep:
        lines.append(f"| {s['min_orders']} | {s['clean_detected']} | {s['precision']:.2%} "
                     f"| {s['recall']:.2%} | {s['event_precision']:.2%} "
                     f"| {s['event_recall']:.2%} | {s['event_f1']:.2%} |")
    lines += ["",
              "> min_orders 从 10 提到 50 指标不变，这是**差分口径的特性**：低样本量切片"
              "（如 web网站，日单量约 20）在干净表和注入表里会同时被检出，差分时相互抵消。",
              "> 提到 80 才开始漏检（日均单量不足 80 的平台被整体挡掉）—— 门槛过高会伤查全率。",
              ""]

    if report["false_positive_attribution"]:
        lines += ["## 差分误报归因（每个误报离最近的注入事件多远）", "",
                  "| 维度值 | 日期 | 方法 | 偏离% | 归因 |", "|---|---|---|---|---|"]
        for fp in report["false_positive_attribution"][:25]:
            lines.append(f"| {fp['dim_value']} | {fp['period']} | {fp['method']} "
                         f"| {fp['deviation_pct']} | {fp['attribution']} |")
        lines += ["",
                  "> 归因说明这些「误报」全部落在注入事件区间内或前后几天：它们是同一个业务事件的",
                  "> 「进入点 / 恢复点 / 窗口跨界点」，属于多次检出而非凭空乱报。",
                  "> 真正的改进方向是**后处理合并 + 边界抑制**，而不是放宽容差硬凑指标。",
                  ""]

    lines += ["## 已知局限（面试主动说，别等被问）", "",
              "1. 注入异常是**人工构造**的，幅度偏理想化；真实异常更隐蔽",
              "2. 只评了 platform 维度（其他维度日样本量达不到最小支持度门槛）",
              "3. 事件匹配容差 ±7 天偏宽会高估查全率，因此同时给出严格口径对照",
              "4. 真实数据只有 1 年，做不了同比基线，只能用周内效应 + 滚动中位数",
              "5. **未做多重比较校正（FDR）** —— 维度组合一多，误报率必然上升",
              ""]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
