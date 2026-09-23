"""agent_lab.api —— 把分析能力暴露成 HTTP 接口（FastAPI）。

================================================================================
【给不熟悉 FastAPI 的人：一句话理解】

FastAPI = "把普通 Python 函数变成 HTTP 接口"的框架。
你写一个函数 → 加一行装饰器 → 它就有了一个 URL，
而且框架会自动生成一个**可以点着测试**的文档页（/docs）。

【四个核心概念】（详见同目录《FastAPI入门.md》）
1. **路由**：`@app.get("/metrics")` 表示"GET /metrics 这个 URL 交给下面这个函数处理"
2. **参数三种来源**：
   - 路径参数：`/report/{period_b}` → 函数参数 `period_b`
   - 查询参数：`/metrics?period=2025-11` → 函数参数带默认值
   - 请求体：POST 的 JSON → 用 Pydantic 模型接收
3. **Pydantic 模型**：把请求/响应声明成类，FastAPI 自动做类型校验 + 自动写文档
4. **自动文档**：启动后打开 http://127.0.0.1:8010/docs 可直接点击调用

================================================================================
【本项目最重要的一个决策：用 `def` 而不是 `async def`】

我们的分析是**同步阻塞**的：pymysql 查询、报告渲染都要几秒。
- 写成 `def`：FastAPI 会自动把它丢到**线程池**执行，不占用事件循环 → 其他请求照常响应
- 写成 `async def` 却执行同步阻塞代码：**整个服务会被卡住**，其他请求全部排队等它

这是新手最常踩的坑，也是面试高频题。本文件的接口**一律用 `def`**，并在
`/analyze` 这类慢接口旁边标注"为什么这里不 async"。

启动：
    python agent_lab/api.py                 # 默认 127.0.0.1:8010（端口常量的原因见下方 PORT 注释）
    # 或 uvicorn agent_lab.api:app --reload --port 8010
然后浏览器打开 http://127.0.0.1:8010/docs
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from agent_lab import report as report_mod  # noqa: E402
from agent_lab.anomaly import detect  # noqa: E402
from agent_lab.attribution import drill_down  # noqa: E402
from agent_lab.db import query  # noqa: E402
from agent_lab.tools import METRICS, query_metrics  # noqa: E402

# ------------------------------------------------------------------ 端口
# 【踩坑记录 · 为什么不是默认的 8000】
# 本机 8000 被 **C-Lodop 打印控件**（`CLodopPrint32.exe`，开机自启，绑 `0.0.0.0:8000`）占着。
# 危险的地方在于它**不会报错**：Windows 允许我们再绑一个更具体的 `127.0.0.1:8000`，
# uvicorn 照样打印 "Uvicorn running on http://127.0.0.1:8000"，
# 但发往 127.0.0.1:8000 的请求会被那条通配 socket 抢走 ——
# 打开 `/docs` 看到的是打印控件的欢迎页，`/health` 还返回 HTTP 200（HTML），
# 于是任何"只看端口在不在监听 / 只看状态码是不是 200"的健康检查都会**误报成功**。
# 教训：健康检查必须校验**返回内容**，而不是端口状态或状态码。
PORT = 8010

# ------------------------------------------------------------------ 应用对象
# FastAPI() 创建的就是"这个 Web 应用"。title/description 会直接显示在 /docs 页面上。
app = FastAPI(
    title="销售经营分析 API",
    version="0.1.0",
    description=(
        "把 agent_lab 的分析能力暴露成 HTTP 接口。\n\n"
        "- `/metrics` 取单指标\n- `/anomalies` 取异常\n"
        "- `/report/{period}` 生成经营报告\n- `/analyze` 自然语言分析\n\n"
        "所有接口都是**只读**的（不接受写库请求）。"
    ),
)


# ------------------------------------------------------------------ Pydantic 模型
# 这些类的作用有两个：① 自动校验请求/响应 ② 自动生成文档里的字段说明
class AnalyzeRequest(BaseModel):
    """POST /analyze 的请求体。"""

    question: str = Field(..., min_length=1, max_length=200,
                          description="自然语言问题，例如：11 月销售涨了，是不是靠单量堆的？")
    max_steps: int = Field(6, ge=1, le=10, description="Agent 最多走几步")


class AnalyzeResponse(BaseModel):
    """POST /analyze 的响应体（只返回摘要，不返回完整轨迹）。"""

    answer: str = Field(..., description="Agent 的回答")
    stop_reason: str = Field(..., description="为什么停下来（给出答案 / 步数耗尽 / 预算耗尽）")
    steps: int = Field(..., description="走了几步")
    tool_calls: int = Field(..., description="调了几次工具")
    tokens: int = Field(..., description="消耗 token 总数（成本指标）")
    elapsed_ms: float = Field(..., description="耗时（毫秒）")
    failure_modes: list[str] = Field(default_factory=list, description="失败模式（空列表=无）")


class HealthResponse(BaseModel):
    """/health 的响应契约。

    存在的原因是本项目真踩过"HTTP 200 但服务是坏的"：端口被别的进程通配抢占时，
    探活请求拿到 200 却是别人的 HTML。所以健康检查必须**校验响应体字段**，
    而这个模型就是逼着调用方去看 `status` / `database`，而不是只看状态码。
    """
    status: str
    database: str
    orders_rows: int


# ------------------------------------------------------------------ 接口 1：健康检查
@app.get("/health", response_model=HealthResponse, summary="健康检查")
def health() -> dict:
    """探活用：进程活着 + 数据库连得上 + 数据在不在。

    为什么单列一个接口：部署后（Docker/Nginx/负载均衡）都靠它判断实例是否可用。
    """
    try:
        rows = query("SELECT COUNT(*) AS c FROM orders")[0]["c"]
        return {"status": "ok", "database": "ok", "orders_rows": rows}
    except Exception as exc:  # noqa: BLE001
        # 连不上库时返回 503（服务不可用），而不是 200 假装健康
        raise HTTPException(status_code=503, detail=f"数据库不可用：{exc}") from exc


# ------------------------------------------------------------------ 接口 2：单指标查询
@app.get("/metrics", summary="查询单个指标")
def get_metrics(
    metric: str = Query("revenue", description=f"指标名，可选：{list(METRICS)}"),
    period: str = Query("2025-11", pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="月份 YYYY-MM"),
    dimension: str | None = Query(None, description="可选维度：platform/channel/product"),
    dimension_value: str | None = Query(None, description="维度取值，配合 dimension 使用"),
) -> dict:
    """取某个指标在某个月的值（内部复用 tools.query_metrics，口径与 Agent 完全一致）。

    **校验放在接口层**：非法月份（如 2025-13）由 `pattern` 直接挡成 422，
    不会进到业务逻辑 —— 这叫"在边界处挡住脏数据"。

    【踩坑记录 · 接口测试抓出的 bug】
    最初 pattern 写成 `^\\d{4}-\\d{2}$`，它**放过了 2025-13**；
    而 `_month_range()` 又写在 `try` 外面，抛出的 ValueError 直接变成 **500**。
    修法两步：① 正则收紧到 01~12 ② 把可能抛异常的行移进 `try`、映射成 400。
    """
    try:
        start, end = report_mod._month_range(period)
        return query_metrics(metric, start, end, dimension, dimension_value)
    except Exception as exc:  # noqa: BLE001
        # 业务校验失败 → 400（客户端的问题），不是 500（服务端的锅）
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ------------------------------------------------------------------ 接口 3：异常查询
@app.get("/anomalies", summary="查询某月异常")
def get_anomalies(
    period: str = Query("2025-11", pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    dimension: str = Query("platform"),
    min_orders: int = Query(30, ge=0, le=1000, description="最小支持度门槛"),
) -> dict:
    """返回该月检出的日粒度异常列表（含判据：基线/偏离/robust_z/样本量）。"""
    try:
        items = [a.as_dict() for a in detect(dimension, freq="day", min_orders=min_orders)
                 if a.period.startswith(period)]
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"period": period, "dimension": dimension, "min_orders": min_orders,
            "count": len(items), "anomalies": items}


# ------------------------------------------------------------------ 接口 4：经营报告
@app.get("/report/{period_b}", summary="生成经营报告（Markdown）")
def get_report(period_b: str, period_a: str | None = None,
               dimension: str = Query("platform")) -> dict:
    """生成某月的经营报告，返回 Markdown 原文 + 对账结论。

    ⚠️ 这是**慢接口**（要跑十几次 SQL + 异常检测，约 3~8 秒）。
    这里故意用同步 `def`：FastAPI 会把它丢线程池，不会卡住 /health 等其他请求。
    生产上这种任务应该改成"提交任务 → 轮询结果"，而不是让 HTTP 请求干等。
    """
    try:
        analysis = report_mod.collect(period_a or _prev_month(period_b), period_b, dimension)
        view = report_mod.build_view(analysis)
        markdown = report_mod.render(view)
        text_check = report_mod.verify_numbers(markdown, analysis)
        db_check = report_mod.reconcile_db(analysis)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "period": f"{analysis['period_a']}->{analysis['period_b']}",
        "publishable": bool(text_check["passed"] and db_check["all_ok"]),
        "text_reconciliation": {"checked": text_check["checked"],
                                "violations": len(text_check["violations"])},
        "db_reconciliation": {"all_ok": db_check["all_ok"]},
        "markdown": markdown,
    }


# ------------------------------------------------------------------ 接口 5：对账结果
@app.get("/reconciliation/{period_b}", summary="读取某一期的对账结果文件")
def get_reconciliation(period_b: str) -> dict:
    """把 `reports/reconciliation_<period>.json` 读出来返回（演示"读产物"型接口）。"""
    import json

    path = report_mod.OUT_DIR / f"reconciliation_{period_b}.json"
    if not path.exists():
        # 资源不存在 → 404（而不是 500，也不是空对象）
        raise HTTPException(status_code=404,
                            detail=f"没有 {period_b} 的对账结果，请先调用 /report/{period_b}")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 接口 6：自然语言分析
@app.post("/analyze", response_model=AnalyzeResponse, summary="自然语言分析（调 LLM）")
def analyze(req: AnalyzeRequest) -> dict:
    """把自然语言问题交给手写 ReAct Agent，返回结论 + 成本摘要。

    为什么这个接口要单独限流/限步数：它**真的会花钱**（调 LLM）。
    `max_steps` 上限 10 是成本保险丝；生产上还应加鉴权 + 配额。

    【踩坑记录 · 全局状态竞态】
    原实现是 `react.MAX_STEPS = min(req.max_steps, MAX_STEPS)`，跑完再在 finally 里还原。
    单请求正确，但同步 `def` 接口跑在 FastAPI 的**线程池**里，并发请求会交错：
    后到的请求读到的是前一个请求改过的值，还原顺序也会互相覆盖 ——
    A 请求设了 3 步，B 请求进来时 `old` 读到的是 3，B 还原时就把全局写回 3，
    A 那边则可能按 B 的 10 步跑满（**多花钱**）。
    修法：上限作为 `run_agent(max_steps=...)` 参数传入，全局常量只当默认值，不再被改写。
    """
    from agent_lab.p1_react import MAX_STEPS as STEP_CAP
    from agent_lab.p1_react import run_agent

    try:
        result = run_agent(req.question, verbose=False,
                           max_steps=min(req.max_steps, STEP_CAP))
    except SystemExit as exc:                      # 没配 LLM_API_KEY 时 call_llm 会 SystemExit
        raise HTTPException(status_code=503, detail=f"LLM 未配置：{exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"分析失败：{exc}") from exc
    return {
        "answer": result.answer or "（未得出结论）",
        "stop_reason": result.stop_reason,
        "steps": len(result.steps),
        "tool_calls": result.tool_calls,
        "tokens": result.total_prompt_tokens + result.total_completion_tokens,
        "elapsed_ms": result.elapsed_ms,
        "failure_modes": result.failure_modes,
    }


# ------------------------------------------------------------------ 接口 7：多层下钻归因
@app.get("/attribution/{period_b}", summary="多层下钻归因链（总量 → 平台 → 该平台内商品）")
def get_attribution(
    period_b: str,
    period_a: str | None = None,
    levels: str = Query("platform,product", description="逗号分隔的维度链，如 platform,product"),
    top_n: int = Query(1, ge=1, le=3, description="每层继续往下钻的分支数"),
) -> dict:
    """逐层下钻：每层取 |delta| 最大的分支，作为下一层的过滤条件。

    与 `/report` 的分工：这里**只跑分解**，不生成报告也不做对账，所以是快接口（每层两次 SQL）。
    链条闭合判据：`chain[i].subset_total_delta` 必须等于 `chain[i-1].top[0].delta`，
    且每层 `decompose_check` 为 0 —— 不成立就说明下钻链断了。
    """
    try:
        return drill_down(period_a or _prev_month(period_b), period_b,
                          tuple(levels.split(",")), top_n)
    except Exception as exc:  # noqa: BLE001  含 ToolError：维度非法/层级重复/不成对都回 400
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _prev_month(period: str) -> str:
    """'2025-11' → '2025-10'（跨年时回退到上一年 12 月）。"""
    y, m = int(period[:4]), int(period[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


if __name__ == "__main__":
    import uvicorn

    # reload=False：生产要 False（改代码不自动重启）；开发可设 True 边改边生效
    uvicorn.run(app, host="127.0.0.1", port=PORT, reload=False)
