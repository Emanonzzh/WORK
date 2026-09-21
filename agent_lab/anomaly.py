"""agent_lab.anomaly —— 异常检测（纯函数 + 真实/注入数据）。

【为什么要有这个文件 / 检测为什么不能"拍脑袋定阈值"】
第一版我用「3 天窗口比中位数 + 30% 阈值」跑真实日数据，检出 **175 个异常** —— 全是误报。
根因是**周内效应**：日销售额周六比周一高 40% 是常态，拿 3 天窗口比就等于拿周六比周一。
修正成两条**季节基线**后误报数才会降下来（见 `__main__` 自检）。

【五条设计原则】
1. **最小支持度门槛** `min_orders`：订单数太少的切片不参与统计判定
   （wap网站只有 3 单，任何波动都是"100% 异常"）。
2. **周内效应校正**（日粒度）：脉冲检测的基线取**同一星期几**的滚动中位数，
   不能拿周六和周一的绝对水平比。
3. **日历校正**（月粒度）：月度值先按当月**天数归一**再比较（2 月 28 天，不校正必误报）。
4. **稳健统计（MAD）**：`robust_z = residual / (1.4826 × MAD)`，而不是均值 ±3σ
   —— 均值/标准差会被异常点自己撑大。
5. **两类异常**：`spike`（脉冲，单期偏离）/ `level_shift`（水平漂移，趋势型，
   用**整周对整周**比，避免周内效应污染）。

【输出必须带判据】每个异常给出 基线 / 偏离% / robust_z / 样本量，
否则无法回答"凭什么判它是异常"。
"""
from __future__ import annotations

import calendar
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from statistics import mean, median

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_lab.db import query  # noqa: E402
from agent_lab.tools import DIMENSIONS  # noqa: E402

MAD_SCALE = 1.4826  # 正态分布下 MAD → 标准差的换算系数


@dataclass
class Anomaly:
    """一条异常记录。

    必须自带**判据**（baseline / deviation_pct / robust_z / orders），
    否则无法回答"凭什么判它是异常" —— 不可解释的异常等于没有异常。
    """

    dimension: str
    dim_value: str
    freq: str
    period: str
    metric: str
    value: float
    baseline: float
    deviation_pct: float | None
    robust_z: float | None
    method: str
    orders: int

    def as_dict(self) -> dict:
        """转 dict，便于写 JSON / 传给报告层。"""
        return asdict(self)


# ------------------------------------------------------------------ 取数
def _dim_col(dimension: str) -> str:
    """维度别名 → 真实列名（platform → platform_type）。白名单，防注入。"""
    if dimension not in DIMENSIONS:
        raise ValueError(f"未知维度 {dimension}，可用：{list(DIMENSIONS)}")
    return DIMENSIONS[dimension]


def fetch_series(dimension: str, table: str = "orders", freq: str = "day",
                 start: str | None = None, end: str | None = None,
                 values: list[str] | None = None) -> dict[str, list[dict]]:
    """取 (维度值 → 时间序列)。freq='day' 按日、'month' 按月。"""
    dim_col = _dim_col(dimension)
    period_expr = "order_date" if freq == "day" else "DATE_FORMAT(order_date, '%%Y-%%m')"
    clauses, args = [], []
    if start:
        clauses.append("order_date >= %s")
        args.append(start)
    if end:
        clauses.append("order_date <= %s")
        args.append(end)
    if values:
        clauses.append(f"{dim_col} IN (" + ",".join(["%s"] * len(values)) + ")")
        args.extend(values)
    where_sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"""
        SELECT COALESCE({dim_col}, '未知') AS dim_value,
               {period_expr} AS period,
               SUM(payment_amount) AS revenue,
               COUNT(*) AS orders
        FROM {table}
        {where_sql}
        GROUP BY dim_value, period
        ORDER BY dim_value, period
    """
    series: dict[str, list[dict]] = {}
    for r in query(sql, tuple(args)):
        series.setdefault(r["dim_value"], []).append({
            "period": str(r["period"]),
            "value": float(r["revenue"] or 0),
            "orders": int(r["orders"] or 0),
        })
    return series


def days_in_month(period: str) -> int:
    """'YYYY-MM' → 当月天数。日历校正用（2 月 28 天不能和 31 天的月直接比）。"""
    return calendar.monthrange(int(period[:4]), int(period[5:7]))[1]


