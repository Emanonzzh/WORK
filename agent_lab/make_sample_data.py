"""agent_lab.make_sample_data —— 造一份与真实订单**同 schema、同分布形状**的样例数据。

【为什么要有这个文件】
本项目的分析结论建立在 102,287 行真实订单上，那份数据由上游开源项目
（`ai-commerce-intelligence-platform`，README 声明 MIT）提供，**不随本仓库分发**。
没有数据，别人 clone 下来就跑不起来。这个脚本生成一份替代数据，
让"建库 → 导入 → 查指标 → 量价分解 → 出报告"整条链路可以在任何机器上跑通。

【它不能用来做什么 —— 这条最重要】
样例数据是随机数，**不能用来复算 README / 报告里出现的任何一个具体数字**
（102,287 行、+15.92%、量效应 2,020,698.05 等）。它只证明"流程能跑"，不证明"结论成立"。
把样例数据跑出来的数当结论，是这个仓库最容易踩的诚信坑。

【与真实数据的对应关系】
列名、列序、编码（UTF-8 带 BOM）、分隔符都与真实 CSV 一致，
所以 `sql/02_import_data.sample.sql` 能沿用上游的字段映射直接 LOAD DATA。
分布参数（平台占比、退款金额率、折扣率）取自真实数据的量级，不是精确值。
样例行的 order_id 一律以 `SAMP-` 开头，便于和真实数据一眼区分。

用法：
    python agent_lab/make_sample_data.py --rows 1000 --out sample_orders.csv
    python agent_lab/make_sample_data.py --rows 5000 --seed 7 --out /tmp/big.csv
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

# 与真实 cleaned_orders.csv 完全一致的中文表头与列序
HEADER = [
    "订单顺序编号", "订单号", "用户名", "商品编号", "订单金额", "付款金额",
    "渠道编号", "平台类型", "下单时间", "付款时间", "是否退款", "优惠金额",
    "支付耗时_秒", "下单日期", "下单小时", "星期几",
]

# 平台占比按真实数据的量级设定（微信公众号与 APP 合计约 93%）
PLATFORMS: list[tuple[str, float]] = [
    ("微信公众号", 46.0),
    ("APP", 45.0),
    ("web网站", 6.0),
    ("淘宝", 2.0),
    ("微信小商店", 0.7),
    ("wap网站", 0.3),
]
CHANNELS = ["渠道1", "渠道2", "渠道3"]
WEEKDAY_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

YEAR_START = date(2025, 1, 1)
YEAR_DAYS = 365


def _pick_platform(rng: random.Random) -> str:
    """按 PLATFORMS 的权重抽一个平台（微信公众号与 APP 占大头，长尾平台偶尔出现）。"""
    return rng.choices([p for p, _ in PLATFORMS], weights=[w for _, w in PLATFORMS], k=1)[0]


def _amount(rng: random.Random) -> float:
    """客单价量级：真实数据约 900~1000 元/单，长尾。用对数正态近似。"""
    return round(rng.lognormvariate(mu=6.6, sigma=0.75), 2)


def _order_time(rng: random.Random) -> datetime:
    """在 2025 年内随机取一个下单时刻；时段按电商作息加权（9 点前稀疏）。"""
    day = YEAR_START + timedelta(days=rng.randrange(YEAR_DAYS))
    # 下单集中在 9~23 点，与真实电商作息一致
    hour = rng.choices(range(24), weights=[1 if h < 9 else 3 for h in range(24)])[0]
    return datetime.combine(day, time()).replace(
        hour=hour, minute=rng.randrange(60), second=rng.randrange(60)
    )


def build_rows(n: int, seed: int) -> list[dict[str, object]]:
    """生成 n 行样例订单。同一个 seed 必然得到同一份数据，便于复现与对账。

    勾稽关系与真实数据一致：`付款金额 = 订单金额 - 优惠金额`，
    所以折扣率 = SUM(优惠)/SUM(订单金额)、退款金额率都能由这些列直接算出。
    """
    rng = random.Random(seed)
    rows: list[dict[str, object]] = []
    for i in range(1, n + 1):
        order_amount = _amount(rng)
        # 折扣率真实量级约 5%~6%：约四成订单带优惠，其余无优惠
        if rng.random() < 0.40:
            discount = round(order_amount * rng.uniform(0.02, 0.25), 2)
        else:
            discount = 0.0
        payment_amount = round(order_amount - discount, 2)
        refund = "是" if rng.random() < 0.13 else "否"  # 退款订单占比（金额率真实约 13%）
        ot = _order_time(rng)
        pay_seconds = rng.randint(8, 420)
        pt = ot + timedelta(seconds=pay_seconds)
        rows.append({
            "订单顺序编号": i,
            "订单号": f"SAMP-{i:08d}",
            "用户名": f"u{rng.randrange(10**9, 10**10 - 1)}",
            "商品编号": f"PR{rng.randrange(1, 600):06d}",
            "订单金额": f"{order_amount:.2f}",
            "付款金额": f"{payment_amount:.2f}",
            "渠道编号": rng.choice(CHANNELS),
            "平台类型": _pick_platform(rng),
            "下单时间": ot.strftime("%Y-%m-%d %H:%M:%S"),
            "付款时间": pt.strftime("%Y-%m-%d %H:%M:%S"),
            "是否退款": refund,
            "优惠金额": f"{discount:.2f}",
            "支付耗时_秒": pay_seconds,
            "下单日期": ot.strftime("%Y-%m-%d"),
            "下单小时": ot.hour,
            "星期几": WEEKDAY_EN[ot.weekday()],
        })
    return rows


def write_csv(rows: list[dict[str, object]], out: Path) -> None:
    """utf-8-sig：真实 CSV 带 BOM，LOAD DATA 的 IGNORE 1 LINES 依赖这一点。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)


def main() -> int:
    """命令行入口：生成样例 CSV，并打印客单价/退款率/折扣率供人自查分布量级。"""
    ap = argparse.ArgumentParser(description="生成与真实订单同 schema 的样例数据（随机数，不可用于复算结论）")
    ap.add_argument("--rows", type=int, default=1000, help="行数，默认 1000")
    ap.add_argument("--seed", type=int, default=2025, help="随机种子，默认 2025（同种子同输出）")
    ap.add_argument("--out", type=Path, default=Path("sample_orders.csv"), help="输出 CSV 路径")
    args = ap.parse_args()

    if args.rows < 1:
        print("rows must be >= 1")
        return 2

    rows = build_rows(args.rows, args.seed)
    write_csv(rows, args.out)

    paid = sum(float(r["付款金额"]) for r in rows)  # type: ignore[arg-type]
    refund_amt = sum(float(r["付款金额"]) for r in rows if r["是否退款"] == "是")  # type: ignore[arg-type]
    disc = sum(float(r["优惠金额"]) for r in rows)  # type: ignore[arg-type]
    order_amt = sum(float(r["订单金额"]) for r in rows)  # type: ignore[arg-type]
    print(f"written rows={len(rows)} file={args.out}")
    print(f"  payment_sum={paid:.2f}  aov={paid / len(rows):.2f}")
    print(f"  refund_amount_rate={refund_amt / paid:.4f}  discount_rate={disc / order_amt:.4f}")
    print("  NOTE: sample data is random -- do NOT use it to reproduce any documented figure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
