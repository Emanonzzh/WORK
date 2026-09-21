"""报告数字对账器 `verify_numbers` 的单测（防幻觉机制的核心）。

===============================================================================
这套测试在守什么：
报告里的每一个数字都必须能追溯到"分析结果"（唯一数据源）。
对账器一旦太宽松，报告就会带着编造的数字发出去；太严格，则会天天误报、
最后被人为绕过。所以两侧都要有测试：**该抓的必抓，不该抓的别抓**。

历史 bug（已修，本文件留作回归测试）：
`verify_numbers` 早期版本在抽取数字时用的正则前瞻遇到千分位逗号会断掉，
`-512,555.14` 只被抽出 `555.14` —— 于是真实数字被判成"违规"，
而对账器看起来像在正常工作（这才是最危险的：**错的检查器比没有检查器更糟**）。
===============================================================================
"""
from __future__ import annotations

import pytest

from agent_lab.report import _month_range, selftest_verifier, verify_numbers


@pytest.fixture()
def source() -> dict:
    """一份典型分析结果：整数、负数、比例（0~1）、已是百分数的值都有。"""
    return {
        "total_delta": -512555.14,
        "total_mom_pct": 0.1592,          # 比例口径：0.1592 → 15.92%
        "delta_share_pct": 55.9,          # 已是百分数口径：55.9 → 55.90%
        "orders": 102287,
        "total_revenue": 2020700.0,
        "nested": {"breakdown": [{"volume_effect": 2020700.0}, {"price_effect": -512600.0}]},
    }


# ---------------------------------------------------------------- 该放行的
@pytest.mark.parametrize(
    "text",
    [
        "实付额环比 +15.92%",
        "变化 -512,555.14 元",          # 千分位（回归：历史 bug 就在这里）
        "变化 -512555.14 元",           # 无千分位
        "订单 102,287 单",
        "订单 102287 单",
        "占比 55.90%",                  # 已是百分数口径
        "占比 55.9%",
        "总额 2,020,700.00 元",
    ],
)
def test_authorized_numbers_pass(text, source):
    result = verify_numbers(text, source)
    assert result["violations"] == []
    assert result["passed"] is True


def test_thousands_separator_is_stripped_before_comparison(source):
    """千分位版本与无千分位版本必须被判为**同一个数**。"""
    with_comma = verify_numbers("变化 -512,555.14 元", source)
    without_comma = verify_numbers("变化 -512555.14 元", source)
    assert with_comma["violations"] == without_comma["violations"] == []


def test_nested_source_values_are_authorized(source):
    """嵌套结构里的数字也在授权清单里（_walk_numbers 要递归到底）。"""
    assert verify_numbers("量效应 2,020,700.00 元", source)["passed"] is True
    assert verify_numbers("价效应 -512,600.00 元", source)["passed"] is True


def test_whitelisted_judgement_constants_do_not_trip_the_checker(source):
    """判据参数（z=3.5、最小订单 30）是白名单常量，不是数据，不该被误判。"""
    assert verify_numbers("稳健 z 阈值 3.5，最小支持度 30 单", source)["passed"] is True


def test_dates_and_times_are_not_treated_as_numbers(source):
    """日期/时间先从文本里剔除，否则 '2025-11' 会被当成数字 2025 与 11。"""
    result = verify_numbers("期间 2025-11-01 至 2025-11-30，生成时间 10:30", source)
    assert result["passed"] is True


# ---------------------------------------------------------------- 该抓住的（假数字）
@pytest.mark.parametrize(
    "text,fake",
    [
        ("实付额 777,888.99 元", "777,888.99"),
        ("环比 +99.99%", "99.99%"),
        ("订单 999999 单", "999999"),
        ("下降 -123,456.78 元", "-123,456.78"),
    ],
)
def test_fabricated_numbers_are_flagged(text, fake, source):
    """对账器的核心职责：把报告里编造的数字抓出来。"""
    result = verify_numbers(text, source)
    assert result["passed"] is False
    assert result["violations"], "编造的数字必须产生违规记录"
    assert any(fake.replace(",", "") in v["token"].replace(",", "")
               for v in result["violations"])


def test_violation_records_carry_token_and_type(source):
    """违规记录要能定位：给出原始 token 和类型（数值/百分比）。"""
    result = verify_numbers("编造 777,888.99 元 和 88.88%", source)
    tokens = {v["token"] for v in result["violations"]}
    types = {v["type"] for v in result["violations"]}
    assert "777,888.99" in tokens
    assert "88.88%" in tokens
    assert types <= {"数值", "百分比"}


def test_pass_rate_and_checked_count(source):
    """pass_rate / checked 是给报告头部用的统计量，口径要稳定。"""
    ok = verify_numbers("实付额 -512,555.14 元", source)
    assert ok["checked"] == 1
    assert ok["pass_rate"] == 1.0

    bad = verify_numbers("编造 777,888.99 元", source)
    assert bad["checked"] == 1
    assert bad["pass_rate"] == 0.0


def test_empty_text_is_vacuously_valid(source):
    """空文本没有数字可查 → 0 违规，且 pass_rate 不能除以 0。"""
    result = verify_numbers("", source)
    assert result["checked"] == 0
    assert result["violations"] == []
    assert result["pass_rate"] == 1.0
    assert result["passed"] is True


# ---------------------------------------------------------------- test the test
def test_selftest_catches_its_own_injected_fake_number(source):
    """对账器自测：故意注入假数字，必须被抓到。

    这条是整套防幻觉机制的**地基**：如果对账器坏了（或太宽松），
    那么"报告 0 违规"就毫无意义。先证明它能抓假，"0 违规"才可信。
    """
    report = "## 一、经营概览\n\n实付额 -512,555.14 元\n"
    result = selftest_verifier(report, source)
    assert result["caught"] is True
    assert "有效" in result["conclusion"]


def test_selftest_reports_failure_if_anchor_missing(source):
    """自测的注入锚点不存在时，必须诚实报告"没抓到"，而不是假装成功。"""
    result = selftest_verifier("（报告里没有那个章节标题）", source)
    assert result["caught"] is False
    assert "失效" in result["conclusion"]


# ---------------------------------------------------------------- _month_range
@pytest.mark.parametrize(
    "ym,expected",
    [
        ("2025-11", ("2025-11-01", "2025-11-30")),
        ("2025-02", ("2025-02-01", "2025-02-28")),
        ("2024-02", ("2024-02-01", "2024-02-29")),   # 闰年
        ("2025-01", ("2025-01-01", "2025-01-31")),
        ("2025-12", ("2025-12-01", "2025-12-31")),
        ("2025-04", ("2025-04-01", "2025-04-30")),
    ],
)
def test_month_range_end_is_last_real_day(ym, expected):
    assert _month_range(ym) == expected
