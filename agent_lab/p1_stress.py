"""agent_lab.p1_stress —— D2 压测：**主动把 Agent 的失败模式逼出来**。

为什么要有这个文件：
上一轮 3 个问题全对、零失败模式 —— 那不是"Agent 很好"，而是**题目太简单**。
没有失败模式的 Agent 报告是不真实的，也学不到东西。本文件用 6 个刻意构造的
场景去撞它的边界，把"哪里会坏、怎么坏、坏到什么程度"记录下来。

6 个场景对应《学习路线》第二节的机制：
  T1 超纲外推   → 机制 #2 停止条件（工具答不了，会不会反复重试到步数耗尽）
  T2 非法参数   → 机制 #4 错误回传（工具报错后能不能修正）
  T3 故障注入   → 机制 #4 错误回传（用 tool_hook 模拟数据库超时，验证重试）
  T4 极模糊提问 → 机制 #5 规划（会不会反问澄清，还是自己乱选口径）
  T5 跨维度多步 → 机制 #5 规划（需要多次工具调用的真·多步任务）
  T6 上下文压力 → 机制 #3 上下文工程（observation 累积 → token 爆炸/截断）

用法：python agent_lab/p1_stress.py
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent_lab.p1_react import RunResult, run_agent  # noqa: E402
from agent_lab.tools import ToolError, call_tool  # noqa: E402


# ------------------------------------------------------------------ T3 的故障注入钩子
def flaky_tool_hook(name: str, args: dict) -> dict:
    """模拟"第一次调用 query_metrics 时数据库超时"，之后恢复正常。

    这才是真实生产里最常见的故障：网络抖动、连接池耗尽。
    要验证的是：模型能不能**读懂错误信息并重试**，而不是放弃或编造。
    """
    state = flaky_tool_hook.state
    if name == "query_metrics" and state["fails"] > 0:
        state["fails"] -= 1
        raise ToolError(
            "数据库连接超时（错误码 DB_TIMEOUT_504）：本次查询未执行，"
            "数据未被修改，可以直接重试完全相同的参数"
        )
    return call_tool(name, args)


flaky_tool_hook.state = {"fails": 1}  # type: ignore[attr-defined]


# ------------------------------------------------------------------ 场景定义
TESTS: list[dict] = [
    {
        "id": "T1",
        "name": "超纲外推（工具做不到的事）",
        "question": "预测一下 2026 年 1 月的销售额会是多少，并说明你的依据。",
        "expect": "工具只能查历史（数据截止 2025-12-31）。观察它会不会反复换区间重试、"
                  "会不会编造一个预测数字、还是明确说明「超出能力范围」",
    },
    {
        "id": "T2",
        "name": "非法参数（不存在的月份）",
        "question": "帮我查一下 2025 年 13 月的销售额。",
        "expect": "13 月不存在。观察它是编造一个数、还是报错后换合法口径（比如说明只有 1-12 月）",
    },
    {
        "id": "T3",
        "name": "故障注入（模拟数据库超时）",
        "question": "2025 年 11 月的订单数是多少？",
        "expect": "第一次调用被注入 DB_TIMEOUT_504。观察它能否读懂错误并原样重试成功",
        "hook": flaky_tool_hook,
    },
    {
        "id": "T4",
        "name": "极模糊提问",
        "question": "最近生意怎么样？",
        "expect": "没有时间范围、没有指标、没有对比基线。观察它是反问澄清、"
                  "还是自行假设一个口径（后者要记录它假设了什么）",
    },
    {
        "id": "T5",
        "name": "跨维度多步分析",
        "question": "对比微信公众号和 APP 这两个平台，11 月谁的下滑更严重？原因有什么不同？",
        "expect": "需要多次工具调用（至少 2 次）。观察多步规划是否稳定、会不会只查一半就下结论",
    },
    {
        "id": "T6",
        "name": "上下文压力（重度取数）",
        "question": "把 2025 年销售额最高的前 10 个商品都列出来，并且逐个告诉我它们各自的月度趋势。",
        "expect": "observation 会快速累积。观察 token 增长、是否触发 1800 字符截断、"
                  "是否因为上下文过长而丢目标或步数耗尽",
    },
]


def main() -> int:
    """依次跑 6 个压测场景并打印汇总表（命令行参数可只跑指定场景）。"""
    # 支持只跑指定场景：python agent_lab/p1_stress.py T2 T3
    wanted = {a.upper() for a in sys.argv[1:]} or {t["id"] for t in TESTS}
    results: list[tuple[dict, RunResult]] = []
    for t in TESTS:
        if t["id"] not in wanted:
            continue
        print("\n" + "#" * 78)
        print(f"# [{t['id']}] {t['name']}")
        print(f"# 问题：{t['question']}")
        print(f"# 预期观察：{t['expect']}")
        print("#" * 78)
        if t.get("hook"):
            t["hook"].state["fails"] = 1  # 每个场景重置注入状态
        t0 = time.time()
        res = run_agent(t["question"], verbose=True, tool_hook=t.get("hook"))
        res.elapsed_ms = res.elapsed_ms or round((time.time() - t0) * 1000, 1)
        results.append((t, res))
        print(f"\n  → 回答：{res.answer or '（未得出结论）'}")
        print(f"  → 停止原因：{res.stop_reason}")
        print(f"  → 步数 {len(res.steps)} / 工具调用 {res.tool_calls} / "
              f"tokens {res.total_prompt_tokens}+{res.total_completion_tokens} / {res.elapsed_ms} ms")
        print(f"  → 失败模式：{dict(Counter(res.failure_modes)) if res.failure_modes else '无'}")

    # ---- 汇总表
    print("\n\n" + "=" * 100)
    print("D2 压测汇总")
    print("=" * 100)
    print(f"{'ID':<4}{'场景':<26}{'步数':>4}{'工具':>5}{'tokens':>8}{'耗时(ms)':>10}  失败模式")
    print("-" * 100)
    agg: Counter = Counter()
    for t, r in results:
        fm = dict(Counter(r.failure_modes))
        agg.update(r.failure_modes)
        print(f"{t['id']:<4}{t['name']:<26}{len(r.steps):>4}{r.tool_calls:>5}"
              f"{r.total_prompt_tokens + r.total_completion_tokens:>8}{r.elapsed_ms:>10.0f}  {fm or '无'}")
    print("-" * 100)
    total_tokens = sum(r.total_prompt_tokens + r.total_completion_tokens for _, r in results)
    print(f"合计：{len(results)} 个场景，tokens {total_tokens}，"
          f"失败模式统计：{dict(agg) if agg else '无（说明压测还不够狠）'}")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
