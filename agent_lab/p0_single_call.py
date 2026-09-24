"""agent_lab.p0_single_call —— 单轮基线：给"循环到底值不值"提供一个可比的对照组。

================================================================================
【为什么要有这个文件】
只有"多步循环"这一边有数字、没有对照组，那句话就是信仰不是工程。
本文件提供两个退化臂，和 `p1_react.run_agent`
只差**两件事**：能不能多次取数、工具报错后能不能重试。模型、温度、协议、工具目录全一样。

【三个臂（严格定义，别让措辞越过实现）】
  no_tool   —— 1 次 LLM 调用、**0 次工具**。模型只有参数知识。预期：数字要么拒答要么编。
  one_tool  —— 最多 1 次工具 + 1 次收尾生成，**工具报错就是终局**（不回传、不重试）。
               LLM 调用 2 次。它考的是"只许赌一次，工具选对、参数填对"的能力。
  loop      —— 直接用 `p1_react.run_agent`（默认上限 8 步 / 24k token / 错误回传）。
⚠ one_tool 刻意保留 2 次 LLM 调用：若压成 1 次，它就退化成 no_tool，
  对比也就失去意义。**它和 loop 的唯一差别是"1 次取数、不许修正"** ——
  结论只能支持"多步 + 自我修正有价值"，不能支持"调用次数多就是好"。

【不做什么】不做 function calling 版基线（协议不同就不是对照组了）；不做多种温度
  （分析类任务 TEMPERATURE 恒为 0，见 p1_react）。
================================================================================
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_lab import p1_react  # noqa: E402
from agent_lab.tools import ToolError, call_tool, tool_catalog_text  # noqa: E402

NO_TOOL_PROMPT = """你是一个销售经营分析助手。
**本轮你没有任何数据工具**，只能凭已有知识回答。
铁律：不知道就直说"没有数据无法回答"，**绝对不许给出任何具体金额、单量或百分比**。
直接输出结论，不要写 Thought/Action 这些格式。
"""

ONE_TOOL_PROMPT = """你是一个销售经营分析助手。你可以调用工具获取**真实数据**。

可用工具：
{catalog}

**你只被允许调用一次工具**，然后必须立刻给出结论。
输出格式（严格二选一）：

Thought: <为什么查这个>
Action: <工具名>
Action Input: <严格 JSON 对象>

或（不需要数据时）：

Thought: <思考>
Final Answer: <给用户的回答>

