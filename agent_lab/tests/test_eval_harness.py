"""tests/test_eval_harness —— 评测集与判分器自身的测试（不连库、不调模型）。

为什么要单独测判分器：`report.selftest_verifier` 那条教训在这儿同样成立 ——
"0 违规 / 5/25 正确"这类数字本身不可信，除非先证明**打分器抓得住错**。
所以这里的重点不是"能跑"，而是每一条判分口径都有正反两个断言。
"""
from __future__ import annotations

import pytest

from agent_lab import eval_run
from agent_lab.eval_set import MULTI, OPEN, PREV_TOP, QUESTIONS, SINGLE, Question, by_id


def _fake_question(**kw) -> Question:
    """造一道不连库的题，用于单测打分器。"""
    base = dict(id="T01", kind="single", text="测试题", calls=[], required=lambda p: [1234.56])
    base.update(kw)
    return Question(**base)


def test_question_set_is_10_10_5():
    """题型配比就是文档里写的 10/10/5，总数 25。"""
    assert (len(SINGLE), len(MULTI), len(OPEN)) == (10, 10, 5)
    assert len(QUESTIONS) == 25


def test_ids_unique_and_lookup_works():
    """编号唯一，且 `by_id` 命中/未命中两条路径都对。"""
    ids = [q.id for q in QUESTIONS]
    assert len(set(ids)) == 25
    assert by_id("S01") is not None and by_id("NOPE") is None


def test_every_question_has_text_and_plan():
    """每题都有题干和取数计划 —— 空计划会让 required 拿不到数据而静默判错。"""
    for q in QUESTIONS:
        assert q.text.strip(), q.id
        assert q.calls, f"{q.id} 没有取数计划"
        assert q.kind in {"single", "multi", "open"}, q.id


def test_no_question_refers_to_a_previous_question():
    """每次评测都是**独立会话**，题干里出现"上题"就是无解 —— 白扣两臂各一分。

    全量第一轮 M02 就是这么坏的：两臂都答"不知道指哪个商品"，看着像模型不行，
    其实是题目不可独立作答。
    """
    for q in QUESTIONS:
        assert "上题" not in q.text, f"{q.id} 题干引用了上一题：{q.text}"


def test_multi_bucket_declares_at_least_two_calls_and_says_why():
    """防虚高主装置：标 multi 就必须 min_calls>=2、note 说清为什么一次拿不全、计划足够长。

    上一版是"看起来要两步"就标 multi，pilot 里 one_tool 一把过 —— 因为
    `contribution_breakdown` 一次返回就含了全部所需字段。这条测试就是那次的残留教训。
    """
    for q in MULTI:
        assert q.min_calls >= 2, f"{q.id} 标 multi 却声明 min_calls={q.min_calls}"
        assert q.note.strip(), f"{q.id} multi 却没写『为什么一次拿不全』"
        assert len(q.calls) >= q.min_calls, f"{q.id} 计划 {len(q.calls)} 步 < 声明 {q.min_calls} 次"
    assert sum(q.min_calls for q in QUESTIONS) >= 25 + 12   # 25 题、multi/open 平均≥2 次


def test_single_bucket_declares_one_call_and_no_dependency():
    """单步题：min_calls==1、计划一步、且不许出现跨步哨兵。"""
    for q in SINGLE:
        assert q.min_calls == 1, q.id
        assert len(q.calls) == 1, q.id
        assert PREV_TOP not in q.calls[0][1].values(), f"{q.id} 单步题却有跨步依赖"


def test_prev_top_only_appears_after_a_row_producing_step():
    """用 PREV_TOP 哨兵的参数，前面必须已有能产出"第一行维度值"的步骤。"""
    producers = {"contribution_breakdown", "rank_dimension"}
    for q in QUESTIONS:
        for i, (_name, args) in enumerate(q.calls):
            if PREV_TOP in args.values():
                earlier = {n for n, _ in q.calls[:i]}
                assert earlier & producers, f"{q.id} 第 {i+1} 步的哨兵没有前驱产出方"


