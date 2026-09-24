"""agent_lab.eval_run —— 把 25 题跑过三个臂，产出"循环 vs 单轮"的对比数字。

================================================================================
【判分口径（先说清楚，免得数字被解读歪）】
一个回答被记为 **correct**，当且仅当：`Question.required()` 里**每一个**数都出现在回答文本里，
**并且**（非开放题时）回答里没有冒出授权集之外的大数。少任何一半都会放水：只查"说到没说到"，
无工具臂蒙对真数字也拿分；只查"有没有依据"，漏说关键数也算对。
开放题只查前半句 —— 它合法地会去查取数计划之外的事实，那些真数字会被授权集误判成编造。
  - 数字匹配复用 `report._variants()`：容忍千分位、0~3 位小数、`万/亿` 写法、
    以及小数↔百分数两种口径（退款率 0.13 与 13% 都算对）。
  - 要求"全都说到"而不是"说到一个就算对"：漏一个数说明它没把结论取全，这在业务上是缺陷。

**fabricated（编造）**：回答里出现了授权集之外、且量级 ≥ 1000 的数字。
  - 阈值 1000 是有意为之：把"11 月""两个平台""第 1 名"这类正常表述和
    "凭空报出 1,234,567 元"分开。不看量级的编造率会被日期和小整数灌满，变成没有信息量的数。
  - 授权集 = 该臂**实际看到过的**工具返回值。`no_tool` 臂看到 0 个工具结果，
    所以它答对的任何大额数字都记为编造 —— 那不是它会算，那是它猜中了。

【成本控制】25 题 × 3 臂 = 75 次运行、约 150 次 LLM 调用。所以：
  --facts-only  **一次 LLM 都不调**，只把 25 题的标准答案算出来打一遍。
                这是评测集自身的自检：题目写错了、口径变了，这一步就会当场露出来。
  --limit N / --ids Q01,Q07  只跑子集，先用 3 题验证链路再跑全量。
================================================================================
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

from agent_lab import p0_single_call, report  # noqa: E402
from agent_lab.eval_set import PREV_TOP, QUESTIONS, Question, by_id  # noqa: E402
from agent_lab.tools import call_tool  # noqa: E402


def env_candidates() -> list[Path]:
    """按顺序找 `.env`：本仓库 → `EVAL_ENV_FILE` 指定的路径 → 旁边的开发目录副本。

    为什么不能只找本仓库：`.env` 被 gitignore 了，clone 出来的副本里**根本没有这个文件**；
    而 `db.py` 的 `load_dotenv` 找不到不会报错，只会静默跳过，于是连库时以
    OS 用户名 + 空密码报 1045 —— 看着像密码错，其实是配置缺失。
    这里刻意不写任何人的绝对路径：换成环境变量或同级目录的猜测，别人 clone 也能用。
    """
    cands = [PROJECT_ROOT / ".env"]
    if extra := os.getenv("EVAL_ENV_FILE"):
        cands.append(Path(extra))
    cands.append(PROJECT_ROOT.parent / "ai-commerce-intelligence-platform" / ".env")
    return cands


def load_env() -> Path:
    """加载第一个存在的 `.env`；一个都没有就直接退出，把试过的路径打出来。"""
    cands = env_candidates()
    for path in cands:
        if path.exists():
            load_dotenv(str(path))
            return path
    raise SystemExit("❌ 没找到 .env，LLM key 与数据库口令都会缺失。已尝试："
                     + "、".join(str(p) for p in cands))


# 注意：**不在模块导入时调用** —— `.env` 被 gitignore 了，CI 上根本没有这个文件，
# 一导入就 SystemExit 会把整条 pytest 带崩。由 main() 显式调用。

FABRICATION_MIN_MAGNITUDE = 1000.0   # 见模块 docstring：低于此量级的数字不计入"编造"

# 年/月/时刻先抠掉再抽数字，否则 "2025 年 11 月" 会被当成两个未授权数字
CN_TIME_RE = re.compile(r"\d{4}\s*年|\d{1,2}\s*月|\d{1,2}\s*日")


def _first_dim_value(name: str, payload: dict) -> str | None:
    """取一次调用结果里"第一行"的维度值：breakdown 按 |delta| 排、rank 按指标值排。"""
    if name == "contribution_breakdown":
        return payload["breakdown"][0]["dim_value"]
    if name == "rank_dimension":
        return payload["top"][0]["dim_value"]
    return None


def gather_facts(q: Question) -> dict:
    """按题目的取数计划真实取数，返回 {f"{工具名}_{序号}": 返回值}。

    带 `PREV_TOP` 哨兵的参数：替换成**最近一次**调用第一行的维度值 ——
    所以取数顺序不能打乱，这类题也因此一次调用做不完（这正是 multi 桶的定义）。
    """
    merged: dict = {}
    prev_top: str | None = None
    for i, (name, args) in enumerate(q.calls):
        resolved = {}
        for k, v in args.items():
            if v == PREV_TOP:
                if prev_top is None:
                    raise ValueError(f"{q.id}: 第 {i+1} 步要用 PREV_TOP，但前面没有可依赖的取数步骤")
                resolved[k] = prev_top
            else:
                resolved[k] = v
        merged[f"{name}_{i}"] = call_tool(name, resolved)
        got = _first_dim_value(name, merged[f"{name}_{i}"])
        if got is not None:
            prev_top = got
    return merged


def _rich_variants(v: float) -> set[str]:
    """一个数的合法写法集合：0~2 位小数 + 万/亿 简写（只在量级够大时才给简写）。

    为什么要自己加一套而不直接用 `report._variants`：它只给 `f"{v/1e4:.2f}"`，
    于是模型写"约 1005.4 万元"（10,053,982.02 的常见写法）会被判成编造。
    简写只在 |v| 够大时才收录 —— 否则小数的 `.0f` 会退化成一堆容易撞车的整数。
    """
    out: set[str] = set()
    for nd in (0, 1, 2, 3):
        out |= {f"{v:.{nd}f}", f"{abs(v):.{nd}f}"}
    a = abs(v)
    if a >= 1e4:
        out |= {f"{v / 1e4:.{nd}f}" for nd in (0, 1, 2)}
    if a >= 1e8:
        out |= {f"{v / 1e8:.{nd}f}" for nd in (0, 1, 2)}
    if a <= 1:   # 只有 0~1 的"率"才需要百分数写法；给 1234.56 也收 ×100 会白放行 123456
        out |= {f"{v * 100:.{nd}f}" for nd in (0, 1, 2)}
    return out


def _authorized_pool(obj) -> set[str]:
    """把该臂看到过的所有数字摊平成"允许出现的写法"集合。"""
    pool: set[str] = set()
    for v in report._walk_numbers(obj):
        pool |= _rich_variants(v)
    return pool


def pool_from_texts(texts: list[str]) -> set[str]:
    """从 observation **原文**里抽授权集 —— 不要求它是合法 JSON。

    循环里被 `OBS_MAX_CHARS` 截断的 observation 解析不出 dict，
    之前那些**模型真看到过**的数字就整个丢了，于是真数字被判成编造（M07/M08/M10 全中）。
    授权的本义是"它眼睛看到过的东西"，所以按文本抽数就行。
    """
    pool: set[str] = set()
    for text in texts:
        for token in numbers_in(text):
            pool.add(token)
            try:
                pool |= _rich_variants(float(token))
            except ValueError:
                continue
    return pool


def _cleaned(text: str) -> str:
    """把年月日与时刻替换成空格，避免把它们误判成未授权数字。"""
    return CN_TIME_RE.sub(" ", report.TIME_RE.sub(" ", report.DATE_RE.sub(" ", text)))


def numbers_in(text: str) -> list[str]:
    """抽出回答里的所有数字 token（已去千分位）。"""
    return [t.replace(",", "") for t, _ in report.NUM_RE.findall(_cleaned(text))]


def answer_tokens(text: str) -> set[str]:
    """回答里出现过的数字，写成去千分位的 token 集合 —— 判"说到没说到"就靠它。"""
    return set(numbers_in(text))


def appears_in(tokens: set[str], value: float) -> bool:
    """`value` 是否以任何一种可接受写法出现在 `tokens` 里（千分位/小数位/万/亿/百分数）。"""
    return bool(_rich_variants(value) & tokens)


YEAR_MIN, YEAR_MAX = 1900, 2100


def is_year_like(token: str) -> bool:
    """四位整数年份不算"编造的业务数字"。模型写"2025 全年"不是在瞎报金额（S03 就这么被冤枉过）。"""
    return token.isdigit() and YEAR_MIN <= int(token) <= YEAR_MAX


def score(q: Question, answer: str, facts: dict, authorized: set[str]) -> dict:
    """给一个回答打分：说到必含数没有、有没有冒出授权集外的大数。

    两个池子**必须分开**，别图省事合并：
      "说到没说到" 比的是 `answer` 里的 token（模型说了什么）；
      "是不是编的" 比的是 `authorized`（它看到过什么，由调用方按臂取好）。
    早先的版本拿授权集去判"说到没说到"，于是只要臂拿到过工具，答什么都算对 ——
    这个 bug 是 `test_numberless_answer_is_not_correct` 抓出来的。

    Args:
        facts: 标准答案的取数结果 —— 只用来算 `required`，**不参与授权判定**。
        authorized: 该臂**实际看到过**的数字写法集合。`no_tool` 传空集，
                    于是它答出的任何大数都算编造（那不是会算，是猜中了）。
    """
    tokens = answer_tokens(answer)
    required = q.required(facts)
    missing = [v for v in required if not appears_in(tokens, v)]
    missing_names = [n for n in q.must_name(facts) if n not in answer]

    ok_pool = authorized | report.CONSTANT_ALLOWED
    invented = []
    for token in sorted(tokens):
        try:
            value = abs(float(token))
        except ValueError:
            continue
        if value < FABRICATION_MIN_MAGNITUDE or token in ok_pool or is_year_like(token):
            continue
        invented.append(token)
    strict_grounding = q.kind != "open"
    return {
        "required": required,
        "missing": missing,
        "missing_names": missing_names,
        # "对"= 该说的数都说了 **且** 链式定出来的实体名也报了 **且**
        # （非开放题时）没冒出没依据的大数。
        # 名字这一项专治"靠枚举蒙对"：未过滤的分解把所有商品的数都列出来，
        # 只比数字就会让一次调用的臂混过去。
        "correct": (not missing and not missing_names
                    and (not strict_grounding or not invented)),
        "invented": sorted(set(invented)),
        "grounding_strict": strict_grounding,
        "answer_chars": len(answer),
    }


def run_one(q: Question, arm: str) -> dict:
    """跑一题一臂：取标准答案 → 调模型 → 打分，返回一条可 JSON 化的记录。

    授权集按臂取，**一律取"该臂真正看到过的东西"**，不拿标准答案当授权：
      no_tool  → 空集，最严；
      one_tool → 它拿到的那一次工具返回值；
      loop     → 它每一步 observation 的**原文**（外加能解析成 JSON 的部分）。
    工具调用报错时授权集就是空 —— 那一刻它确实什么都没看到。
    """
    facts = gather_facts(q)
    res = p0_single_call.run(q.text, arm)
    if arm == "no_tool":
        pool: set[str] = set()
    elif arm == "loop":
        pool = pool_from_texts(res.get("observation_texts", [])) | _authorized_pool(res.get("authorized_list", []))
    elif res.get("authorized"):
        pool = _authorized_pool(res["authorized"])
    else:
        pool = set()
    verdict = score(q, res["answer"], facts, pool)
    return {
        "id": q.id, "kind": q.kind, "arm": arm, "question": q.text,
        "answer": res["answer"], "stop_reason": res["stop_reason"],
        "llm_calls": res["llm_calls"], "tool_calls": res["tool_calls"],
        "prompt_tokens": res["prompt_tokens"], "completion_tokens": res["completion_tokens"],
        # 把"它看到过什么"一起落盘：以后判分器再改，就能**离线重打分**，
        # 不必为了修一个判分 bug 再花一次全量的钱（这次就是这么被迫重跑的）。
        "saw": {"observations": res.get("observation_texts", []),
                "payload": res.get("authorized")},
        "elapsed_ms": res["elapsed_ms"], **verdict,
    }


def summarize(rows: list[dict]) -> dict:
    """按臂汇总：正确率（分题型）、平均工具调用数、平均 token、编造题数。"""
    out: dict = {}
    for arm in sorted({r["arm"] for r in rows}):
        sub = [r for r in rows if r["arm"] == arm]
        by_kind = {}
        for kind in sorted({r["kind"] for r in sub}):
            k = [r for r in sub if r["kind"] == kind]
            by_kind[kind] = f"{sum(1 for r in k if r['correct'])}/{len(k)}"
        out[arm] = {
            "n": len(sub),
            "correct": f"{sum(1 for r in sub if r['correct'])}/{len(sub)}",
            "by_kind": by_kind,
            "avg_tool_calls": round(sum(r["tool_calls"] for r in sub) / len(sub), 2),
            "avg_llm_calls": round(sum(r["llm_calls"] for r in sub) / len(sub), 2),
            "avg_tokens": round(sum(r["prompt_tokens"] + r["completion_tokens"] for r in sub) / len(sub)),
            "avg_elapsed_ms": round(sum(r["elapsed_ms"] for r in sub) / len(sub)),
            "invented_qs": sum(1 for r in sub if r["invented"]),
        }
    return out


PROBE_METRICS = ("revenue", "orders", "aov", "refund_rate", "discount_rate")
PROBE_DIMENSIONS = ("platform", "channel", "product")
PROBE_PERIODS = (("2025-01-01", "2025-12-31"), ("2025-10-01", "2025-10-31"),
                 ("2025-11-01", "2025-11-30"), ("2025-12-01", "2025-12-31"),
                 ("2025-01-01", "2025-06-30"), ("2025-07-01", "2025-12-31"))
PROBE_MONTH_PAIRS = (("2025-10", "2025-11"), ("2025-11", "2025-12"))
PROBE_TOP_NS = (1, 3, 5, 20)


def one_call_candidates() -> list[tuple[str, dict]]:
    """列出"一次调用"的全部候选（只走维度/指标/区间的合法组合，不猜库里的值）。

    候选里**不含**需要预先知道维度取值的过滤（如 dimension_value="微信公众号"）——
    那类参数值本身就是题目给的或上一步算出来的，一次调用拿不到"下一步才知道的值"。
    """
    out: list[tuple[str, dict]] = []
    for m in PROBE_METRICS:
        for a, b in PROBE_PERIODS:
            out.append(("query_metrics", {"metric": m, "start_date": a, "end_date": b}))
        for months in (3, 6, 12):
            out.append(("monthly_trend", {"metric": m, "months": months}))
    for d in PROBE_DIMENSIONS:
        for m in PROBE_METRICS:
            for a, b in PROBE_PERIODS:
                for n in PROBE_TOP_NS:
                    out.append(("rank_dimension", {"dimension": d, "metric": m,
                                                   "start_date": a, "end_date": b, "top_n": n}))
        for pa, pb in PROBE_MONTH_PAIRS:
            out.append(("contribution_breakdown", {"dimension": d, "period_a": pa, "period_b": pb}))
    return out


def build_candidate_pools() -> list[tuple[str, list[float]]]:
    """把全部"一次调用"候选各真跑一次，缓存成 [(标签, 该次返回里的所有数字), ...]。

    为什么要缓存：候选只取决于 (工具, 参数)，与题目无关。四百来个候选算一次就够，
    逐题重算会变成几千次查询 —— 那是几分钟到几十分钟的差别。
    """
    pools: list[tuple[str, list[float]]] = []
    for name, args in one_call_candidates():
        try:
            payload = call_tool(name, args)
        except Exception:  # noqa: BLE001
            continue       # 非法参数组合本来就跑不通，跳过
        pools.append((f"{name} {json.dumps(args, ensure_ascii=False)}",
                      report._walk_numbers({"_": payload})))
    return pools


def number_present(candidates: list[float], value: float) -> bool:
    """`value` 是否作为**一个数**出现在 candidates 里（容忍 ×100 / 万 / 亿的换算）。

    这里刻意不复用判分那套字符串写法：`report._variants` 会产出 `f"{v:.0f}"`，
    于是 0.05 和 0.13 都贡献一个 "0"，任何一题都能在小数字上撞出假见证 ——
    probe 第一版就是这么把 10 道 multi 全判成"一次可答"的。数字要比数字，不要比字符串。
    """
    for c in candidates:
        for scale in (1.0, 100.0, 1e-4, 1e-8):
            target = value * scale
            if abs(c - target) <= max(1e-9, abs(target) * 1e-6):
                return True
    return False


def probe_single_call(q: Question, pools: list[tuple[str, list[float]]]) -> list[str]:
    """返回能一次答对本体的调用清单。空列表 = 这题确实一次做不完。

    判据：标准答案的**全部** required 数字都出现在那一次调用的返回里。
    ⚠ 这个判据只看"数在不在"，不看"模型知不知道该挑哪个" —— 所以 required
      必须覆盖题干问到的每一个数，否则一题单值的 multi 必然被误判成一次可答
      （`rank_dimension` 里到处都有 0.05 这种数）。这条是 probe 的使用前提，不是 bug。
    """
    required = q.required(gather_facts(q))
    if not required:
        return ["<本题没有可判数字，probe 无意义>"]
    return [label for label, nums in pools
            if all(number_present(nums, v) for v in required)]


def scan_multi(questions: list[Question]) -> int:
    """`--probe-multi`：拿"一次调用"去撞每题。撞得上 multi 就是标签或题面写松了。

    `min_calls` 是我手判的，同一个毛病栽了两次（M06 一次、M04/M08 一次：
    `contribution_breakdown` 一次就返回所有平台两个月的 revenue，本身就是排名表）。
    所以不靠自觉，靠撞。

    **probe 的两条已知边界，写在这里而不是藏在代码里：**
      1. 值可达 ≠ 可回答。链式题（取数计划里用了 `PREV_TOP`）的"该挑哪一行"来自上一步，
         未过滤的排名/分解里当然含目标数，但模型无从知道是哪一个 —— 这类题**豁免**，
         只把见证数量打出来给人看，不判失败。
      2. 小整数撞数。`orders` 量级只有几十，一次商品排名里就会出现一串和别题答案相等的
         数；所以非链式 multi 的 required 尽量带**大数（元）或跨指标**，别只考一个小整数。
    """
    pools = build_candidate_pools()
    print(f"一次调用候选 {len(pools)} 个，逐题撞：")
    wrong = exempt = 0
    for q in questions:
        w = probe_single_call(q, pools)
        chained = any(PREV_TOP in args.values() for _n, args in q.calls)
        if chained:
            exempt += 1
            print(f"○ {q.id:4s} [{q.kind:6s}] 链式豁免（min_calls={q.min_calls}，"
                  f"一次调用含全部数的候选有 {len(w)} 个，但选哪行要靠上一步）")
            continue
        if q.kind == "multi" and w:
            wrong += 1
            print(f"✗ {q.id} 标 multi，但一次调用能答全：{w[0]}")
        elif q.kind == "single" and not w:
            wrong += 1
            print(f"✗ {q.id} 标 single，却撞不出任何一次调用（标签反过来错了）")
        else:
            print(f"✓ {q.id:4s} [{q.kind:6s}] min_calls={q.min_calls} "
                  f"{'一次可答' if w else '一次做不完'}")
    print("-" * 78)
    print(f"标签与事实不符 {wrong} 题；链式豁免 {exempt} 题")
    return 1 if wrong else 0


def print_facts_table(questions: list[Question]) -> int:
    """`--facts-only`：只把 25 题的标准答案算出来打一遍，一次 LLM 都不调。

    这一步的意义是**评测集自身的阳性对照**：如果某题的 required 为空、min_calls 与
    取数计划矛盾、或取数报错，说明题目或口径坏了 —— 那后面跑出来的正确率全都无意义。
    """
    bad = 0
    for q in questions:
        problems = []
        if not q.note and q.kind == "multi":
            problems.append("multi 没写『为什么一次拿不全』")
        if q.kind == "multi" and q.min_calls < 2:
            problems.append(f"multi 却标 min_calls={q.min_calls}")
        if q.min_calls > len(q.calls):
            problems.append(f"min_calls={q.min_calls} 大于取数计划 {len(q.calls)} 步")
        try:
            req = q.required(gather_facts(q))
        except Exception as exc:  # noqa: BLE001
            print(f"{q.id} [{q.kind}]  取数失败：{type(exc).__name__}: {exc}")
            bad += 1
            continue
        if not req:
            problems.append("required 为空，该题无法判分")
        if problems:
            bad += 1
        flag = ("  ← " + "；".join(problems)) if problems else ""
        vals = ", ".join(f"{v:,.2f}" for v in req)
        print(f"{q.id} [{q.kind:6s}] {len(q.calls)} 步/最少 {q.min_calls} 次  {vals}{flag}")
    print("-" * 78)
    print(f"共 {len(questions)} 题，最少调用合计 {sum(q.min_calls for q in questions)} 次，异常 {bad} 题")
    return 1 if bad else 0


def rescore_file(path: Path) -> int:
    """`--rescore <json>`：拿已有结果**离线重打分** —— 不调模型、不花一分钱。

    为什么必须有这条：判分口径改过一次就要重算 75 行，而重新调模型既花钱、
    答案还会漂移，等于拿新误差污染旧结论。所以 run_one 会把每个臂"看到过什么"
    写进 `saw`，这一条读回来重算即可。
    """
    d = json.loads(path.read_text(encoding="utf-8"))
    out_rows, errors = [], list(d.get("errors", []))
    for row in d["rows"]:
        q = by_id(row["id"])
        if q is None:
            errors.append(f"未知题号 {row['id']}")
            continue
        try:
            facts = gather_facts(q)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{row['id']}: 重算标准答案失败 {type(exc).__name__}: {exc}")
            continue
        saw = row.get("saw") or {}
        if row["arm"] == "no_tool":
            pool: set[str] = set()
        elif row["arm"] == "loop":
            pool = pool_from_texts(saw.get("observations") or []) | _authorized_pool(saw.get("observations") or [])
        else:
            pool = _authorized_pool(saw.get("payload")) if saw.get("payload") else set()
        verdict = score(q, row["answer"], facts, pool)
        out_rows.append({**row, **verdict})
    if not out_rows:
        print("没有可重打的行 —— 这个 JSON 里没有 rows")
        return 1
    summary = summarize(out_rows)
    path.with_name(path.stem + "_rescored.json").write_text(
        json.dumps({"rows": out_rows, "summary": summary, "errors": errors,
                    "rescored_from": path.name}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"重打结果写入 {path.with_name(path.stem + '_rescored.json')}")
    if errors:
        print(f"⚠ {len(errors)} 行未能重打")
        return 1
    return 0

def main() -> int:
    """CLI：`--facts-only` 自检，或按 `--arms/--ids/--limit` 真跑并落盘 JSON+CSV。"""
    ap = argparse.ArgumentParser(description="25 题 × 三臂评测")
    ap.add_argument("--arms", default="no_tool,one_tool,loop")
    ap.add_argument("--ids", default="", help="逗号分隔的题目编号，如 S01,M06")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（联调用）")
    ap.add_argument("--facts-only", action="store_true", help="只算标准答案，不调模型")
    ap.add_argument("--rescore", default="", help="离线重打一个已有结果 JSON（不调模型、不花钱）")
    ap.add_argument("--probe-multi", action="store_true",
                    help="拿'一次调用'去撞每题，检验 multi 标签（只连库，不调模型）")
    ap.add_argument("--out", default="eval_results", help="结果目录（默认 agent_lab/eval_results）")
    ns = ap.parse_args()
    load_env()

    questions = [by_id(s.strip()) for s in ns.ids.split(",") if s.strip()] if ns.ids else list(QUESTIONS)
    missing = [s for s, q in zip(ns.ids.split(","), questions) if q is None]
    if missing:
        print(f"未知题号：{missing}；可用编号 {sorted(q.id for q in QUESTIONS)}")
        return 2
    if ns.limit:
        questions = questions[: ns.limit]

    if ns.facts_only:
        return print_facts_table(questions)
    if ns.probe_multi:
        return scan_multi(questions)
    if ns.rescore:
        return rescore_file(Path(ns.rescore))

    rows: list[dict] = []
    errors: list[str] = []
    for arm in [a.strip() for a in ns.arms.split(",") if a.strip()]:
        for q in questions:
            try:
                row = run_one(q, arm)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{q.id}/{arm}: {type(exc).__name__}: {exc}")
                print(f"{q.id}/{arm} 运行异常：{type(exc).__name__}: {exc}")
                continue
            rows.append(row)
            mark = "OK " if row["correct"] else "MISS"
            inv = f" 编造{len(row['invented'])}" if row["invented"] else ""
            print(f"{q.id} [{arm:8s}] {mark} 工具{row['tool_calls']} "
                  f"tokens{row['prompt_tokens'] + row['completion_tokens']:6d} "
                  f"{row['elapsed_ms']:8.0f}ms{inv}")

    summary = summarize(rows)
    out_dir = Path(ns.out)
    if not out_dir.is_absolute():
        out_dir = Path(__file__).resolve().parent / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (out_dir / f"eval_{stamp}.json").write_text(
        json.dumps({"rows": rows, "summary": summary, "errors": errors},
                   ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    print("\n" + "=" * 78)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"结果已写入 {out_dir / f'eval_{stamp}.json'}")
    if errors:
        # 跑挂了不能报 0：上次 pilot 就是 6/6 全异常却打印"结果已写入"，看着像跑通了
        print(f"⚠ {len(errors)} 次运行异常，本次汇总不完整，退出码 1")
        return 1
    if not rows:
        print("⚠ 一条记录都没有 —— 别把空表当结果")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