铁律：
1. 绝对不许编造数字，回答里的每个数字都必须来自工具返回值。
2. 只有一次取数机会，请把最关键的参数填对。
3. Action Input 必须是合法 JSON。
"""


def _record(question: str, arm: str, answer: str, *, llm_calls: int, tool_calls: int,
            pt: int, ct: int, ms: float, stop_reason: str, extra: dict | None = None) -> dict:
    """把一次臂运行归一成同一个 dict —— 三个臂的字段不齐就没法在同一张表里比。"""
    out = {
        "question": question, "arm": arm, "answer": answer,
        "llm_calls": llm_calls, "tool_calls": tool_calls,
        "prompt_tokens": pt, "completion_tokens": ct, "elapsed_ms": round(ms, 1),
        "stop_reason": stop_reason,
    }
    if extra:
        out.update(extra)
    return out


def run_no_tool(question: str) -> dict:
    """臂 1：无工具单轮。只发一次请求，看模型在没有数据时会答什么。"""
    t0 = time.time()
    content, pt, ct = p1_react.call_llm([
        {"role": "system", "content": NO_TOOL_PROMPT},
        {"role": "user", "content": question},
    ])
    return _record(question, "no_tool", content.strip(), llm_calls=1, tool_calls=0,
                   pt=pt, ct=ct, ms=(time.time() - t0) * 1000, stop_reason="单轮（无工具）")


def run_one_tool(question: str) -> dict:
    """臂 2：一次工具 + 一次收尾。工具报错或参数非法都**不回传、不重试**，直接算终局。"""
    t0, pt, ct = time.time(), 0, 0
    messages = [
        {"role": "system", "content": ONE_TOOL_PROMPT.format(catalog=tool_catalog_text())},
        {"role": "user", "content": question},
    ]
    content, a, b = p1_react.call_llm(messages)
    pt += a
    ct += b
    parsed = p1_react.parse_model_output(content)

    if parsed["kind"] != "action":
        # 模型选择不查（或格式非法）—— 按它给的文本收场，不额外追问
        answer = parsed.get("answer") or content.strip()
        stop = ("模型选择不调用工具" if parsed["kind"] == "final"
                else f"首轮输出不可解析：{parsed.get('reason', '')}")
        return _record(question, "one_tool", answer, llm_calls=1, tool_calls=0,
                       pt=pt, ct=ct, ms=(time.time() - t0) * 1000, stop_reason=stop)

    tool_name, args = parsed["action"], parsed["args"]
    try:
        payload = call_tool(tool_name, args)
        observation = json.dumps(payload, ensure_ascii=False, default=str)
        errored = False
    except ToolError as exc:
        observation, errored = f"工具报错：{exc}", True
    except Exception as exc:  # noqa: BLE001
        observation, errored = f"工具内部异常：{type(exc).__name__}: {exc}", True
    if len(observation) > p1_react.OBS_MAX_CHARS:
        observation = observation[:p1_react.OBS_MAX_CHARS] + f"...(已截断，原长 {len(observation)} 字符)"

    if errored:
        # 这就是本臂与循环的分界：没有第二次机会，一次填错参数就到底了
        return _record(question, "one_tool", observation, llm_calls=1, tool_calls=1,
                       pt=pt, ct=ct, ms=(time.time() - t0) * 1000,
                       stop_reason=f"唯一一次工具调用失败（{tool_name}），无重试机会",
                       extra={"tool_used": tool_name, "tool_args": args})

    messages.append({"role": "assistant", "content": content})
    messages.append({"role": "user", "content": f"Observation: {observation}\n\n"
                                                "这是你唯一一次取数的结果，请据此给出最终回答。"})
    final, a2, b2 = p1_react.call_llm(messages)
    pt += a2
    ct += b2
    return _record(question, "one_tool", final.strip(), llm_calls=2, tool_calls=1,
                   pt=pt, ct=ct, ms=(time.time() - t0) * 1000,
                   stop_reason=f"一次工具调用后收尾（{tool_name}）",
                   extra={"tool_used": tool_name, "tool_args": args,
                          "authorized": payload})


def run_loop(question: str) -> dict:
    """臂 3：现成的 ReAct 循环（对照组本身），字段拉平成和另外两臂同一形状。

    顺手把**它真正看到过的工具返回值**收出来当授权集：不收的话判分只能拿标准答案的
    取数计划当授权，而计划里每题只留了 top1 —— 循环里查到的第二名、别的月份这些
    **真数字**就会被当成编造（M04 就这么冤枉过一次）。
    ⚠ 被 `OBS_MAX_CHARS` 截断的 observation 不是合法 JSON，解析失败就不进授权集，
      方向偏保守：宁可漏授权，也不给没看到的数字发通行证。
    """
    r = p1_react.run_agent(question, verbose=False)
    seen: list = []
    for s in r.steps:
        if s.observation.strip().startswith("{"):
            try:
                seen.append(json.loads(s.observation))
            except json.JSONDecodeError:
                continue
    return _record(question, "loop", r.answer, llm_calls=len(r.steps), tool_calls=r.tool_calls,
                   pt=r.total_prompt_tokens, ct=r.total_completion_tokens, ms=r.elapsed_ms,
                   stop_reason=r.stop_reason,
                   extra={"failure_modes": r.failure_modes, "authorized_list": seen,
                          "observation_texts": [s.observation for s in r.steps if s.observation]})


ARMS = {"no_tool": run_no_tool, "one_tool": run_one_tool, "loop": run_loop}


def run(question: str, arm: str) -> dict:
    """按臂名跑一次。臂名写错直接报错，不静默退回默认臂。"""
    if arm not in ARMS:
        raise ValueError(f"未知臂 '{arm}'，可选：{sorted(ARMS)}")
    return ARMS[arm](question)


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "2025 年 11 月的实付额是多少元？"
    for name in ("no_tool", "one_tool", "loop"):
        res = run(q, name)
        print("\n" + "=" * 78)
        print(f"[{name}] {q}")
        print(f"回答：{res['answer'][:400]}")
        print(f"停止原因：{res['stop_reason']} | LLM {res['llm_calls']} 次 / 工具 {res['tool_calls']} 次 "
              f"| tokens {res['prompt_tokens']}+{res['completion_tokens']} | {res['elapsed_ms']} ms")