# ------------------------------------------------------------------ 基线原语
def _rolling_median(values: list[float], window: int) -> list[float]:
    """居中滚动**中位数**基线（不是均值）。

    为什么用中位数：脉冲点会被算进均值、把基线拉高，从而掩盖自己；
    中位数对离群值稳健，异常点无法"抬走"自己的基线。
    """
    half = max(1, window // 2)
    return [median(values[max(0, i - half):min(len(values), i + half + 1)]) for i in range(len(values))]


def _same_weekday_baseline(periods: list[str], values: list[float], weeks: int = 3) -> list[float]:
    """【原则 2】同一星期几的滚动中位数 —— 消掉周内效应。

    周六的基线只看它前后几周的周六，而不是把工作日也算进去。
    """
    wd = [date(*map(int, p.split("-"))).weekday() for p in periods]
    out = []
    for i in range(len(values)):
        cand = [values[j] for j in range(len(values))
                if j != i and wd[j] == wd[i] and abs(j - i) <= weeks * 7]
        out.append(median(cand) if cand else values[i])
    return out


def _mad(values: list[float]) -> float:
    """MAD（绝对中位差）= median(|x − median(x)|)。

    乘 1.4826 就是稳健标准差估计。它比标准差抗离群值 ——
    这正是"异常点无法把自己的阈值抬高"的原因（原则 4）。
    """
    if not values:
        return 0.0
    med = median(values)
    return median([abs(v - med) for v in values])


# ------------------------------------------------------------------ 核心检测
def detect_in_series(
    rows: list[dict],
    *,
    freq: str = "day",
    min_orders: int = 30,
    z_threshold: float = 3.5,
    shift_pct: float = 25.0,
    weekday_adjust: bool = True,
) -> list[dict]:
    """在单条序列上检测。返回不含 dimension/dim_value 的 dict 列表。"""
    periods = [r["period"] for r in rows]
    raw = [r["value"] for r in rows]
    orders = [r["orders"] for r in rows]

    # 基线：月粒度做日历校正，日粒度做周内效应校正
    if freq == "month":
        values = [v / days_in_month(p) for v, p in zip(raw, periods)]
        baseline = _rolling_median(values, 3)
    elif weekday_adjust:
        values = raw
        baseline = _same_weekday_baseline(periods, values, weeks=3)
    else:
        values = raw
        baseline = _rolling_median(values, 7)

    residuals = [v - b for v, b in zip(values, baseline)]
    mad = _mad(residuals)
    sigma = MAD_SCALE * mad if mad > 0 else 0.0

    found: list[dict] = []

    # ① 脉冲型：单期偏离同星期几基线
    for i, res in enumerate(residuals):
        if orders[i] < min_orders or sigma <= 0:
            continue
        z = res / sigma
        if abs(z) > z_threshold:
            found.append({
                "period": periods[i],
                "method": "spike",
                "value": round(raw[i], 2),
                "baseline": round(baseline[i], 2),
                "deviation_pct": round(res / baseline[i] * 100, 2) if baseline[i] else None,
                "robust_z": round(z, 2),
                "orders": orders[i],
            })

    # ② 水平漂移：**整周对整周**（日）或整月对整月（月），避免周内效应污染
    w = 7 if freq == "day" else 3
    if len(values) >= 2 * w + 1:
        for i in range(w, len(values) - w + 1):
            prev_win, next_win = values[i - w:i], values[i:i + w]
            if min(orders[i - w:i] + orders[i:i + w]) < min_orders:
                continue
            prev_mean = mean(prev_win)
            if prev_mean <= 0:
                continue
            change_pct = (mean(next_win) - prev_mean) / prev_mean * 100
            if abs(change_pct) < shift_pct:
                continue
            # 合并连续漂移：一段只留起点
            if found and found[-1]["method"] == "level_shift" and found[-1]["_idx"] == i - 1:
                found[-1]["_idx"] = i
                continue
            found.append({
                "period": periods[i],
                "method": "level_shift",
                "value": round(raw[i], 2),
                "baseline": round(prev_mean, 2),
                "deviation_pct": round(change_pct, 2),
                "robust_z": None,
                "orders": orders[i],
                "_idx": i,
            })

    for a in found:
        a.pop("_idx", None)
    return found


def detect(
    dimension: str,
    *,
    table: str = "orders",
    metric: str = "revenue",
    freq: str = "day",
    min_orders: int = 30,
    z_threshold: float = 3.5,
    shift_pct: float = 25.0,
    weekday_adjust: bool = True,
    values: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    top_n_values: int | None = None,
) -> list[Anomaly]:
    """在某个维度的所有（或部分）序列上跑检测，返回扁平异常列表。"""
    series = fetch_series(dimension, table=table, freq=freq, start=start, end=end, values=values)
    if top_n_values:
        ranked = sorted(series.items(), key=lambda kv: sum(r["value"] for r in kv[1]), reverse=True)
        series = dict(ranked[:top_n_values])

    out: list[Anomaly] = []
    for dim_value, rows in series.items():
        for a in detect_in_series(rows, freq=freq, min_orders=min_orders,
                                  z_threshold=z_threshold, shift_pct=shift_pct,
                                  weekday_adjust=weekday_adjust):
            out.append(Anomaly(dimension=dimension, dim_value=dim_value, freq=freq,
                               metric=metric, **a))
    out.sort(key=lambda x: (x.dim_value, x.period))
    return out


if __name__ == "__main__":
    # 自检：真实数据上跑，对比"未做周内校正"与"做了周内校正"的误报量（不花任何 LLM 费用）
    print("=== 日 · 平台维度：不做周内校正（复现第一版的 175 个误报）===")
    bad = detect("platform", freq="day", min_orders=30, weekday_adjust=False, shift_pct=30.0)
    print(f"检出 {len(bad)} 个")

    print("\n=== 日 · 平台维度：做周内校正（当前实现）===")
    good = detect("platform", freq="day", min_orders=30, weekday_adjust=True, shift_pct=25.0)
    print(f"检出 {len(good)} 个")
    for a in good[:15]:
        print(f"  {a.dim_value:<10} {a.period}  {a.method:<12} 偏离 {str(a.deviation_pct):>8}%  "
              f"z={a.robust_z}  订单 {a.orders}")

    print("\n=== 月 · 平台维度（日历校正）===")
    mon = detect("platform", freq="month", min_orders=30, shift_pct=25.0)
    print(f"检出 {len(mon)} 个")
    for a in mon[:12]:
        print(f"  {a.dim_value:<10} {a.period}  {a.method:<12} 偏离 {str(a.deviation_pct):>8}%")
