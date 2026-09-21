"""agent_lab.p1_react —— 手写 ReAct 循环（不用任何 Agent 框架）。

================================================================================
【为什么要手写一遍】
`create_agent(model, tools)` 一行背后，框架替你做了：system prompt 组装、工具
schema 注入、多轮循环、消息历史维护、停止条件、错误回传、结果解析。你只调用了它，
没有实现它 —— 所以"学完了还是觉得什么都不懂"。这个文件把那些事全部摊开。

【本文件亲手实现了哪些机制】（编号对应《Agent 开发学习路线》第二节）
  #2 循环与停止条件：max_steps / token 预算 / 重复动作检测 / 单步超时
  #3 上下文工程：observation 截断策略（可调），目标与工具说明固定在 system prompt
  #4 失败模式处理：输出格式非法 / 工具名幻觉 / 参数类型错 / JSON 解析失败
     —— 全部转成 observation 回传给模型，让它自己修正（这就是"错误回传"的价值）
  #10 可观测性：每步记录 thought / action / args / observation / 耗时 / token

【刻意不做】不接 LangChain、不做向量检索、不做并行 —— 先看清最小闭环。
【协议】经典 ReAct 文本协议（不是原生 function calling）：
     Thought: ...
     Action: 工具名
     Action Input: {...JSON...}
     或
     Thought: ...
     Final Answer: ...
  选文本协议而不是 function calling，是因为**解析输出、处理畸形输出是你必须自己写的部分**，
  这部分恰恰是"会调框架"和"会做 Agent"的分界线。
================================================================================
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

from agent_lab.tools import ToolError, call_tool, tool_catalog_text  # noqa: E402

load_dotenv(str(PROJECT_ROOT / ".env"))

# ------------------------------------------------------------------ 配置
# 注意：这两个是**默认值**，不是运行期开关。
# 单次运行的上限请通过 run_agent(max_steps=..., token_budget=...) 传入，
# 不要改写模块全局 —— 并发下会互相污染（见 run_agent 的踩坑记录）。
MAX_STEPS = 8               # 步数上限默认值（机制 #2）
TOKEN_BUDGET = 24000        # 总 token 预算默认值（机制 #2）
STEP_TIMEOUT_SEC = 60       # 单次 LLM 调用超时
OBS_MAX_CHARS = 1800        # observation 截断长度（机制 #3：上下文工程）
TEMPERATURE = 0.0           # 分析类任务必须确定性

SYSTEM_PROMPT = """你是一个销售经营分析助手。你可以调用工具获取**真实数据**，再基于数据回答。

可用工具：
{catalog}

输出格式（**必须严格遵守**，每次只做一件事）：

Thought: <你的思考，说明为什么要查这个>
Action: <工具名>
Action Input: <严格的 JSON 对象>

当你已经能从已有数据得出结论时，改为输出：

Thought: <思考>
Final Answer: <给用户的回答>

铁律：
1. **绝对不许编造数字**。回答里出现的每一个数字都必须来自工具返回值。
2. 一次只调用一个工具；需要多个数据就分多步。
3. Action Input 必须是合法 JSON（双引号、无注释、无尾逗号）。
4. 工具返回里已经算好的值（如 mom_pct 环比、volume_effect 量效应）直接引用，不要自己重算。
5. 如果工具报错，读懂错误信息后**修正参数重试**，不要原样重发。
"""


@dataclass
class Step:
    """一步的完整轨迹（机制 #10：可观测性）。"""
    index: int
    thought: str = ""
    action: str = ""
    action_input_raw: str = ""
    args: dict | None = None
    observation: str = ""
    error: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_ms: float = 0.0


@dataclass
class RunResult:
    """一次 Agent 运行的完整结果：答案 + 轨迹 + 停止原因 + 成本 + 失败模式。"""

    question: str
    answer: str = ""
    steps: list[Step] = field(default_factory=list)
    stop_reason: str = ""
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    elapsed_ms: float = 0.0
    failure_modes: list[str] = field(default_factory=list)

    @property
    def tool_calls(self) -> int:
        """工具调用次数（步骤数 ≠ 工具调用数：收尾那一步不带 Action）。"""
        return sum(1 for s in self.steps if s.action)


