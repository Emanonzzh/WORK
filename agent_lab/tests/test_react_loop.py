"""手写 ReAct 循环 `run_agent` 的单测 —— 用**假的 LLM**驱动，不联网、不连库。

===============================================================================
这个文件存在的意义（也是整个测试套件里最值钱的一个）：

1. **锁住并发竞态这个真实 bug 的修复。**
   修复前 `api.py` 用"临时改写模块全局 `MAX_STEPS`、用完还原"来控制单次请求的步数。
   单请求没问题，但 FastAPI 的同步 `def` 接口跑在**线程池**里，并发请求会交错：
   后到的请求读到的是别人改过的值，还原顺序也会互相覆盖 ——
   A 请求设了 3 步，却可能按 B 的 10 步跑满，**实实在在地多花钱**。
   现在上限是 `run_agent(max_steps=...)` 的参数，不再碰全局。
   `test_concurrent_runs_keep_their_own_step_limit` 就是防止有人改回去。

2. **把"模型输出畸形怎么办"变成可执行的规格。**
   畸形输出、工具名幻觉、工具报错、死循环 —— 这些不是异常情况，是**常态**。
   用假 LLM 可以精确制造每一种，然后断言循环的处理方式。

为什么必须用假 LLM：真 LLM 的输出不可复现，而且花钱。
把"不可控的外部依赖"换成"可控的假对象"，是让 Agent 代码可测的唯一办法 ——
这也是"会调框架"和"会写可测代码"的分界。
===============================================================================
"""
from __future__ import annotations

import threading
import time

import pytest

from agent_lab import p1_react as react
from agent_lab.tools import ToolError


# ---------------------------------------------------------------- 测试替身
def _action_line(day: int) -> str:
    """生成一个**参数各不相同**的合法 Action，避免触发重复动作检测。"""
    return (
        f"Thought: 查第 {day} 天\n"
        "Action: query_metrics\n"
        'Action Input: {"metric": "revenue", '
        f'"start_date": "2025-11-{day:02d}", "end_date": "2025-11-{day:02d}"}}'
    )


def _final_line(answer: str = "已完成") -> str:
    return f"Thought: 数据够了\nFinal Answer: {answer}"


def _fake_llm(script, prompt_tokens: int = 10, completion_tokens: int = 5):
    """按剧本返回输出的假 LLM；剧本用完后重复最后一条。"""
    box = {"i": 0}

    def _call(messages):
        i = box["i"]
        box["i"] += 1
        content = script[i] if i < len(script) else script[-1]
        return content, prompt_tokens, completion_tokens

    _call.box = box
    return _call


def _ok_tool(name, args):
    return {"value": 1.0, "echo": args}


@pytest.fixture()
def patched(monkeypatch):
    """把 call_llm / call_tool 都换成假对象；返回一个便于改写的命名空间。"""

    class _P:
        def llm(self, script, **kw):
            fake = _fake_llm(script, **kw)
            monkeypatch.setattr(react, "call_llm", fake)
            return fake

        def tool(self, func):
            monkeypatch.setattr(react, "call_tool", func)

        def raw_llm(self, func):
            monkeypatch.setattr(react, "call_llm", func)

    p = _P()
    p.tool(_ok_tool)
    return p


# ================================================================ 步数上限
def test_max_steps_parameter_is_honored(patched):
    """`max_steps` 参数生效：循环跑到上限就停，并说明原因。"""
    patched.llm([_action_line(d) for d in range(1, 13)])
    result = react.run_agent("11 月情况？", verbose=False, max_steps=3)
    assert len(result.steps) == 3
    assert result.stop_reason == "达到步数上限 3"


def test_max_steps_defaults_to_module_constant(patched, monkeypatch):
    """不传 `max_steps` 时用模块常量当默认值（模块常量是"默认值"，不是开关）。"""
    monkeypatch.setattr(react, "MAX_STEPS", 2)
    patched.llm([_action_line(d) for d in range(1, 13)])
    result = react.run_agent("q", verbose=False)
    assert len(result.steps) == 2
    assert result.stop_reason == "达到步数上限 2"


def test_final_answer_ends_run_immediately(patched):
    """给出 Final Answer 就立刻收尾，不再多走一步（省钱）。"""
    patched.llm([_final_line("11 月实付额 2020700.00 元")])
    result = react.run_agent("q", verbose=False, max_steps=8)
    assert result.answer == "11 月实付额 2020700.00 元"
    assert len(result.steps) == 1
    assert result.tool_calls == 0
    assert result.stop_reason == "模型给出 Final Answer"


# ================================================================ 并发隔离（核心回归）
def test_run_agent_does_not_mutate_module_global(patched):
    """单次运行绝不能改写模块全局 —— 这是修复前 bug 的根因。"""
    patched.llm([_action_line(d) for d in range(1, 13)])
    before = react.MAX_STEPS
    react.run_agent("q", verbose=False, max_steps=1)
    assert react.MAX_STEPS == before


