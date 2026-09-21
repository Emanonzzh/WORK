"""agent_lab.inject_anomalies —— 注入已知异常，构造带 ground truth 的评测集。

【为什么必须注入】
真实数据里只有"真实波动"，**没有唯一正确答案**：元旦暴涨到底算不算"该被检出"？
没有标注就算不出查准/查全。注入（fault injection，企业里也叫故障注入/混沌工程）
让我们能算出精确的 P/R/F1。

【两个关键设计】
1. **不改原表**：复制出 `orders_injected`，`orders` 保留为干净基线。
2. **差分评测**（见 evaluate_anomaly.py）：只把"注入表上新出现的异常"计入统计。
   这样真实业务事件（元旦、春节）不会被冤枉成误报 —— 这是方法上的严谨，不是作弊。

用法：python agent_lab/inject_anomalies.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_lab.db import execute, query  # noqa: E402

INJECTED_TABLE = "orders_injected"
GT_PATH = Path(__file__).with_name("eval") / "injected_anomalies.json"

# 【注入清单】只挑"样本量足够"的切片：日粒度检测设了 min_orders=30 门槛，
# 平台日单量 APP≈150 / 微信公众号≈130 / web网站≈20 / 淘宝≈5，
# 所以只对前两个平台注入，否则注入会因门槛被过滤掉（那评测的是门槛而不是检测器）。
INJECTIONS: list[dict] = [
    {
        "id": "INJ-01", "type": "level_shift_down", "dimension": "platform", "value": "APP",
        "start": "2025-09-01", "end": "2025-09-14", "factor": 0.40,
        "desc": "APP 平台 9 月上半月销售额被压到 40%（持续型下滑）",
    },
    {
        "id": "INJ-02", "type": "spike_up", "dimension": "platform", "value": "APP",
        "start": "2025-06-18", "end": "2025-06-18", "factor": 3.50,
        "desc": "APP 平台 6/18 单日销售额 3.5 倍（脉冲型暴涨，如刷单/大促异常）",
    },
    {
        "id": "INJ-03", "type": "level_shift_up", "dimension": "platform", "value": "微信公众号",
        "start": "2025-07-01", "end": "2025-07-14", "factor": 1.80,
        "desc": "微信公众号 7 月上半月销售额 1.8 倍（持续型异常增长）",
    },
    {
        "id": "INJ-04", "type": "spike_down", "dimension": "platform", "value": "微信公众号",
        "start": "2025-10-15", "end": "2025-10-15", "factor": 0.25,
        "desc": "微信公众号 10/15 单日销售额跌到 25%（脉冲型塌陷，如接口故障）",
    },
    {
        "id": "INJ-05", "type": "level_shift_down", "dimension": "platform", "value": "APP",
        "start": "2025-03-01", "end": "2025-03-21", "factor": 0.50,
        "desc": "APP 平台 3 月前三周销售额腰斩（月度级下滑）",
    },
    {
        "id": "INJ-06", "type": "level_shift_up", "dimension": "platform", "value": "APP",
        "start": "2025-11-10", "end": "2025-11-23", "factor": 1.60,
        "desc": "APP 平台 11 月双周销售额 1.6 倍（大促型增长）",
    },
]


def build_injected_table() -> int:
    """复制 orders → orders_injected，再按清单注入。返回注入行数合计。"""
    print(f"① 复制 orders → {INJECTED_TABLE} ...")
    execute(f"DROP TABLE IF EXISTS {INJECTED_TABLE}")
    execute(f"CREATE TABLE {INJECTED_TABLE} LIKE orders")
    execute(f"INSERT INTO {INJECTED_TABLE} SELECT * FROM orders")

    total = 0
    for inj in INJECTIONS:
        col = {"platform": "platform_type", "channel": "channel_id", "product": "product_id"}[inj["dimension"]]
        # 三个金额字段同比例缩放，保持行内一致性（折扣率不变、客单价随之下移）
        rows = execute(
            f"""
            UPDATE {INJECTED_TABLE}
            SET payment_amount  = payment_amount  * %s,
                order_amount    = order_amount    * %s,
                discount_amount = discount_amount * %s
            WHERE {col} = %s AND order_date BETWEEN %s AND %s
            """,
            (inj["factor"], inj["factor"], inj["factor"], inj["value"], inj["start"], inj["end"]),
        )
        total += rows
        print(f"   {inj['id']} {inj['dimension']}={inj['value']} "
              f"{inj['start']}~{inj['end']} ×{inj['factor']} → 影响 {rows} 行")
    return total


def sanity_check() -> None:
    """注入后必须自检：整体金额变化应与注入量级吻合，别悄悄改坏了数据。"""
    row = query(f"""
        SELECT (SELECT ROUND(SUM(payment_amount),2) FROM orders)          AS clean_total,
               (SELECT ROUND(SUM(payment_amount),2) FROM {INJECTED_TABLE}) AS injected_total,
               (SELECT COUNT(*) FROM orders)                               AS clean_rows,
               (SELECT COUNT(*) FROM {INJECTED_TABLE})                     AS injected_rows
    """)[0]
    delta = row["injected_total"] - row["clean_total"]
    print(f"\n③ 自检：干净 {row['clean_total']:,.2f} → 注入 {row['injected_total']:,.2f} "
          f"(Δ {delta:,.2f})")
    print(f"   行数 {row['clean_rows']} → {row['injected_rows']}"
          f"  {'✅ 一致' if row['clean_rows'] == row['injected_rows'] else '❌ 行数不一致！'}")


def write_ground_truth() -> None:
    """把注入清单落盘为 ground truth（评测脚本读它来算 P/R/F1）。"""
    GT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": "在真实 orders 上注入的已知异常。评测时用差分法：只统计注入表上新出现的异常。",
        "table": INJECTED_TABLE,
        "baseline_table": "orders",
        "injections": INJECTIONS,
    }
    GT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n④ ground truth 已写入 {GT_PATH}")


if __name__ == "__main__":
    rows = build_injected_table()
    sanity_check()
    write_ground_truth()
    print(f"\n✅ 注入完成：{len(INJECTIONS)} 个已知异常，合计影响 {rows} 行")
