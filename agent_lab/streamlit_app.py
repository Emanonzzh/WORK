"""agent_lab.streamlit_app —— 销售经营分析看板（Streamlit + Plotly）。

================================================================================
【Streamlit 是什么 / 为什么用它做这个看板】
Streamlit 把"Python 脚本"直接变成网页：你写 `st.metric(...)` 就是一个指标卡，
写 `st.dataframe(df)` 就是一张表，不用写任何 HTML/JS。脚本**每次交互都会从头重跑**，
所以"重活"必须缓存 —— 这是我们这里用 `@st.cache_data` 的原因。

【本看板的五个页签，对应项目的五块能力】
  1. 量价归因   → 瀑布图（量效应/价效应/交互项，三项精确加总）
  2. 维度下钻   → 贡献度表 + 条形图（谁在拉、谁在拖）
  3. 异常发现   → 日粒度异常列表（含判据：基线/偏离/robust_z/样本量）
  4. 报告与对账 → 生成 Markdown 报告 + 下载 + 两道对账结果
  5. 自然语言问数 → 调手写 ReAct Agent（会花钱，需点按钮触发）

【运行】
    streamlit run agent_lab/streamlit_app.py --server.port 8502
需要 MySQL（orders 表 102,287 行）；低配机/无 MySQL 时跑不起来，只读代码即可。
================================================================================
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from agent_lab import report as report_mod  # noqa: E402
from agent_lab.anomaly import detect  # noqa: E402
from agent_lab.attribution import drill_down  # noqa: E402
from agent_lab.db import query  # noqa: E402
from agent_lab.tools import monthly_trend  # noqa: E402

DIMENSION_LABEL = {"platform": "平台", "channel": "渠道", "product": "商品"}
UP_COLOR, DOWN_COLOR, BASE_COLOR = "#2ca02c", "#d62728", "#4c78a8"

# set_page_config 必须是第一个 st 调用（否则 Streamlit 会报错）
st.set_page_config(page_title="销售经营分析", page_icon="📊", layout="wide")


# ------------------------------------------------------------------ 取数（带缓存）
@st.cache_data(ttl=600, show_spinner=False)
def available_months() -> list[str]:
    """数据里有哪些月份（用于下拉框）。缓存 10 分钟。"""
    rows = query("SELECT DISTINCT DATE_FORMAT(order_date, '%%Y-%%m') AS ym "
                 "FROM orders ORDER BY ym")
    return [r["ym"] for r in rows]


@st.cache_data(ttl=600, show_spinner="正在计算分析结果…")
def load_analysis(period_a: str, period_b: str, dimension: str) -> dict:
    """把重活（十几次 SQL + 异常检测）包进缓存：同参数第二次打开是瞬时的。

    这是 Streamlit 最重要的实践：**不缓存的话，每次点一下页面都会重跑全部查询**。
    """
    return report_mod.collect(period_a, period_b, dimension)


@st.cache_data(ttl=600, show_spinner="正在下钻…")
def load_drill(period_a: str, period_b: str, dimension: str, sub_dim: str) -> dict:
    """两层下钻：先按 `dimension` 找最大分支，再进它内部按 `sub_dim` 拆。

    每层保留 5 个分支用于展示；真正往下钻的是各层 |变化| 最大的那一个。
    """
    return drill_down(period_a, period_b, (dimension, sub_dim), top_n=5)


@st.cache_data(ttl=600, show_spinner=False)
def load_sparkline(metric: str) -> list[float]:
    """指标卡上的迷你趋势线数据。"""
    return [s["value"] for s in monthly_trend(metric, 12)["series"]]


def prev_month(period: str) -> str:
    y, m = int(period[:4]), int(period[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


# ------------------------------------------------------------------ 侧边栏筛选
months = available_months()
with st.sidebar:
    st.title("📊 销售经营分析")
    st.caption("数据：102,287 行真实电商订单（MySQL）")
    # 只允许选"有基期可比"的月份：数据里最早那个月没有更早的月份，选了就没法做环比
    # （UI 冒烟测试发现的缺陷：原来允许选 2025-01，结果页面直接进错误态、0 个指标卡）
    comparable = months[1:] if len(months) > 1 else months
    period_b = st.selectbox("分析月份", comparable, index=len(comparable) - 1,
                            help="只列出有基期可比的月份（最早一个月没有更早的月份可对比）")
    period_a = st.selectbox(
        "对比基期", months, index=max(0, months.index(period_b) - 1),
        help="默认取上一个月，可手动改成任意月份做对比",
    )
    dimension = st.selectbox("下钻维度", list(DIMENSION_LABEL),
                             format_func=lambda k: DIMENSION_LABEL[k])
    st.divider()
    st.caption("口径：实付额 = SUM(payment_amount)；一单一品，订单数 = 行数")

if period_a == period_b:
    st.error(f"基期与对比期相同（都是 {period_a}），无法算环比。"
             "请在左侧把「对比基期」选成另一个月份。")
    st.stop()   # st.stop() 会中断本次脚本运行，后面的代码不执行

analysis = load_analysis(period_a, period_b, dimension)
ov_a, ov_b, dec = analysis["ov_a"], analysis["ov_b"], analysis["decomposition"]
mom = analysis["mom"]


# ------------------------------------------------------------------ 顶部：KPI 行
st.title(f"{period_b} 经营分析")
st.caption(f"对比基期 {period_a} ｜ 维度：{DIMENSION_LABEL[dimension]} ｜ "
           f"口径版本 {report_mod.KPI_VERSION}")

# st.container(horizontal=True) 里的多个 metric 会自动横向排列并在窄屏换行
with st.container(horizontal=True):
    st.metric("实付额（元）", f"{ov_b['revenue']:,.0f}", f"{mom['revenue_pct']:+.2f}%",
              border=True, chart_data=load_sparkline("revenue"), chart_type="line")
    st.metric("订单数（单）", f"{ov_b['orders']:,.0f}", f"{mom['orders_pct']:+.2f}%",
              border=True, chart_data=load_sparkline("orders"), chart_type="bar")
    st.metric("客单价（元/单）", f"{ov_b['aov']:,.2f}", f"{mom['aov_pct']:+.2f}%",
              border=True, chart_data=load_sparkline("aov"), chart_type="line")
    st.metric("退款金额率", f"{ov_b['refund_rate'] * 100:.2f}%", border=True)
    st.metric("折扣率", f"{ov_b['discount_rate'] * 100:.2f}%", border=True)

tab_attr, tab_dim, tab_anom, tab_report, tab_ask = st.tabs(
    ["📉 量价归因", "🔍 维度下钻", "⚠️ 异常发现", "📄 报告与对账", "💬 自然语言问数"]
)


# ------------------------------------------------------------------ 页签 1：量价归因（瀑布图）
with tab_attr:
    st.subheader("量价结构分解")
    st.caption("ΔR = 量效应 + 价效应 + 交互项，三项**精确加总**回总变化"
               "（这是判断走势「靠量还是靠价」的核心方法）")

    labels = [f"{period_a} 实付额", "量效应", "价效应", "交互项", f"{period_b} 实付额"]
    values = [ov_a["revenue"], dec["total_volume_effect"], dec["total_price_effect"],
              dec["total_interaction"], ov_b["revenue"]]
    fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=["absolute", "relative", "relative", "relative", "total"],
        x=labels,
        y=values,
        text=[f"{v:,.0f}" for v in values],
        textposition="outside",
        connector={"line": {"color": "rgb(170,170,170)"}},
        increasing={"marker": {"color": UP_COLOR}},
        decreasing={"marker": {"color": DOWN_COLOR}},
        totals={"marker": {"color": BASE_COLOR}},
    ))
    fig.update_layout(height=430, showlegend=False, margin=dict(t=40, b=10, l=10, r=10),
                      yaxis_title="元")
    st.plotly_chart(fig)

    check = dec["total_decompose_check"]
    ok = abs(check) < 1e-6
    st.success(f"加总校验：量效应 + 价效应 + 交互项 − 总变化 = {check:.2f} "
               f"{'✅ 精确闭合' if ok else '❌ 不闭合，请检查计算'}")

    with st.container(border=True):
        st.markdown("**怎么读这张图**")
        st.markdown(
            "- 量效应为正 = 靠**单量增长**拉动；价效应为负 = **客单价在拖后腿**\n"
            "- 典型「以量补价」：量效应大正、价效应负 → 增长质量要打问号\n"
            "- 交互项是量价同时变化产生的交叉影响，**不能省略**（省了就无法精确加总）"
        )


# ------------------------------------------------------------------ 页签 2：维度下钻
with tab_dim:
    st.subheader(f"按{DIMENSION_LABEL[dimension]}的贡献度")
    breakdown = dec["breakdown"]
    df = pd.DataFrame([{
        "维度值": r["dim_value"],
        f"{period_a} 实付额": r["revenue_a"],
        f"{period_b} 实付额": r["revenue_b"],
        "变化": r["delta"],
        "贡献占比%": r["delta_share_pct"],
        "量效应": r["volume_effect"],
        "价效应": r["price_effect"],
    } for r in breakdown])

    col1, col2 = st.columns([3, 2])
    with col1:
        with st.container(border=True):
            st.markdown("**贡献度明细**")
            st.dataframe(
                df, hide_index=True,
                column_config={
                    f"{period_a} 实付额": st.column_config.NumberColumn(format="%.2f"),
                    f"{period_b} 实付额": st.column_config.NumberColumn(format="%.2f"),
                    "变化": st.column_config.NumberColumn(format="%.2f"),
                    "贡献占比%": st.column_config.NumberColumn(format="%.2f%%"),
                    "量效应": st.column_config.NumberColumn(format="%.2f"),
                    "价效应": st.column_config.NumberColumn(format="%.2f"),
                },
            )
    with col2:
        with st.container(border=True):
            st.markdown("**谁在拉、谁在拖**")
            sdf = df.sort_values("变化")
            fig2 = go.Figure(go.Bar(
                x=sdf["变化"], y=sdf["维度值"], orientation="h",
                marker_color=[UP_COLOR if v >= 0 else DOWN_COLOR for v in sdf["变化"]],
                text=[f"{v:,.0f}" for v in sdf["变化"]], textposition="outside",
            ))
            fig2.update_layout(height=90 + 42 * len(sdf), showlegend=False,
                               margin=dict(t=10, b=10, l=10, r=10), xaxis_title="变化（元）")
            st.plotly_chart(fig2)

    st.caption("说明：贡献占比之和会超过 100%，因为正负贡献会相互抵消 —— 这是贡献度的正常现象。")

    st.divider()
    st.markdown("#### 再钻一层：上一层最大的分支，内部是谁在动")
    others = [d for d in DIMENSION_LABEL if d != dimension]
    # 不用 format_func：AppTest 恢复不了这个不可序列化的回调，会退化成恒等函数，
    # 于是"选项=中文标签、value=英文键"的控件在写回 widget state 时必然 ValueError。
    # 直接把中文标签当选项值用，再映射回键，行为一致且可测。
    sub_label = st.selectbox("第二层按什么拆", [DIMENSION_LABEL[d] for d in others], index=0,
                             key=f"sub_dim_for_{dimension}")
    sub_dim = next((k for k in others if DIMENSION_LABEL[k] == sub_label), others[0])
    chain = load_drill(period_a, period_b, dimension, sub_dim)["chain"]
    parent_row, level1 = chain[0]["top"][0], chain[1]
    st.caption(
        f"路径：全市场 Δ {chain[0]['subset_total_delta']:,.2f} → "
        f"{DIMENSION_LABEL[dimension]}「{parent_row['value']}」Δ {parent_row['delta']:,.2f} → "
        f"其内部按{DIMENSION_LABEL[sub_dim]}拆，共 {level1['groups']} 组"
    )
    closed = abs(level1["subset_total_delta"] - parent_row["delta"]) < 0.01
    (st.success if closed else st.error)(
        f"链条闭合校验：子层合计 Δ {level1['subset_total_delta']:,.2f} "
        f"{'==' if closed else '!='} 父层该分支 Δ {parent_row['delta']:,.2f}"
        f"　两层分解残差 {chain[0]['decompose_check']} / {level1['decompose_check']}"
    )
    sub_df = pd.DataFrame([{
        "维度值": t["value"], "变化": t["delta"], "占本层%": t["share_of_subset_pct"],
        f"{period_a} 订单": t["orders_a"], f"{period_b} 订单": t["orders_b"],
    } for t in level1["top"]])
    st.dataframe(sub_df, hide_index=True, column_config={
        "变化": st.column_config.NumberColumn(format="%.2f"),
        "占本层%": st.column_config.NumberColumn(format="%.2f%%"),
    })
    st.caption("注意：这些数字只在上一层选中的那个分支**内部**统计，是子集小计，不是全市场。")


# ------------------------------------------------------------------ 页签 3：异常发现
with tab_anom:
    st.subheader(f"{period_b} 异常发现")
    all_anom = [a.as_dict() for a in detect(dimension, freq="day", min_orders=30)
                if a.period.startswith(period_b)]
    st.caption("判据：同星期几基线 + 滚动中位数 + MAD 稳健 z 阈值 3.5 ｜ "
               "最小支持度 30 单（订单太少的切片不判定，压误报）")

    if all_anom:
        adf = pd.DataFrame([{
            "日期": a["period"], "类型": a["method"], "维度值": a["dim_value"],
            "实际值": a["value"], "基线": a["baseline"],
            "偏离%": a["deviation_pct"], "robust_z": a["robust_z"], "样本量": a["orders"],
        } for a in all_anom])
        st.dataframe(adf, hide_index=True, column_config={
            "实际值": st.column_config.NumberColumn(format="%.2f"),
            "基线": st.column_config.NumberColumn(format="%.2f"),
            "偏离%": st.column_config.NumberColumn(format="%.2f%%"),
            "robust_z": st.column_config.NumberColumn(format="%.2f"),
        })
    else:
        st.info("本期未检出**统计意义上**的异常：各日波动均落在同星期几基线的正常范围内"
                "（这是结论，不是漏检）。")
        st.caption("对比：注入了异常的评测集上，同一套检测器能全部检出（见 eval/anomaly_report.md）")


# ------------------------------------------------------------------ 页签 4：报告与对账
with tab_report:
    st.subheader("经营报告与数字对账")
    st.caption("数字全部由代码计算并写入模板；发布前做两道对账。")

    view = report_mod.build_view(analysis)
    markdown = report_mod.render(view)
    text_check = report_mod.verify_numbers(markdown, analysis)
    selftest = report_mod.selftest_verifier(markdown, analysis)
    db_check = report_mod.reconcile_db(analysis)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("文本级对账", f"{text_check['pass_rate'] * 100:.0f}%",
              f"{len(text_check['violations'])} 违规", delta_color="inverse", border=True)
    c2.metric("抽取数字", f"{text_check['checked']} 个", border=True)
    c3.metric("对账器自测", "抓到" if selftest["caught"] else "漏抓",
              f"注入 {selftest['injected_value']}", border=True)
    c4.metric("数据库级复算", "全一致" if db_check["all_ok"] else "有不一致",
              f"{len(db_check['checks'])} 项", border=True)

    publishable = text_check["passed"] and db_check["all_ok"] and selftest["caught"]
    (st.success if publishable else st.error)(
        "✅ 报告可发布" if publishable else "❌ 对账未通过，禁止发布")

    st.download_button("⬇️ 下载报告（Markdown）", markdown,
                       file_name=f"经营分析报告_{period_b}.md", mime="text/markdown")

    with st.expander("查看报告全文"):
        st.markdown(markdown)
    with st.expander("查看数据库级复算明细"):
        st.dataframe(pd.DataFrame(db_check["checks"]), hide_index=True)


# ------------------------------------------------------------------ 页签 5：自然语言问数
with tab_ask:
    st.subheader("自然语言问数（手写 ReAct Agent）")
    st.warning("这个页签**会调用大模型、产生费用**，所以不自动执行 —— 点按钮才跑。"
               "Agent 的完整机制见 `agent_lab/p1_react.py`，压测见 `p1_stress.py`。")

    question = st.text_input("问题", value=f"{period_b} 销售涨了，是不是靠单量堆的？客单价拖累了多少？")
    if st.button("🤖 让 Agent 分析", type="primary"):
        with st.spinner("Agent 正在多步推理…"):
            try:
                import agent_lab.p1_react as react
                result = react.run_agent(question, verbose=False)
            except SystemExit as exc:
                st.error(f"LLM 未配置：{exc}")
                result = None
        if result is not None:
            if result.answer:
                st.markdown(result.answer)
            else:
                st.warning("未得出结论")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("步数", len(result.steps), border=True)
            m2.metric("工具调用", result.tool_calls, border=True)
            m3.metric("tokens", result.total_prompt_tokens + result.total_completion_tokens,
                      border=True)
            m4.metric("耗时", f"{result.elapsed_ms / 1000:.1f}s", border=True)
            st.caption(f"停止原因：{result.stop_reason} ｜ "
                       f"失败模式：{result.failure_modes or '无'}")
            with st.expander("查看每步轨迹（思考 / 调用 / 观察）"):
                for s in result.steps:
                    st.markdown(f"**Step {s.index}** ｜ {s.elapsed_ms:.0f} ms ｜ "
                                f"tokens {s.prompt_tokens}+{s.completion_tokens}")
                    if s.thought:
                        st.markdown(f"- Thought: {s.thought}")
                    if s.action:
                        st.markdown(f"- Action: `{s.action}` {s.action_input_raw}")
                    if s.observation:
                        st.code(s.observation[:500], language="json")

st.divider()
st.caption(f"agent_lab · 生成于 {datetime.now():%Y-%m-%d %H:%M} ｜ "
           "口径：销售额 = SUM(payment_amount) ｜ 数据：102,287 行真实电商订单")