def test_required_never_contains_literal_business_numbers():
    """判分用的数字必须从取数结果里取，不许把业务数字硬写进题目文件。

    做法：把 `required` 换成一个"取数结果里全是 0"的 dict 再调用 ——
    只要它还能吐出非 0 的数，那个数就是硬编码进来的。
    """
    zero = {f"{name}_{i}": {"value": 0.0, "breakdown": [{"delta": 0.0}], "series": [], "top": []}
            for name, _ in [(n, a) for q in QUESTIONS for n, a in q.calls]
            for i in range(3)}
    for q in QUESTIONS:
        try:
            got = q.required(zero)
        except Exception:  # noqa: BLE001
            continue   # 键不全导致取不到数，正是"依赖真实结构"的证据，放过
        assert not [v for v in got if v], f"{q.id} 的 required 里有硬编码数字：{got}"


def test_scorer_accepts_number_written_many_ways():
    """同一个数写成 1234.56 / 1,234.56 都算说到；万 的写法另见下面那条测试。"""
    facts = {"query_metrics_0": {"value": 1234.56}}
    pool = eval_run._authorized_pool(facts)
    for text in ("实付额是 1234.56 元", "实付额 1,234.56 元", "实付额约 1234.6 元"):
        assert eval_run.score(_fake_question(), text, facts, pool)["correct"], text


def test_scorer_fails_when_a_required_number_is_missing():
    """必含数只说到一半 = 判错（业务上这叫结论没取全，不是接近正确）。"""
    facts = {"query_metrics_0": {"value": 1234.56}}
    q = _fake_question(required=lambda p: [1234.56, 999999.0])
    assert eval_run.score(q, "实付额 1,234.56 元", facts, eval_run._authorized_pool(facts))["correct"] is False


def test_numberless_answer_is_not_correct():
    """一句不含任何数字的回答不能被判对 —— 否则"我不知道"会拿高分。"""
    facts = {"query_metrics_0": {"value": 1234.56}}
    assert eval_run.score(_fake_question(), "没有数据无法回答", facts, eval_run._authorized_pool(facts))["correct"] is False


def test_wrong_number_with_tools_is_not_correct_and_is_invented():
    """【判分器空转 bug 的固化回归】拿着正确授权集、却报出另一个大数：既要判错，也要算编造。

    旧实现拿"授权集"去判"说到没说到"，这种情况会被判成 correct —— 因为它比对的不是回答文本。
    """
    facts = {"query_metrics_0": {"value": 1234.56}}
    res = eval_run.score(_fake_question(), "实付额是 7,777,777.00 元", facts, eval_run._authorized_pool(facts))
    assert res["correct"] is False
    assert res["missing"] == [1234.56]
    assert res["invented"] == ["7777777.00"]


def test_open_questions_tolerate_ungrounded_numbers_but_record_them():
    """开放题：说了计划外的大数不判错，但 invented 照记、grounding_strict 必须是 False。

    理由写在这里而不是只写在实现里：开放题合法地会去查取数计划之外的事实，
    那些**真数字**会被授权集误判成编造；宁可漏判，也不给"真话当假话"的分数。
    """
    facts = {"query_metrics_0": {"value": 1234.56}}
    q = _fake_question(kind="open")
    res = eval_run.score(q, "实付额 1,234.56 元；另外全年来看约 9,999,999.00 元", facts, eval_run._authorized_pool(facts))
    assert res["correct"] is True
    assert res["invented"] == ["9999999.00"]
    assert res["grounding_strict"] is False


def test_big_number_without_tools_counts_as_invented():
    """【阳性对照】无工具臂"猜对"了真数字：correct 仍然 False，且该数进 invented。"""
    facts = {"query_metrics_0": {"value": 1234.56}}
    res = eval_run.score(_fake_question(), "实付额 1,234.56 元", facts, set())
    assert res["correct"] is False
    assert res["invented"] == ["1234.56"]


