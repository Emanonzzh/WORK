"""agent_lab.db —— 统一数据库连接（从仓库根 .env 读凭据）。

为什么单独抽一层：连接参数散落各处时，改一次端口/密码就要改多处（DRY 原则）。
实际引用本模块的六个：`tools` / `anomaly` / `inject_anomalies` / `report` / `api` / `streamlit_app`。
（`evaluate_anomaly` 不直接连库，它走 `anomaly.detect`。）
注：`tools` 原先自带一份 `pymysql.connect`，是在读码过程中被发现并收进本层——
"抽了统一层但最大的取数模块还在层外"是这类重构最常见的失效方式。
"""
from __future__ import annotations

import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(str(PROJECT_ROOT / ".env"))


def connect(database: str | None = None) -> pymysql.connections.Connection:
    """建一个连接。默认连 .env 里的 DB_NAME，可传 database 覆盖。"""
    return pymysql.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=database or os.getenv("DB_NAME"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def query(sql: str, args: tuple = ()) -> list[dict]:
    """执行**只读**查询，返回 dict 列表（DictCursor）。

    这是分析路径的唯一取数入口，调用方只应传 SELECT。
    参数走占位符（%s），不做字符串拼接 —— 防 SQL 注入的第一层。
    """
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.close()


def execute(sql: str, args: tuple = ()) -> int:
    """执行写操作，返回影响行数。

    ⚠️ 只给注入脚本（inject_anomalies）用；**生产分析路径必须只读**。
    单独开这个口子而不是让分析代码也能写库，是为了让"谁能写"一眼可查。
    """
    conn = connect()
    try:
        with conn.cursor() as cur:
            rows = cur.execute(sql, args)
        conn.commit()
        return rows
    finally:
        conn.close()


def table_exists(name: str) -> bool:
    """表是否存在于当前库（供注入脚本做前置检查）。"""
    rows = query(
        "SELECT COUNT(*) AS c FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = %s",
        (name,),
    )
    return bool(rows and rows[0]["c"])
