"""agent_lab.eval_set —— 25 题评测集：题目 + **由代码算出的标准答案**。

================================================================================
【为什么要这么设计】
"循环比单轮强"这个论断必须有可信分母。分母不可信的三种写法我都避开了：
  ✗ 把答案硬编码进题目文件 —— 数据一换就悄悄过期，而且没人会发现；
  ✗ 让模型自己判分 —— 判分器本身没被验证过（同 `report.selftest_verifier` 的教训）；
  ✗ 只比"回答得像不像" —— 那是观感不是指标。
  ✓ **标准答案由现调 `tools.call_tool` 算出来**：本题文件里没有一个业务数字，
    只有"问什么"和"用哪个工具、什么参数能得到事实"。

【multi 桶在 09-25 被推翻过一次，这是重做后的版本】
上一版 10 道 multi 里有 6 道其实一次调用就能取全 —— pilot 直接打脸：`one_tool` 臂
（只许调一次工具、报错即终局）在 S01/M06 上和 `loop` 同样全对，因为
`contribution_breakdown` 一次返回就带了"下钻到最大分支"所需的全部字段。
所以这一版每题都标 `min_calls`（**已知最少几次调用**），且 multi 题的 `note`
必须写清"为什么一次拿不全"。三条硬规则：
  1. multi ⇒ min_calls >= 2（由 `tests/test_eval_harness.py` 钉住）；
  2. 凡能从 `contribution_breakdown` 的 total_* / aov_* / revenue_* 顺手拿到的，
     都不算多步 —— 这个工具返回值太肥，它是本桶最大的陷阱；
  3. 真正一次做不完的只有两类：**跨步依赖**（下一步的 filter 来自上一步的输出）
     和**跨参数组合**（两个维度 / 两个指标 / 两个区间，而工具参数全是单值）。
     新 10 题全部落在这两类里。

【判分口径】见 `eval_run.py`：
  correct    —— `required()` 里每个数都出现在回答文本里（容忍 万/亿/百分数/各种小数位），
               且没有冒出授权集之外的大数；**开放题例外**（模型合法地会查计划外的事实）。
  fabricated —— 回答里出现了授权集之外、量级 ≥ 1000 的数字。
================================================================================
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

NOV_A, NOV_B = "2025-11-01", "2025-11-30"
OCT_A, OCT_B = "2025-10-01", "2025-10-31"
DEC_A, DEC_B = "2025-12-01", "2025-12-31"
YEAR_A, YEAR_B = "2025-01-01", "2025-12-31"
BD_A, BD_B = "2025-10", "2025-11"


@dataclass
class Question:
    """一道题：题干、题型、取事实的工具调用计划、以及从事实里派生的判分函数。

    Attributes:
        calls: 取数计划 `[(工具名, 参数), ...]`。执行方（`eval_run.gather_facts`）把第 i 次
               调用结果存在 `f"{工具名}_{i}"` 键下，`required` 就按这个键取数。
        required: 从取数结果里挑出"回答必须说出来的那几个数"。**这里不许出现字面量业务数字**。
        note: 判分口径上的例外或坑；multi 题还必须在这里写清"为什么一次拿不全"。
        min_calls: **已知最少需要几次工具调用**。本文件唯一的防虚高装置 ——
               multi 题必须 >=2。上一版把"要算两步"当成"要调两次"，pilot 把它戳穿了。
        must_name: 回答里还必须出现的实体名（链式取数一步步定出来的那个平台/商品）。
               光对数字不够：`contribution_breakdown(dimension="product")` 会把**每个**商品
               当月的实付额都列出来，"答案那个数在返回值里"一次调用就能撞上，可模型压根
               没解出"是哪一行"。链式题连名字一起对，否则 `one_tool` 靠枚举蒙对
               （M02 在全量第一轮就是这么"过"的）。
    """

    id: str
    kind: str
    text: str
    calls: list[tuple[str, dict]] = field(default_factory=list)
    required: Callable[[dict], list[float]] = lambda p: []
    note: str = ""
    min_calls: int = 1
    must_name: Callable[[dict], list[str]] = lambda p: []


def _one(name: str, args: dict) -> list[tuple[str, dict]]:
    """单步题的取数计划：只调一次工具。"""
    return [(name, args)]


# 参数值写成这个哨兵，表示"取**最近一次**调用结果里第一行的维度值"
# （contribution_breakdown 取 breakdown[0].dim_value，rank_dimension 取 top[0].dim_value）。
# 有它的题才是真考循环：下一步的 filter 只能是上一步的输出，一次调用做不完。
PREV_TOP = "@prev_top_dim_value"


# ---------------------------------------------------------------- 10 道单步题
SINGLE = [
    Question("S01", "single", "2025 年 11 月的实付额是多少元？",
             _one("query_metrics", {"metric": "revenue", "start_date": NOV_A, "end_date": NOV_B}),
             lambda p: [p["query_metrics_0"]["value"]]),
    Question("S02", "single", "2025 年 10 月的订单数是多少单？",
             _one("query_metrics", {"metric": "orders", "start_date": OCT_A, "end_date": OCT_B}),
             lambda p: [p["query_metrics_0"]["value"]]),
    Question("S03", "single", "2025 年全年的客单价是多少元/单？",
             _one("query_metrics", {"metric": "aov", "start_date": YEAR_A, "end_date": YEAR_B}),
             lambda p: [p["query_metrics_0"]["value"]]),
    Question("S04", "single", "2025 年 12 月的退款金额率是多少？请按百分数说。",
             _one("query_metrics", {"metric": "refund_rate", "start_date": "2025-12-01", "end_date": "2025-12-31"}),
             lambda p: [p["query_metrics_0"]["value"]],
             note="口径返回 0~1 的小数，判分同时接受 0.13 与 13%（见 report._variants）"),
    Question("S05", "single", "2025 年 11 月的折扣率是多少？请按百分数说。",
             _one("query_metrics", {"metric": "discount_rate", "start_date": NOV_A, "end_date": NOV_B}),
             lambda p: [p["query_metrics_0"]["value"]]),
    Question("S06", "single", "全年实付额排名第一的平台是哪一个？它的实付额是多少元？",
             _one("rank_dimension", {"dimension": "platform", "metric": "revenue",
                                     "start_date": YEAR_A, "end_date": YEAR_B, "top_n": 1}),
             lambda p: [p["rank_dimension_0"]["top"][0]["value"]]),
    Question("S07", "single", "2025 年 6 月的实付额是多少元？",
             _one("query_metrics", {"metric": "revenue", "start_date": "2025-06-01", "end_date": "2025-06-30"}),
             lambda p: [p["query_metrics_0"]["value"]]),
    Question("S08", "single", "2025 年 12 月的实付额是多少元？",
             _one("query_metrics", {"metric": "revenue", "start_date": "2025-12-01", "end_date": "2025-12-31"}),
             lambda p: [p["query_metrics_0"]["value"]]),
    Question("S09", "single", "2025 年 11 月环比 10 月的实付额涨跌幅是百分之几？",
             _one("monthly_trend", {"metric": "revenue", "months": 12}),
             lambda p: [next(s["mom_pct"] for s in p["monthly_trend_0"]["series"] if s["month"] == "2025-11")],
             note="monthly_trend 已把环比算好在返回值里 —— 一步就够，所以它偏偏是单轮最不吃亏的题型"),
    Question("S10", "single", "2025 年全年实付额总计是多少元？",
             _one("query_metrics", {"metric": "revenue", "start_date": YEAR_A, "end_date": YEAR_B}),
             lambda p: [p["query_metrics_0"]["value"]]),
]

# ---------------------------------------------------------------- 10 道多步题
# 每题的 note 就是它"一次拿不全"的证明；min_calls 与 note 由 tests/test_eval_harness.py 校验。
MULTI = [
    Question("M01", "multi", "10→11 月实付额变化最大的那个平台里，变化最大的商品是哪个？它变化了多少元？",
             [("contribution_breakdown", {"dimension": "platform", "period_a": BD_A, "period_b": BD_B}),
              ("contribution_breakdown", {"dimension": "product", "period_a": BD_A, "period_b": BD_B,
                                          "filter_dimension": "platform", "filter_value": PREV_TOP})],
             lambda p: [p["contribution_breakdown_1"]["breakdown"][0]["delta"]],
             "第二层的 filter 只能是第一层的输出；四个工具里没有任何一个能一次返回两层", 2,
             lambda p: [p["contribution_breakdown_1"]["breakdown"][0]["dim_value"]]),
    Question("M02", "multi", "10→11 月实付额变化最大的平台里，变化最大的那个商品是哪个？它 11 月在**全平台**的实付额合计是多少元？",
             [("contribution_breakdown", {"dimension": "platform", "period_a": BD_A, "period_b": BD_B}),
              ("contribution_breakdown", {"dimension": "product", "period_a": BD_A, "period_b": BD_B,
                                          "filter_dimension": "platform", "filter_value": PREV_TOP}),
              ("query_metrics", {"metric": "revenue", "start_date": NOV_A, "end_date": NOV_B,
                                 "dimension": "product", "dimension_value": PREV_TOP})],
             lambda p: [p["query_metrics_2"]["value"]],
             "这题连着暴露两个坑，都记在这儿：① 题干原来写『接上题』，而**每次评测都是独立会话、"
             "没有上下文**，两臂都因'不知道指哪个商品'拒答 —— 是题目坏了不是模型不行；"
             "② 改成独立成句后又有口径歧义：标准答案取**全平台合计 1,267.80 元**，"
             "循环答的是**该平台内 0 元**（PR000492 11 月在微信公众号确实没成交），"
             "两种读法都站得住，所以题干把口径写死成'全平台合计'。"
             "③ 更要紧：PR000492 同时是全局 |Δ| 第一和公众号内 |Δ| 第一 —— 这份数据上它的"
             "'链'是**假链**，一次全局分解就撞得到同一行。**本题不得作为多步优势的证据**", 3,
             lambda p: [p["contribution_breakdown_1"]["breakdown"][0]["dim_value"]]),
    Question("M03", "multi", "11 月退款率最高的平台是哪个？它的退款率和实付额分别是多少？",
             [("rank_dimension", {"dimension": "platform", "metric": "refund_rate",
                                  "start_date": NOV_A, "end_date": NOV_B, "top_n": 1}),
              ("query_metrics", {"metric": "revenue", "start_date": NOV_A, "end_date": NOV_B,
                                 "dimension": "platform", "dimension_value": PREV_TOP})],
             lambda p: [p["rank_dimension_0"]["top"][0]["value"], p["query_metrics_1"]["value"]],
             "rank_dimension 一次只按一个指标排，query_metrics 一次只取一个指标，没有工具同时给两者。"
             "required 必须**两个数都要**：只要实付额的话，一次平台收入排名就撞得出那个数", 2,
             lambda p: [p["rank_dimension_0"]["top"][0]["dim_value"]]),
    Question("M04", "multi", "10 月、11 月、12 月这三个月，实付额第一的平台分别是哪个？三个月的第一名各是多少元？",
             [("rank_dimension", {"dimension": "platform", "metric": "revenue",
                                  "start_date": OCT_A, "end_date": OCT_B, "top_n": 1}),
              ("rank_dimension", {"dimension": "platform", "metric": "revenue",
                                  "start_date": NOV_A, "end_date": NOV_B, "top_n": 1}),
              ("rank_dimension", {"dimension": "platform", "metric": "revenue",
                                  "start_date": DEC_A, "end_date": DEC_B, "top_n": 1})],
             lambda p: [p["rank_dimension_0"]["top"][0]["value"],
                        p["rank_dimension_1"]["top"][0]["value"],
                        p["rank_dimension_2"]["top"][0]["value"]],
             "上一版这题只有两个月，pilot 里 one_tool 一次就过了 —— 因为 contribution_breakdown "
             "一次返回所有平台两个月的 revenue，它本身就是一张排名表。改成三个月才真正越过它", 3,
             lambda p: [p["rank_dimension_0"]["top"][0]["dim_value"],
                        p["rank_dimension_1"]["top"][0]["dim_value"],
                        p["rank_dimension_2"]["top"][0]["dim_value"]]),
    Question("M05", "multi", "11 月按平台排的实付额第一名，和按渠道排的第一名，各自是多少元？",
             [("rank_dimension", {"dimension": "platform", "metric": "revenue",
                                  "start_date": NOV_A, "end_date": NOV_B, "top_n": 1}),
              ("rank_dimension", {"dimension": "channel", "metric": "revenue",
                                  "start_date": NOV_A, "end_date": NOV_B, "top_n": 1})],
             lambda p: [p["rank_dimension_0"]["top"][0]["value"],
                        p["rank_dimension_1"]["top"][0]["value"]],
             "rank_dimension 的 dimension 是单值参数，一次只能拆一个维度", 2),
    Question("M06", "multi", "微信公众号平台 11 月的实付额是多少元？它的退款金额率又是多少（说成百分数）？",
             [("query_metrics", {"metric": "revenue", "start_date": NOV_A, "end_date": NOV_B,
                                 "dimension": "platform", "dimension_value": "微信公众号"}),
              ("query_metrics", {"metric": "refund_rate", "start_date": NOV_A, "end_date": NOV_B,
                                 "dimension": "platform", "dimension_value": "微信公众号"})],
             lambda p: [p["query_metrics_0"]["value"], p["query_metrics_1"]["value"]],
             "query_metrics 的 metric 是单值参数，一次只给一个指标。上一版要的是"
             "『退款率+折扣率』两个小数，probe 在五名平台的退款率排名里撞出了这两个数 —— "
             "换成『一个大数 + 一个率』才撞不出来", 2,
             lambda p: ["微信公众号"]),
    Question("M07", "multi", "10→11 月变化最大的平台，它变化了多少元？那个平台 11 月的折扣率是多少？",
             [("contribution_breakdown", {"dimension": "platform", "period_a": BD_A, "period_b": BD_B}),
              ("query_metrics", {"metric": "discount_rate", "start_date": NOV_A, "end_date": NOV_B,
                                 "dimension": "platform", "dimension_value": PREV_TOP})],
             lambda p: [p["contribution_breakdown_0"]["breakdown"][0]["delta"],
                        p["query_metrics_1"]["value"]],
             "折扣率不在 contribution_breakdown 的返回字段里，必须先定人再回去补一次取数。"
             "required 两个数都要：只问折扣率的话，一次按折扣率排名就能撞出那个数", 2,
             lambda p: [p["contribution_breakdown_0"]["breakdown"][0]["dim_value"]]),
    Question("M08", "multi", "微信公众号平台 10 月、11 月、12 月的实付额分别是多少元？",
             [("query_metrics", {"metric": "revenue", "start_date": OCT_A, "end_date": OCT_B,
                                 "dimension": "platform", "dimension_value": "微信公众号"}),
              ("query_metrics", {"metric": "revenue", "start_date": NOV_A, "end_date": NOV_B,
                                 "dimension": "platform", "dimension_value": "微信公众号"}),
              ("query_metrics", {"metric": "revenue", "start_date": DEC_A, "end_date": DEC_B,
                                 "dimension": "platform", "dimension_value": "微信公众号"})],
             lambda p: [p["query_metrics_0"]["value"], p["query_metrics_1"]["value"],
                        p["query_metrics_2"]["value"]],
             "三个大数各来自一次 query_metrics；breakdown 的窗口只有两个月，monthly_trend 又没有维度过滤。"
             "上一版这题考的是 orders 排名第一（几十个订单量级的小整数），probe 到处都能撞出同样的数 —— "
             "换成大额实付额才有判别力", 3,
             lambda p: ["微信公众号"]),
    Question("M09", "multi", "全年实付额第一的平台，它全年是多少元？11 月又是多少元、占 11 月全市场的百分之几？",
             [("rank_dimension", {"dimension": "platform", "metric": "revenue",
                                  "start_date": YEAR_A, "end_date": YEAR_B, "top_n": 1}),
              ("query_metrics", {"metric": "revenue", "start_date": NOV_A, "end_date": NOV_B,
                                 "dimension": "platform", "dimension_value": PREV_TOP}),
              ("query_metrics", {"metric": "revenue", "start_date": NOV_A, "end_date": NOV_B})],
             lambda p: [p["rank_dimension_0"]["top"][0]["value"], p["query_metrics_1"]["value"],
                        round(p["query_metrics_1"]["value"] / p["query_metrics_2"]["value"] * 100, 2)],
             "先定人、再取它 11 月的值、最后还要全市场做分母 —— 三步。上一版没把全年值列进 required，"
             "结果 11 月那次排名就同时含了实付额和占比两个数，probe 当场判它一次可答", 3,
             lambda p: [p["rank_dimension_0"]["top"][0]["dim_value"]]),
    Question("M10", "multi", "10→11 月变化最大的平台里变化最大的那个商品，它变化了多少元？11 月客单价是多少元/单？",
             [("contribution_breakdown", {"dimension": "platform", "period_a": BD_A, "period_b": BD_B}),
              ("contribution_breakdown", {"dimension": "product", "period_a": BD_A, "period_b": BD_B,
                                          "filter_dimension": "platform", "filter_value": PREV_TOP}),
              ("query_metrics", {"metric": "aov", "start_date": NOV_A, "end_date": NOV_B,
                                 "dimension": "product", "dimension_value": PREV_TOP})],
             lambda p: [p["contribution_breakdown_1"]["breakdown"][0]["delta"],
                        p["query_metrics_2"]["value"]],
             "和 M02 同一条三步链但换指标。required 带上第二层的 delta：只要客单价的话，"
             "一次全局商品客单价排名里很可能就有同一个数，probe 会判它一次可答", 3,
             lambda p: [p["contribution_breakdown_1"]["breakdown"][0]["dim_value"]]),
]

# ---------------------------------------------------------------- 5 道开放题
OPEN = [
    Question("O01", "open", "11 月比 10 月涨了。这轮增长是靠单量还是靠客单价？健康吗？",
             _one("contribution_breakdown", {"dimension": "platform", "period_a": BD_A, "period_b": BD_B}),
             lambda p: [p["contribution_breakdown_0"]["total_delta"],
                        p["contribution_breakdown_0"]["total_volume_effect"],
                        p["contribution_breakdown_0"]["total_price_effect"]],
             "判断自由，但三个数必须都在。开放题的编造率只作参考：模型合法地会去查计划外的事实", 1),
    Question("O02", "open", "11 月的退款率健康吗？要不要专门盯某个平台？",
             [("query_metrics", {"metric": "refund_rate", "start_date": NOV_A, "end_date": NOV_B}),
              ("query_metrics", {"metric": "refund_rate", "start_date": OCT_A, "end_date": OCT_B}),
              ("rank_dimension", {"dimension": "platform", "metric": "refund_rate",
                                  "start_date": NOV_A, "end_date": NOV_B, "top_n": 3})],
             lambda p: [p["query_metrics_0"]["value"], p["query_metrics_1"]["value"]],
             "要回答『要不要盯某个平台』就必须再看一次按退款率排的平台 —— 3 次调用", 3),
    Question("O03", "open", "如果只能给老板一句话总结 2025 年销售表现，你说什么？给出支撑数字。",
             [("query_metrics", {"metric": "revenue", "start_date": YEAR_A, "end_date": YEAR_B}),
              ("monthly_trend", {"metric": "revenue", "months": 12}),
              ("query_metrics", {"metric": "refund_rate", "start_date": YEAR_A, "end_date": YEAR_B})],
             lambda p: [p["query_metrics_0"]["value"]],
             "只硬要全年总额这一个锚点；其余自选，所以开放题的编造率不作硬指标（理由见模块 docstring）", 3),
    Question("O04", "open", "折扣力度变大是在换量吗？给判断和依据。",
             [("query_metrics", {"metric": "discount_rate", "start_date": NOV_A, "end_date": NOV_B}),
              ("query_metrics", {"metric": "discount_rate", "start_date": OCT_A, "end_date": OCT_B}),
              ("query_metrics", {"metric": "aov", "start_date": NOV_A, "end_date": NOV_B}),
              ("query_metrics", {"metric": "aov", "start_date": OCT_A, "end_date": OCT_B})],
             lambda p: [p["query_metrics_0"]["value"], p["query_metrics_1"]["value"]],
             "两个指标 × 两个区间 = 4 次调用，query_metrics 的参数全是单值", 4),
    Question("O05", "open", "11 月哪个平台最该被追问？为什么？",
             [("contribution_breakdown", {"dimension": "platform", "period_a": BD_A, "period_b": BD_B}),
              ("query_metrics", {"metric": "refund_rate", "start_date": NOV_A, "end_date": NOV_B,
                                 "dimension": "platform", "dimension_value": PREV_TOP})],
             lambda p: [p["contribution_breakdown_0"]["breakdown"][0]["delta"],
                        p["query_metrics_1"]["value"]],
             "选中该被追问的平台之后还得回去查它的退款率，才谈得上回答『为什么』 —— 2 次调用", 2,
             lambda p: [p["contribution_breakdown_0"]["breakdown"][0]["dim_value"]]),
]

QUESTIONS: list[Question] = SINGLE + MULTI + OPEN


def by_id(qid: str) -> Question | None:
    """按题目编号取题，找不到返回 None（CLI 拼错编号时给可读提示而不是 traceback）。"""
    return next((q for q in QUESTIONS if q.id == qid), None)


def total_min_calls() -> int:
    """25 题加起来最少要多少次工具调用 —— 一个能一眼看出题集变没变难的数。"""
    return sum(q.min_calls for q in QUESTIONS)