def test_small_numbers_are_not_counted_as_invention():
    """'11 月''两个平台''第 1 名'这类小整数不该污染编造率；但大数必须抓。"""
    facts = {"query_metrics_0": {"value": 1234.56}}
    clean = eval_run.score(_fake_question(), "11 月两个平台都涨了", facts, eval_run._authorized_pool(facts))
    assert clean["invented"] == []
    dirty = eval_run.score(_fake_question(), "11 月两个平台涨了 8,888,888 元", facts, eval_run._authorized_pool(facts))
    assert dirty["invented"] == ["8888888"]


def test_year_and_month_are_not_read_as_business_numbers():
    """年月写法先被抠掉，否则 '2025 年 11 月' 会造出两个假编造。"""
    facts = {"query_metrics_0": {"value": 1234.56}}
    assert eval_run.score(_fake_question(), "2025 年 11 月实付额 1,234.56 元",
                          facts, eval_run._authorized_pool(facts))["invented"] == []


ARM_KEYS = {"question", "arm", "answer", "llm_calls", "tool_calls",
            "prompt_tokens", "completion_tokens", "elapsed_ms", "stop_reason"}


def test_loop_arm_hands_over_what_it_actually_saw(monkeypatch):
    """循环臂要把"自己看到过的工具返回值"交出来 —— 否则判分只能拿标准答案冒充授权。

    真跑过一次才知道要这条：M04 的循环查到了十二月第二名（4,564,306.29），
    而标准答案的取数计划每题只留 top1，于是那个**真数字**被判成编造。
    """
    replies = iter([
        'Thought: 先查总量\nAction: query_metrics\n'
        'Action Input: {"metric": "revenue", "start_date": "2025-11-01", "end_date": "2025-11-30"}',
        "Thought: 够了\nFinal Answer: 实付额 1,234.56 元",
    ])
    payload = {"value": 1234.56, "label": "实付额"}
    monkeypatch.setattr(eval_run.p0_single_call.p1_react, "call_llm",
                        lambda messages: (next(replies), 10, 5))
    monkeypatch.setattr(eval_run.p0_single_call.p1_react, "call_tool",
                        lambda name, args: payload)
    res = eval_run.p0_single_call.run("随便一题", "loop")
    assert res["tool_calls"] == 1, res
    assert res["authorized_list"] and res["authorized_list"][0]["value"] == 1234.56


def test_year_token_is_not_a_fabricated_business_number():
    """"2025 全年实付额约 8,888,888 元"：年份放行，金额照抓。"""
    facts = {"query_metrics_0": {"value": 1234.56}}
    res = eval_run.score(_fake_question(), "2025 全年实付额约 8,888,888 元", facts, set())
    assert res["invented"] == ["8888888"]


def test_wan_shorthand_is_neither_missed_nor_invented():
    """全量跑出来的两个判分缺陷之一：模型写"约 1005.4 万元"，被判成漏数 + 编造。

    授权写法必须覆盖 万/亿 的 0/1/2 位小数；只在量级够大时才给简写。
    """
    facts = {"query_metrics_0": {"value": 10053982.02}}
    pool = eval_run._authorized_pool(facts)
    res = eval_run.score(_fake_question(required=lambda p: [10053982.02]),
                         "12 月实付额约 1005.4 万元", facts, pool)
    assert res["correct"] is True, res
    assert res["invented"] == []


def test_percent_form_only_authorises_ratios():
    """0~1 的率才配百分数写法。给 1234.56 也收 ×100，等于白放行 123456 这种编造。"""
    assert "13.24" in eval_run._rich_variants(0.1324)
    assert "123456" not in eval_run._rich_variants(1234.56)


