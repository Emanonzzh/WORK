"""量价三因子分解 `decompose` 的单测。

===============================================================================
为什么这个函数最值得单测：
`ΔR = 量效应 + 价效应 + 交互项` 是一个**精确恒等式**（不是近似），
所以它可以被断言到浮点精度 —— 测试不需要"期望值"，只需要断言闭合。
这也是报告里 `total_decompose_check` 必须为 0 的依据。

顺带记一条工程教训：这个函数原先是 `contribution_breakdown` 里的**嵌套函数**。
嵌套函数在 Python 里**无法被 import**，也就无法被单测。
"为了可测而把纯计算提到模块级"是最常见的重构理由之一。
===============================================================================
"""
from __future__ import annotations

import random

import pytest

from agent_lab.tools import decompose


def _sum3(rev0, n0, rev1, n1):
    return sum(decompose(rev0, n0, rev1, n1))


# ---------------------------------------------------------------- 语义正确性
def test_pure_volume_growth_has_no_price_effect():
    """量涨、客单价不变 → 全部是量效应，价效应与交互项必须为 0。"""
    vol, pri, inter = decompose(rev0=1000.0, n0=100, rev1=1200.0, n1=120)
    assert vol == pytest.approx(200.0)
    assert pri == pytest.approx(0.0)
    assert inter == pytest.approx(0.0)


def test_pure_price_growth_has_no_volume_effect():
    """价涨、订单数不变 → 全部是价效应。"""
    vol, pri, inter = decompose(rev0=1000.0, n0=100, rev1=1100.0, n1=100)
    assert vol == pytest.approx(0.0)
    assert pri == pytest.approx(100.0)
    assert inter == pytest.approx(0.0)


def test_both_grow_produces_positive_interaction():
    """量价同涨 → 交互项为正且必须被计入。

    漏掉交互项是这类分解最常见的实现错误：加了量效应和价效应就以为等于 ΔR，
    实际上还差 (N1-N0)(AOV1-AOV0)。这条测试专门钉住这一点。
    """
    vol, pri, inter = decompose(rev0=1000.0, n0=100, rev1=1320.0, n1=120)
    assert vol == pytest.approx(200.0)     # (120-100) * 10
    assert pri == pytest.approx(100.0)     # 100 * (11-10)
    assert inter == pytest.approx(20.0)    # (120-100) * (11-10)
    assert vol + pri + inter == pytest.approx(320.0) == pytest.approx(1320.0 - 1000.0)


def test_revenue_drop_is_decomposed_too():
    """下降方向同样成立（不能只测增长，符号错误最爱藏在下滑场景里）。"""
    vol, pri, inter = decompose(rev0=2000.0, n0=200, rev1=1350.0, n1=150)
    # aov0=10, aov1=9
    assert vol == pytest.approx(-500.0)    # (150-200) * 10
    assert pri == pytest.approx(-200.0)    # 200 * (9-10)
    assert inter == pytest.approx(50.0)    # (-50) * (-1)
    assert vol + pri + inter == pytest.approx(-650.0) == pytest.approx(1350.0 - 2000.0)


# ---------------------------------------------------------------- 恒等式（核心不变量）
# 【关于容差：为什么用"相对误差"而不是"绝对误差"】
# 这条恒等式在实数上是精确的，但在 float 上只能保证**相对**精度：
# 分解里有 aov = rev / n 这一步，量效应/价效应/交互项会出现大数相消
# （最坏情况：n0=9999 且 n1=1 时，price_effect 与 interaction 都在 1e10 量级上相消）。
# 实测 3000 组随机输入：最坏**相对**误差 2.7e-10（≈机器精度 1e-16 的合理放大），
# 但最坏**绝对**误差可达 1.0e-5 —— 绝对容差在这里不是一个稳定的判据。
# 所以：随机性质测试用 rel=1e-9，并另设一条测试显式记录这个精度边界。
_IDENTITY_REL_TOL = 1e-9