# ------------------------------------------------------------------ LLM 调用（原生 HTTP）
def call_llm(messages: list[dict]) -> tuple[str, int, int]:
    """直接 POST 到 OpenAI 兼容接口。返回 (内容, prompt_tokens, completion_tokens)。"""
    api_key = os.getenv("LLM_API_KEY", "")
    if not api_key or api_key.startswith("sk-在这里"):
        raise SystemExit("❌ .env 里没有可用的 LLM_API_KEY")
    base = os.getenv("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("LLM_MODEL", "deepseek-chat")

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": TEMPERATURE,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=STEP_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 把 HTTP 错误也变成可读信息
        raise RuntimeError(f"LLM HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')[:300]}") from exc
    except Exception as exc:
        raise RuntimeError(f"LLM 调用失败: {type(exc).__name__}: {exc}") from exc

    content = data["choices"][0]["message"]["content"] or ""
    usage = data.get("usage", {}) or {}
    return content, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


# ------------------------------------------------------------------ 输出解析（机制 #4）
def extract_json_object(text: str) -> str | None:
    """从文本里抠出**第一个完整的 JSON 对象**（花括号配对，且跳过字符串内的括号）。

    为什么要自己写：模型经常在 JSON 后面接着写解释，或者 JSON 里嵌套括号。
    直接 json.loads 整段几乎必然失败 —— 这就是"畸形输出"的第一课。
    """
    start = text.find("{")
    if start == -1:
        return None
    depth, in_str, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def parse_model_output(content: str) -> dict:
    """解析模型输出。返回 {kind: 'final'|'action'|'invalid', ...}。"""
    final_match = re.search(r"Final\s*Answer\s*[:：]\s*(.*)", content, re.S | re.I)
    action_match = re.search(r"^\s*Action\s*[:：]\s*(\S+)\s*$", content, re.M | re.I)
    thought_match = re.search(r"^\s*Thought\s*[:：]\s*(.*?)(?=^\s*Action\s*[:：]|^\s*Final\s*Answer\s*[:：]|\Z)",
                              content, re.S | re.M | re.I)
    thought = (thought_match.group(1).strip() if thought_match else "")[:500]

    # 有 Final Answer 且没有 Action → 收尾
    if final_match and not action_match:
        return {"kind": "final", "thought": thought, "answer": final_match.group(1).strip()}

    if not action_match:
        return {"kind": "invalid", "thought": thought, "raw": content,
                "reason": "输出里既没有 Action 也没有 Final Answer"}

    action = action_match.group(1).strip().strip("`").strip()
    tail = content[action_match.end():]
    raw_json = extract_json_object(tail)
    if raw_json is None:
        return {"kind": "invalid", "thought": thought, "action": action, "raw": content,
                "reason": f"Action Input 不是合法 JSON 对象（原始片段：{tail.strip()[:120]!r}）"}
    try:
        args = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        return {"kind": "invalid", "thought": thought, "action": action, "raw": content,
                "reason": f"JSON 解析失败：{exc}"}
    if not isinstance(args, dict):
        return {"kind": "invalid", "thought": thought, "action": action, "raw": content,
                "reason": f"Action Input 必须是 JSON 对象，收到 {type(args).__name__}"}
    return {"kind": "action", "thought": thought, "action": action, "args": args}


# ------------------------------------------------------------------ ReAct 主循环
def run_agent(question: str, verbose: bool = True, tool_hook=None,
              max_steps: int | None = None, token_budget: int | None = None) -> RunResult:
    """跑一轮 ReAct。

    Args:
        tool_hook: 可选钩子 `(name, args) -> dict`，用于**故障注入压测**：
                   可以在真实工具外包裹报错、延迟、返回空数据等，
                   用来验证"模型能不能读懂错误并自我修正"。
        max_steps: 本次运行的步数上限。`None` 时用模块默认值 `MAX_STEPS`。
        token_budget: 本次运行的 token 预算。`None` 时用模块默认值 `TOKEN_BUDGET`。

    【踩坑记录 · 为什么这两个参数必须按次传入，而不是改模块全局】
    最初的实现是让调用方（FastAPI 的 /analyze）临时改写模块全局 `MAX_STEPS`，
    用完再还原。单请求跑起来没问题，但 FastAPI 的同步 `def` 接口是跑在**线程池**里的，
    两个并发请求会交错执行：

        请求 A: old=8 → 全局设为 3 ────────────────► 还原 8
        请求 B:            old=3 → 全局设为 10 ──────────► 还原 3（把 A 的设置覆盖/污染）

    结果是 A 请求可能真的跑满 8~10 步（**多花钱**），而且还原顺序一乱，
    后续请求还会继承到一个谁也没设过的值。这是典型的"可变共享状态 + 无锁"竞态。
    修法：上限作为**参数**沿调用链传下去，循环只读局部变量，模块常量退化为默认值。
    教训：全局变量不是"配置"，是**跨请求共享的可变状态**。
    """
    step_limit = MAX_STEPS if max_steps is None else max_steps
    budget = TOKEN_BUDGET if token_budget is None else token_budget
    result = RunResult(question=question)
    t0 = time.time()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(catalog=tool_catalog_text())},
        {"role": "user", "content": question},
    ]
    seen_actions: dict[str, int] = {}          # 重复动作检测（机制 #2）
    total_tokens = 0

    for step_no in range(1, step_limit + 1):
        step = Step(index=step_no)
        st = time.time()
        try:
            content, pt, ct = call_llm(messages)
        except RuntimeError as exc:
            result.failure_modes.append("llm_error")
            result.stop_reason = f"LLM 调用失败：{exc}"
            break
        step.elapsed_ms = round((time.time() - st) * 1000, 1)
        step.prompt_tokens, step.completion_tokens = pt, ct
        result.total_prompt_tokens += pt
        result.total_completion_tokens += ct
        total_tokens += pt + ct

        parsed = parse_model_output(content)
        step.thought = parsed.get("thought", "")

        if verbose:
            print(f"\n  ── Step {step_no} ── ({step.elapsed_ms} ms, tokens {pt}+{ct})")
            if step.thought:
                print(f"  Thought: {step.thought}")

        # ---- 收尾
        if parsed["kind"] == "final":
            result.answer = parsed["answer"]
            result.steps.append(step)
            result.stop_reason = "模型给出 Final Answer"
            if verbose:
                print("  Final Answer ↑")
            break

        # ---- 畸形输出：把错误当 observation 回传（机制 #4）
        if parsed["kind"] == "invalid":
            step.error = parsed["reason"]
            step.observation = f"格式错误：{parsed['reason']}。请严格按 Thought/Action/Action Input 格式重发。"
            result.failure_modes.append("malformed_output")
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": f"Observation: {step.observation}"})
            result.steps.append(step)
            if verbose:
                print(f"  ⚠ 畸形输出（已回传纠错）：{parsed['reason']}")
            if result.failure_modes.count("malformed_output") >= 3:
                result.stop_reason = "连续 3 次格式错误，放弃"
                break
            continue

        # ---- 正常工具调用
        step.action = parsed["action"]
        step.args = parsed["args"]
        step.action_input_raw = json.dumps(parsed["args"], ensure_ascii=False)
        if verbose:
            print(f"  Action: {step.action}")
            print(f"  Action Input: {step.action_input_raw}")

        signature = f"{step.action}|{json.dumps(parsed['args'], sort_keys=True, ensure_ascii=False)}"
        prev = seen_actions.get(signature, {"count": 0, "last_error": False})
        repeat_no = prev["count"] + 1
        # 【踩坑记录 · D2 压测抓出的 bug】
        # 死循环检测不能只看"参数是否相同"：**工具报错后的原样重试是合法的**，
        # 甚至是我们要鼓励的行为。原实现一律拦截，导致模型在 T3 里被拦住、
        # 最后只能放弃并告知用户"查询失败"——那是机制的错，不是模型的错。
        # 修正：若上一次同签名调用以工具错误收场，放行重试，且最多放行一次。
        legit_retry = bool(prev["last_error"]) and repeat_no <= 2
        seen_actions[signature] = {"count": repeat_no, "last_error": False}

        if repeat_no > 1 and not legit_retry:
            # 机制 #2：死循环检测。模型很爱重复调用同一个工具，这是最常见的失控模式。
            result.failure_modes.append("repeat_action")
            observation = (
                f"你刚刚已经用完全相同的参数调用过 {step.action}，结果没有变化。"
                "请换参数、换工具，或者直接输出 Final Answer。"
            )
            step.observation = observation
            result.steps.append(step)
            if verbose:
                print("  ⚠ 重复动作（已拦截）")
            if repeat_no >= 3:
                result.stop_reason = "同一动作重复 3 次，判定为死循环"
                break
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": f"Observation: {observation}"})
            continue

        if legit_retry and verbose:
            print("  ↻ 上一次以工具错误收场，放行本次重试")

        try:
            if tool_hook is not None:
                payload = tool_hook(step.action, parsed["args"])
            else:
                payload = call_tool(step.action, parsed["args"])
            observation = json.dumps(payload, ensure_ascii=False, default=str)
        except ToolError as exc:
            # 错误回传：让模型根据错误自我修正（这是"工具报错就重试"和"读懂错误再改"的区别）
            result.failure_modes.append("tool_error")
            seen_actions[signature]["last_error"] = True
            observation = f"工具报错：{exc}"
        except Exception as exc:  # noqa: BLE001
            result.failure_modes.append("tool_exception")
            seen_actions[signature]["last_error"] = True
            observation = f"工具内部异常：{type(exc).__name__}: {exc}"

        # 机制 #3：上下文工程 —— observation 过长会迅速吃满 token 预算
        if len(observation) > OBS_MAX_CHARS:
            observation = observation[:OBS_MAX_CHARS] + f"...(已截断，原长 {len(observation)} 字符)"
        step.observation = observation
        result.steps.append(step)
        if verbose:
            print(f"  Observation: {observation[:400]}{'...' if len(observation) > 400 else ''}")

        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": f"Observation: {observation}"})

        if total_tokens > budget:
            result.failure_modes.append("token_budget_exceeded")
            result.stop_reason = f"token 预算耗尽（{total_tokens} > {budget}）"
            break
    else:
        result.stop_reason = f"达到步数上限 {step_limit}"

    result.elapsed_ms = round((time.time() - t0) * 1000, 1)
    return result


