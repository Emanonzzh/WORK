"""入参校验单测：所有拦在 SQL 之前的那一层守卫。

===============================================================================
为什么这层守卫值得测：
工具是给 **LLM** 调的，LLM 会一本正经地传 `"2025-13-01"`、`"unknown_dim"`。
守卫的职责不是"最好别出错"，而是**明确报错**——错误信息越具体，
模型自我修正越可靠（这是本项目"错误回传"机制的前置条件）。

历史背景（D2 压测抓出的真实 bug）：
最初 `_check_date` 只用正则校格式，于是 `2025-13-01` 被放行，
MySQL 当无效日期返回空值，模型看到 0 行只能自己猜"13 月不存在"。
后来发现**同一类 bug 还有第二处**：day 只校验了 01~31，
于是 `2025-02-31` 依然被放行。本文件把这类边界一次性钉死。
===============================================================================
"""
from __future__ import annotations

import pytest

from agent_lab.tools import (
    DIMENSIONS,
    METRICS,
    ToolError,
    _check_date,
    _check_dimension,
    _check_metric,
    _check_month,
)


# ---------------------------------------------------------------- 月份校验
@pytest.mark.parametrize("value", ["2025-01", "2025-10", "2025-11", "2025-12"])
def test_check_month_accepts_valid_months(value):
    _check_month(value, "period_a")          # 不抛异常即通过


@pytest.mark.parametrize(
    "value",
    [
        "2025-13",      # 不存在 13 月（真实抓到的 bug）
        "2025-00",      # 不存在 0 月
        "2025-1",       # 没补零
        "2025/11",      # 分隔符错
        "202511",       # 缺分隔符
        "",             # 空串
        None,           # 缺失
        "2025-11-01",   # 传成了日期
        "abcd-ef",      # 根本不是数字
    ],
)
def test_check_month_rejects_bad_format_or_range(value):
    with pytest.raises(ToolError):
        _check_month(value, "period_a")


@pytest.mark.parametrize("value", ["2019-05", "2031-01"])
def test_check_month_rejects_year_out_of_dataset_range(value):
    """年份越界要单独报错：数据集只覆盖 2025 年，越界应提示"不在范围内"。"""
    with pytest.raises(ToolError, match="年份"):
        _check_month(value, "period_a")


# ---------------------------------------------------------------- 日期校验
@pytest.mark.parametrize(
    "value",
    ["2025-01-01", "2025-11-30", "2025-11-01", "2024-02-29", "2025-12-31"],
)
def test_check_date_accepts_valid_dates(value):
    _check_date(value, "start_date")


@pytest.mark.parametrize(
    "value",
    [
        "2025-13-01",   # 13 月
        "2025-00-10",   # 0 月
        "2025-11-00",   # 0 日
        "2025-02-31",   # 2 月只有 28 天（同一类 bug 的第二处，已补修）
        "2025-04-31",   # 4 月只有 30 天
        "2025-06-31",   # 6 月只有 30 天
        "2025-02-29",   # 2025 不是闰年
        "2025-11-32",   # 超出 31
        "2025-1-01",    # 没补零
        "20251101",     # 缺分隔符
        "",             # 空串
        None,           # 缺失
    ],
)
def test_check_date_rejects_invalid_calendar_dates(value):
    with pytest.raises(ToolError):
        _check_date(value, "start_date")


def test_check_date_accepts_leap_day_in_leap_year():
    """闰年 2 月 29 日必须放行 —— 边界收紧不能紧过头。"""
    _check_date("2024-02-29", "start_date")


def test_check_date_error_message_names_the_real_last_day():
    """报错必须说清"当月实际有几天"，否则模型无法自我修正。"""
    with pytest.raises(ToolError) as ei:
        _check_date("2025-02-31", "start_date")
    msg = str(ei.value)
    assert "28" in msg and "start_date" in msg


# ---------------------------------------------------------------- 维度 / 指标白名单
@pytest.mark.parametrize("dimension", ["platform", "channel", "product"])
def test_check_dimension_accepts_whitelisted(dimension):
    _check_dimension(dimension)


@pytest.mark.parametrize("dimension", ["user", "PLATFORM", "", None, "platform; DROP TABLE orders"])
def test_check_dimension_rejects_non_whitelisted(dimension):
    """白名单同时是**防注入**措施：不在表里就别想拼进 SQL。"""
    with pytest.raises(ToolError):
        _check_dimension(dimension)


@pytest.mark.parametrize("metric", ["revenue", "orders", "aov", "refund_rate", "discount_rate"])
def test_check_metric_accepts_whitelisted(metric):
    _check_metric(metric)


@pytest.mark.parametrize("metric", ["profit", "gross_margin", "REVENUE", "", None])
def test_check_metric_rejects_non_whitelisted(metric):
    """`profit` 被拒是**业务事实**而非疏漏：数据集没有成本字段，算不出毛利。"""
    with pytest.raises(ToolError):
        _check_metric(metric)


# ---------------------------------------------------------------- 口径表自检
def test_metrics_catalog_is_complete():
    """口径表每一条都必须回答清楚：怎么算(SQL)、叫什么(label)、什么口径(definition)、单位。"""
    assert METRICS, "口径表不能为空"
    for name, spec in METRICS.items():
        for key in ("sql", "label", "definition", "unit"):
            assert spec.get(key), f"指标 {name} 缺少 {key}"


def test_dimension_catalog_maps_to_real_columns():
    """维度白名单的每个值都必须是真实列名（防止白名单写错导致 SQL 报错）。"""
    assert set(DIMENSIONS) == {"platform", "channel", "product"}
    assert all(col for col in DIMENSIONS.values())


def test_no_cost_or_profit_metric_exists():
    """反向断言：明确"算不出毛利"这件事，避免以后有人偷偷加一个假的口径。"""
    assert "profit" not in METRICS
    assert "cost" not in METRICS