def test_truncated_observation_still_authorises_what_the_loop_saw():
    """判分缺陷之二：observation 被 OBS_MAX_CHARS 截断 → JSON 解析失败 → 真数字被判编造。

    授权的本义是"它眼睛看到过的东西"，所以按**文本**抽数，不要求是合法 JSON。
    """
    truncated = '{"breakdown": [{"dim_value": "微信公众号", "delta": 776684.7, "revenue_b": 4772645.42'
    pool = eval_run.pool_from_texts([truncated])
    assert eval_run.appears_in(pool, 776684.7)
    assert eval_run.appears_in(pool, 4772645.42)
    assert not eval_run.appears_in(pool, 1234567.89)      # 没看到过的仍然算编造


def test_arms_return_a_common_record_shape(monkeypatch):
    """三个臂必须吐同一组字段 —— 且这条测试不联网、不连库。

    为什么值得占一个用例：`_record` 的关键字名和调用方写成两套（`stop` vs `stop_reason`）时，
    真跑 pilot 会**先花掉 LLM 调用再抛 TypeError**，6/6 全崩。用假传输做形状检查，
    这类错就该在 CI 里 0 成本炸出来。
    """
    def fake_llm(messages: list[dict]) -> tuple[str, int, int]:
        return "Thought: 不用查了\nFinal Answer: 实付额 1,234.56 元", 11, 7

    monkeypatch.setattr(eval_run.p0_single_call.p1_react, "call_llm", fake_llm)
    for arm in ("no_tool", "one_tool", "loop"):
        res = eval_run.p0_single_call.run("随便一题", arm)
        assert ARM_KEYS <= set(res), f"{arm} 缺字段：{sorted(ARM_KEYS - set(res))}"
        assert isinstance(res["tool_calls"], int) and res["tool_calls"] >= 0
        assert res["stop_reason"], f"{arm} 没交代为什么停"


def test_fake_transport_produces_no_tool_calls(monkeypatch):
    """假传输下三个臂都不该产生工具调用 —— 否则说明它偷偷连了库。"""
    def fake_llm(messages: list[dict]) -> tuple[str, int, int]:
        return "Thought: 不用查了\nFinal Answer: 实付额 1,234.56 元", 11, 7

    monkeypatch.setattr(eval_run.p0_single_call.p1_react, "call_llm", fake_llm)
    for arm in ("no_tool", "one_tool", "loop"):
        assert eval_run.p0_single_call.run("随便一题", arm)["tool_calls"] == 0, arm


def test_summarize_groups_by_arm_and_kind():
    """汇总表按臂与题型分桶 —— 拿假记录核对分桶逻辑本身。"""
    rows = [
        {"arm": "loop", "kind": "single", "correct": True, "tool_calls": 2, "llm_calls": 3,
         "prompt_tokens": 100, "completion_tokens": 50, "elapsed_ms": 900, "invented": []},
        {"arm": "loop", "kind": "multi", "correct": False, "tool_calls": 1, "llm_calls": 2,
         "prompt_tokens": 100, "completion_tokens": 50, "elapsed_ms": 100, "invented": ["9"]},
        {"arm": "no_tool", "kind": "single", "correct": False, "tool_calls": 0, "llm_calls": 1,
         "prompt_tokens": 20, "completion_tokens": 10, "elapsed_ms": 300, "invented": ["7"]},
    ]
    s = eval_run.summarize(rows)
    assert s["loop"]["correct"] == "1/2" and s["loop"]["by_kind"] == {"multi": "0/1", "single": "1/1"}
    assert s["no_tool"]["avg_tool_calls"] == 0
    assert s["loop"]["invented_qs"] == 1


def test_arm_names_are_the_three_documented_ones():
    """三臂的名字是对外口径的一部分，改名要连带改文档 —— 用测试钉住。"""
    assert set(eval_run.__dict__["p0_single_call"].ARMS) == {"no_tool", "one_tool", "loop"}


@pytest.mark.parametrize("bad_arm", ["loop2", "", "No_Tool"])
def test_unknown_arm_raises_instead_of_silently_defaulting(bad_arm: str):
    """臂名写错必须炸，不能悄悄退回默认臂 —— 否则跑错对照组没人发现。"""
    with pytest.raises(ValueError, match="未知臂"):
        eval_run.p0_single_call.run("随便", bad_arm)