# ------------------------------------------------------------------ 演示
QUESTIONS = [
    "2025 年 11 月的实付额是多少？",
    "11 月相比 10 月，销售额是涨了还是跌了？",
    "11 月销售涨了，但感觉是靠单量堆出来的，客单价是不是在拖累？具体拖累了多少？",
]


def print_summary(r: RunResult) -> None:
    """打印单次运行的收尾摘要：答案 / 停止原因 / 步数 / token / 失败模式计数。"""
    print("\n" + "=" * 78)
    print(f"问题：{r.question}")
    print(f"回答：{r.answer or '（未得出结论）'}")
    print(f"停止原因：{r.stop_reason}")
    print(f"步数 {len(r.steps)} | 工具调用 {r.tool_calls} 次 | "
          f"tokens {r.total_prompt_tokens}+{r.total_completion_tokens}="
          f"{r.total_prompt_tokens + r.total_completion_tokens} | 总耗时 {r.elapsed_ms} ms")
    if r.failure_modes:
        from collections import Counter
        print(f"失败模式：{dict(Counter(r.failure_modes))}")
    else:
        print("失败模式：无")
    print("=" * 78)


if __name__ == "__main__":
    for q in QUESTIONS:
        print("\n" + "#" * 78)
        print(f"# {q}")
        print("#" * 78)
        res = run_agent(q, verbose=True)
        print_summary(res)
