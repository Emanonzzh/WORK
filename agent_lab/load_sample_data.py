"""agent_lab.load_sample_data —— 把样例 CSV 灌进 orders 表（纯 INSERT，不需要 FILE 权限）。

【为什么不用 LOAD DATA INFILE】
上游那份 102,287 行的导入走 `LOAD DATA INFILE`，它要求**全局 FILE 权限**，
而本项目的分析账号 `commerce` 只有 `USAGE ON *.*` + `ALL PRIVILEGES ON <业务库>.*`
——这是刻意做的最小权限。缺 FILE 权限时 MySQL 返回的是 **1045 Access denied**
（不是 1044，很容易误判成"密码错了"）。另一条 `LOAD DATA LOCAL INFILE` 需要服务端
`local_infile=ON`，本机实测是 OFF。
所以样例导入用 INSERT：只需要普通账号就有的 INSERT 权限，且不碰 secure_file_priv，
换一台机器、换一个云 MySQL 都能跑。真实大数据量仍然用 sql/02_import_data.sql。

用法：
    python agent_lab/make_sample_data.py --rows 1000 --out sample_orders.csv
    python agent_lab/load_sample_data.py --csv sample_orders.csv --table orders

【两个实测出来的坑】
1. `TRUNCATE TABLE` 要拿**排他元数据锁(MDL)**。如果另一个连接在同一张表上有未提交的事务
   （哪怕只是 SELECT 过），TRUNCATE 会**无限等**——MySQL 的 `lock_wait_timeout` 默认大到可以视为无限，
   现象是"脚本卡住、一行输出都没有"，不是报错。所以本脚本**自己开连接、跑完立即关**，
   不要把它 import 进一个已经持有该表事务的会话里调用。
2. 排查这类卡死时先给会话设上限：`SET SESSION lock_wait_timeout=8;`，
   让它 8 秒后报错而不是永远挂着。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_lab.db import connect  # noqa: E402

# CSV 表头是中文（与上游 cleaned_orders.csv 一致），数据库列名是英文 —— 必须显式映射。
# 顺序即 sql/01_create_table.sql 里的列序。
CSV_TO_COLUMN = {
    "订单顺序编号": "order_seq_id",
    "订单号": "order_id",
    "用户名": "user_name",
    "商品编号": "product_id",
    "订单金额": "order_amount",
    "付款金额": "payment_amount",
    "渠道编号": "channel_id",
    "平台类型": "platform_type",
    "下单时间": "order_time",
    "付款时间": "payment_time",
    "是否退款": "is_refund",
    "优惠金额": "discount_amount",
    "支付耗时_秒": "payment_duration_sec",
    "下单日期": "order_date",
    "下单小时": "order_hour",
    "星期几": "weekday",
}
COLUMNS = list(CSV_TO_COLUMN.values())
BATCH = 500


def load(csv_path: Path, table: str, truncate: bool) -> int:
    """批量插入。返回写入行数。"""
    if not csv_path.exists():
        raise SystemExit(f"找不到 CSV：{csv_path}（先跑 make_sample_data.py）")
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in CSV_TO_COLUMN if c not in header]
        if missing:
            raise SystemExit(
                f"CSV 表头缺 {len(missing)} 列：{missing}\n"
                f"表头应为 {list(CSV_TO_COLUMN)}（用 agent_lab/make_sample_data.py 生成即可）"
            )
        rows = list(reader)
    if not rows:
        raise SystemExit("CSV 是空的")

    placeholders = ", ".join(["%s"] * len(COLUMNS))
    sql = f"INSERT INTO {table} ({', '.join(COLUMNS)}) VALUES ({placeholders})"
    conn = connect()
    try:
        with conn.cursor() as cur:
            if truncate:
                cur.execute(f"TRUNCATE TABLE {table}")
            total = 0
            for i in range(0, len(rows), BATCH):
                batch = [tuple(r[c] for c in CSV_TO_COLUMN) for r in rows[i : i + BATCH]]
                cur.executemany(sql, batch)
                total += len(batch)
        conn.commit()
        return total
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="把样例 CSV 灌进指定表（只需 INSERT 权限）")
    ap.add_argument("--csv", type=Path, default=Path("sample_orders.csv"))
    ap.add_argument("--table", default="orders", help="目标表名，默认 orders")
    ap.add_argument("--no-truncate", action="store_true", help="不清空目标表，直接追加")
    args = ap.parse_args()

    n = load(args.csv, args.table, truncate=not args.no_truncate)
    print(f"inserted rows = {n} into table = {args.table}")
    print("  NOTE: sample data is random -- figures derived from it are NOT the documented results.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