def test_concurrent_runs_keep_their_own_step_limit(monkeypatch):
    """【核心回归测试】三个并发运行，各自的上限互不干扰。

    修复前的实现（改写全局 MAX_STEPS）在这条测试下会失败：
    线程 A 想把上限设成 1，线程 B 设成 5，谁先把全局写进去，
    另一个就跑错步数；`finally` 的还原顺序还会把全局留成一个谁也没设过的值。
    """
    lock = threading.Lock()
    counter = {"i": 0}

    def fake_llm(messages):
        with lock:
            counter["i"] += 1
            i = counter["i"]
        time.sleep(0.01)                       # 放大交错窗口
        return _action_line((i % 27) + 1), 1, 1

    monkeypatch.setattr(react, "call_llm", fake_llm)
    monkeypatch.setattr(react, "call_tool", _ok_tool)

    global_before = react.MAX_STEPS
    results: dict[str, react.RunResult] = {}

    def worker(name: str, limit: int) -> None:
        results[name] = react.run_agent("q", verbose=False, max_steps=limit)

    threads = [
        threading.Thread(target=worker, args=("short", 1)),
        threading.Thread(target=worker, args=("mid", 3)),
        threading.Thread(target=worker, args=("long", 5)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results["short"].stop_reason == "达到步数上限 1"
    assert results["mid"].stop_reason == "达到步数上限 3"
    assert results["long"].stop_reason == "达到步数上限 5"
    assert len(results["short"].steps) == 1
    assert len(results["mid"].steps) == 3
    assert len(results["long"].steps) == 5
    assert react.MAX_STEPS == global_before, "并发运行不得污染模块全局"


def test_analyze_endpoint_passes_limit_as_argument(monkeypatch):
    """`/analyze` 必须把步数当**参数**传下去，而不是改写全局。

    这条直接钉住 api.py 与 p1_react.py 之间的契约：
    契约一旦被改回"设全局"，下面两个断言之一必然失败。
    """
    from agent_lab import api

    captured = {}

    def fake_run_agent(question, verbose=True, tool_hook=None,
                       max_steps=None, token_budget=None):
        captured["question"] = question
        captured["verbose"] = verbose
        captured["max_steps"] = max_steps
        return react.RunResult(question=question, answer="ok", stop_reason="done")

    monkeypatch.setattr(react, "run_agent", fake_run_agent)
    before = react.MAX_STEPS

    payload = api.analyze(api.AnalyzeRequest(question="11 月实付额是多少？", max_steps=4))

    assert captured["max_steps"] == 4
    assert captured["verbose"] is False, "接口调用必须静默（不能往服务日志里刷轨迹）"
    assert react.MAX_STEPS == before, "接口不得改写模块全局"
    assert payload["answer"] == "ok"


def test_analyze_endpoint_caps_requested_steps(monkeypatch):
    """请求要 10 步，也要被硬上限 8 压住 —— 成本保险丝不能被参数绕过。"""
    from agent_lab import api

    captured = {}

    def fake_run_agent(question, verbose=True, tool_hook=None,
                       max_steps=None, token_budget=None):
        captured["max_steps"] = max_steps
        return react.RunResult(question=question, answer="ok")

    monkeypatch.setattr(react, "run_agent", fake_run_agent)
    api.analyze(api.AnalyzeRequest(question="q", max_steps=10))
    assert captured["max_steps"] == react.MAX_STEPS == 8


# ================================================================ 机制 #2：死循环与重试
def test_identical_action_twice_is_intercepted(patched):
    """同参数动作重复 → 拦截并回传提示（模型很爱重复调同一个工具）。"""
    patched.llm([_action_line(1)])            # 永远返回同一个动作
    result = react.run_agent("q", verbose=False, max_steps=8)
    assert "repeat_action" in result.failure_modes
    assert "死循环" in result.stop_reason
    assert len(result.steps) == 3


def test_retry_after_tool_error_is_allowed(patched):
    """【D2 压测抓出的 bug 回归】工具报错后**原样重试是合法的**，不能被死循环检测拦掉。

    原实现一律拦截，导致模型被自己的安全机制挡住、最后只能告诉用户"查询失败"。
    那是机制的错，不是模型的错。
    """
    same = _action_line(1)
    patched.llm([same, same, _final_line("该期间没有数据")])

    calls = {"n": 0}

    def flaky_tool(name, args):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ToolError("模拟：数据库连接抖动")
        return {"value": 123.0}

    patched.tool(flaky_tool)
    result = react.run_agent("q", verbose=False)

    assert calls["n"] == 2, "第二次相同调用必须被放行（这正是修复点）"
    assert "tool_error" in result.failure_modes
    assert result.answer == "该期间没有数据"


def test_repeat_after_success_is_still_blocked(patched):
    """成功之后再重复 → 仍然要拦（不能因为加了"报错可重试"就整体放开）。"""
    same = _action_line(1)
    patched.llm([same])                       # 每次都成功，却每次都问同一个东西
    result = react.run_agent("q", verbose=False, max_steps=8)
    assert "repeat_action" in result.failure_modes
    assert result.failure_modes.count("repeat_action") == 2


# ================================================================ 机制 #4：畸形输出
def test_malformed_output_is_returned_as_observation(patched):
    """格式错误的输出不丢弃，而是转成 observation 回传，让模型自己修正。"""
    patched.llm(["我觉得应该查一下销售数据。", _final_line("已按格式修正")])
    result = react.run_agent("q", verbose=False)

    assert "malformed_output" in result.failure_modes
    assert result.answer == "已按格式修正"
    assert "格式错误" in result.steps[0].observation
    assert result.steps[-1].index == 2


def test_three_malformed_outputs_give_up(patched):
    """连续 3 次格式错误就放弃 —— 不能无限重试烧钱。"""
    patched.llm(["完全不合格式的一段话"])
    result = react.run_agent("q", verbose=False, max_steps=10)
    assert result.stop_reason == "连续 3 次格式错误，放弃"
    assert result.failure_modes.count("malformed_output") == 3


def test_json_with_trailing_prose_is_still_parsed(patched):
    """JSON 后面跟着解释文字 → 仍要能抠出 JSON（extract_json_object 的职责）。"""
    content = (
        "Thought: 查一下\n"
        "Action: query_metrics\n"
        'Action Input: {"metric": "revenue", "start_date": "2025-11-01", "end_date": "2025-11-30"}\n'
        "（以上就是我要查的东西，希望有帮助。）"
    )
    patched.llm([content, _final_line("ok")])
    result = react.run_agent("q", verbose=False)
    assert result.steps[0].action == "query_metrics"
    assert result.steps[0].args == {
        "metric": "revenue", "start_date": "2025-11-01", "end_date": "2025-11-30"
    }


def test_invalid_json_action_input_is_flagged_as_malformed(patched):
    """Action Input 不是合法 JSON → 归为畸形输出，而不是抛异常崩掉。"""
    patched.llm([
        "Thought: 查一下\nAction: query_metrics\nAction Input: {metric: revenue}",
        _final_line("修好了"),
    ])
    result = react.run_agent("q", verbose=False)
    assert "malformed_output" in result.failure_modes
    assert result.answer == "修好了"


# ================================================================ 机制 #4：LLM 与工具异常
def test_llm_runtime_error_stops_gracefully(patched):
    """LLM 调用失败 → 记录失败模式并停止，而不是把异常抛给用户。"""

    def boom(messages):
        raise RuntimeError("LLM HTTP 429: rate limited")

    patched.raw_llm(boom)
    result = react.run_agent("q", verbose=False)
    assert "llm_error" in result.failure_modes
    assert "429" in result.stop_reason
    assert result.answer == ""


def test_unexpected_tool_exception_is_returned_as_observation(patched):
    """工具内部异常也要被兜住并回传（区分 ToolError 与未预期异常）。"""

    def broken(name, args):
        raise KeyError("column 'nope' not found")

    patched.tool(broken)
    patched.llm([_action_line(1), _final_line("换个查法")])
    result = react.run_agent("q", verbose=False)
    assert "tool_exception" in result.failure_modes
    assert "KeyError" in result.steps[0].observation


def test_tool_hook_is_used_for_fault_injection(patched):
    """`tool_hook` 用于故障注入压测：它应当**取代**真实工具被调用。"""
    patched.llm([_action_line(1), _final_line("ok")])
    hook_calls = []

    def hook(name, args):
        hook_calls.append((name, args))
        return {"injected": True}

    react.run_agent("q", verbose=False, tool_hook=hook)
    assert [name for name, _ in hook_calls] == ["query_metrics"]


# ================================================================ 机制 #2 / #3：预算与上下文
def test_token_budget_stops_the_loop(patched):
    """token 预算耗尽要停下来并说明（成本保险丝）。"""
    patched.llm([_action_line(d) for d in range(1, 13)],
                prompt_tokens=100, completion_tokens=50)
    result = react.run_agent("q", verbose=False, max_steps=10, token_budget=200)
    assert "token_budget_exceeded" in result.failure_modes
    assert "token 预算耗尽" in result.stop_reason
    assert len(result.steps) == 2


def test_long_observation_is_truncated(patched):
    """超长 observation 必须截断（上下文工程：不然一步就吃满预算）。"""
    patched.tool(lambda name, args: {"blob": "x" * 5000})
    patched.llm([_action_line(1), _final_line("ok")])
    result = react.run_agent("q", verbose=False)
    observation = result.steps[0].observation
    assert "已截断" in observation
    assert len(observation) < 5000
    assert len(observation) <= react.OBS_MAX_CHARS + 80


# ================================================================ 可观测性
def test_trace_records_thought_action_args_observation(patched):
    """每步都要留下可追溯的轨迹（机制 #10）—— 出问题时要能复盘。"""
    patched.llm([_action_line(7), _final_line("结论")])
    result = react.run_agent("11 月怎么样？", verbose=False)

    first = result.steps[0]
    assert first.index == 1
    assert "查第 7 天" in first.thought
    assert first.action == "query_metrics"
    assert first.args["start_date"] == "2025-11-07"
    assert first.observation
    assert first.action_input_raw.startswith("{")

    assert result.question == "11 月怎么样？"
    assert result.total_prompt_tokens > 0
    assert result.elapsed_ms >= 0
    assert result.tool_calls == 1