@pytest.mark.parametrize("seed", range(60))
def test_identity_holds_for_arbitrary_inputs(seed):
    """恒等式在随机输入下成立（相对精度），含 n=0 的退化输入。"""
    rnd = random.Random(seed)
    n0 = rnd.choice([0, 1, 7, 100, 9999])
    n1 = rnd.choice([0, 1, 7, 100, 9999])
    rev0 = 0.0 if n0 == 0 else round(rnd.uniform(0, 5_000_000), 2)
    rev1 = 0.0 if n1 == 0 else round(rnd.uniform(0, 5_000_000), 2)
    total = _sum3(rev0, n0, rev1, n1)
    assert total == pytest.approx(rev1 - rev0, rel=_IDENTITY_REL_TOL, abs=0.0)


@pytest.mark.parametrize("seed", range(60))
def test_identity_is_tight_on_realistic_magnitudes(seed):
    """在**贴近真实数据**的量级上，恒等式紧到绝对误差 < 1e-6。

    真实数据：订单数 5e2~2e5，客单价 50~500 元。这个范围内 aov 相消不严重，
    所以生产代码里 `round(vol + pri + inter - delta, 8) == 0` 这条断言才站得住。
    """
    rnd = random.Random(1000 + seed)
    n0 = rnd.randint(50, 200_000)
    n1 = rnd.randint(50, 200_000)
    aov0 = rnd.uniform(50, 500)
    aov1 = rnd.uniform(50, 500)
    rev0, rev1 = n0 * aov0, n1 * aov1
    assert _sum3(rev0, n0, rev1, n1) == pytest.approx(rev1 - rev0, abs=1e-6)


def test_identity_precision_boundary_is_documented():
    """显式记录精度边界：极端量级下绝对误差可以超过 1e-8，但相对误差仍是机器精度级。

    这条测试的作用不是"证明代码对"，而是把**已知边界写下来**：
    以后若有人把生产断言从 `round(..., 8)` 改成"严格等于 0"，
    会在真实数据上莫名其妙地挂掉；或者误以为分解公式有错。
    真实数据落在 test_identity_is_tight_on_realistic_magnitudes 的范围内。
    """
    rev0, n0, rev1, n1 = 4_407_589.25, 9999, 4_445_743.12, 1
    vol, pri, inter = decompose(rev0, n0, rev1, n1)
    err = abs(vol + pri + inter - (rev1 - rev0))
    assert err > 1e-8                                  # 绝对误差确实超出 round(...,8) 的判定
    assert err / (rev1 - rev0) < 1e-8                  # 但相对误差仍极小


def test_identity_requires_zero_revenue_when_no_orders():
    """【显式写下隐含前提】恒等式的前提是"n=0 ⟺ rev=0"。

    这不是 bug，而是"分解"这个数学对象的定义域：没有订单却有实付额，
    在业务上不可能发生。真实调用方 `contribution_breakdown` 对缺失维度取
    `{"rev": 0.0, "n": 0}`，所以前提始终满足。

    把前提写成测试而不是留在脑子里，是为了让以后传了不自洽数据的人
    一眼看懂"为什么会闭合失败"，而不是去怀疑公式。
    """
    # 违反前提：n0=0（无订单）却给了 rev0=500
    assert _sum3(rev0=500.0, n0=0, rev1=1320.0, n1=120) == pytest.approx(1320.0)
    assert _sum3(rev0=500.0, n0=0, rev1=1320.0, n1=120) != pytest.approx(820.0)

    # 满足前提：n0=0 且 rev0=0，恒等式恢复
    assert _sum3(rev0=0.0, n0=0, rev1=1320.0, n1=120) == pytest.approx(1320.0)


def test_zero_orders_in_both_periods_is_all_zero():
    """两期都没有订单 → 三个效应全为 0，且不抛 ZeroDivisionError。"""
    vol, pri, inter = decompose(rev0=0.0, n0=0, rev1=0.0, n1=0)
    assert (vol, pri, inter) == (0.0, 0.0, 0.0)


def test_returns_plain_floats_not_decimal():
    """返回值必须是 float：要 JSON 序列化后进报告和 API 响应。"""
    vol, pri, inter = decompose(rev0=1000.0, n0=100, rev1=1320.0, n1=120)
    assert all(isinstance(x, float) for x in (vol, pri, inter))
