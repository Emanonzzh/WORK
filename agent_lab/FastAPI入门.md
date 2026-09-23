# FastAPI 入门（结合本项目真实代码）

> 写给"没写过 FastAPI"的人。读完你应该能：看懂 `api.py`、自己加一个接口、知道为什么某些写法会出事。
> 对应的代码文件：`agent_lab/api.py`

---

## 一、一句话理解

**FastAPI = 把普通 Python 函数变成 HTTP 接口的框架。**

```python
from fastapi import FastAPI
app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}
```

跑起来：

```bash
python agent_lab/api.py          # 或 uvicorn agent_lab.api:app --reload --port 8010
```

然后浏览器打开 **http://127.0.0.1:8010/docs** —— 你会看到一个**可以点着测试**的页面，
每个接口都有表单、能直接发请求、能看到返回。**这是 FastAPI 最大的卖点**，不用写一行前端。

> **为什么不是 8000**：本机 8000 被 C-Lodop 打印控件（`CLodopPrint32.exe`，开机自启）占用，
> 它绑的是 `0.0.0.0:8000`。我们在 `127.0.0.1:8000` 上**仍然能启动成功**（Windows 允许共存），
> 但请求会被那条通配 socket 抢走 —— `/docs` 打开是打印控件的页面，`/health` 还返回 200。
> 端口定义在 `agent_lab/api.py` 的 `PORT` 常量。

---

## 二、四个核心概念

### 1. 路由：一个函数 = 一个接口

```python
@app.get("/health")          # GET  http://host/health
def health(): ...

@app.get("/report/{period_b}")   # {period_b} 是"路径参数"，写在 URL 里
def get_report(period_b: str): ...

@app.post("/analyze")        # POST http://host/analyze
def analyze(req: AnalyzeRequest): ...
```

`@app.get` / `@app.post` 叫**装饰器**：它告诉 FastAPI"这个 URL 交给下面这个函数处理"。

### 2. 参数有三种来源（新手最容易混）

| 来源 | 写法 | 例子 | 在我们代码里 |
|---|---|---|---|
| **路径参数** | 写在 URL 路径里 | `/report/2025-11` | `get_report(period_b)` |
| **查询参数** | URL 里 `?a=1&b=2` | `/metrics?period=2025-11` | `get_metrics(period, metric, ...)` |
| **请求体** | POST 的 JSON | `{"question": "..."}` | `analyze(req: AnalyzeRequest)` |

**判断规则很简单**：函数参数是**基础类型**（str/int/bool）→ FastAPI 当成路径/查询参数；
参数是**你定义的类**（Pydantic 模型）→ 当成请求体。

### 3. Pydantic 模型：自动校验 + 自动生成文档

```python
class AnalyzeRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=200, description="自然语言问题")
    max_steps: int = Field(6, ge=1, le=10, description="Agent 最多走几步")
```

这一个类同时干了三件事：
1. **校验**：`question` 为空、超过 200 字、`max_steps=999` → 自动返回 **422**，你的函数根本不会被调用
2. **文档**：`/docs` 里自动显示字段名、类型、说明、默认值
3. **类型安全**：函数体里 `req.question` 一定是 `str`

> 对比一下：如果没有 Pydantic，你得自己写一堆 `if not data.get("question"): return 400`。

### 4. 状态码：什么情况返回什么（我们代码里的真实用法）

| 码 | 含义 | 我们什么时候用 |
|---|---|---|
| 200 | 成功 | 正常返回 |
| **422** | 参数校验失败（框架自动） | `period=2025-13` 被 `pattern` 挡住 |
| **400** | 客户端请求有问题（业务层） | 指标名不存在、月份越界 |
| **404** | 资源不存在 | `/reconciliation/2025-09` 文件还没生成 |
| **500** | 服务端出错 | 未预期的异常 |
| **503** | 服务不可用 | 数据库连不上（`/health` 里）、LLM 没配 Key |

```python
# 真实例子：/health 里，连不上库时返回 503 而不是 200 假装健康
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"数据库不可用：{exc}") from exc
```

---

## 三、本项目最重要的一个决策：用 `def` 而不是 `async def`

**这一条是新手最容易踩、面试最常问的点。**

```python
# ✅ 我们全部这样写
@app.get("/metrics")
def get_metrics(...):          # 普通函数
    return query_metrics(...)  # 这里是同步阻塞的 pymysql 查询
```

```python
# ❌ 危险的写法
@app.get("/metrics")
async def get_metrics(...):    # 异步函数
    return query_metrics(...)  # 里面却是同步阻塞代码！
```

**为什么危险**：
- FastAPI 跑在**单线程事件循环**上。它靠"遇到 I/O 就切去干别的"来实现高并发。
- 普通 `def`：FastAPI 会自动把它丢进**线程池**执行 → 阻塞的是线程池里的线程，事件循环照样转，其他请求照常响应。
- `async def` 里写同步阻塞代码：事件循环被**独占**住，**整个服务卡死**，所有其他请求排队等它跑完。

我们的 `/report/{period}` 要跑十几次 SQL + 异常检测，约 1~8 秒。
如果写成 `async def`，那几秒内**连 `/health` 都点不动**——探活失败，K8s 会以为实例挂了。

> 一句话记住：**同步阻塞代码就用 `def`，让线程池去扛；要用 `async def` 就必须配异步库**（如 `asyncpg` / `httpx`）。

---

## 四、我们这 7 个接口在干嘛

| 方法 | 路径 | 作用 | 快慢 | 教学点 |
|---|---|---|---|---|
| GET | `/health` | 探活：进程 + 数据库 + 数据量 | 快 | 503 的用法 |
| GET | `/metrics` | 查单个指标 | 快 | 查询参数 + `pattern` 边界校验 |
| GET | `/anomalies` | 查某月异常列表 | 中 | 复用分析内核，返回判据 |
| GET | `/report/{period_b}` | 生成经营报告（Markdown） | **慢（~1s+）** | 慢接口 + 返回对账结论 |
| GET | `/reconciliation/{period_b}` | 读对账产物文件 | 快 | 404 的用法 |
| POST | `/analyze` | 自然语言分析（调 LLM） | **慢且花钱** | 请求体模型 + 成本保险丝 |
| GET | `/attribution/{period_b}` | 多层下钻归因链 | 中（每层 2 次 SQL） | 返回体自带闭合判据，可自证 |

看全清单最快的方式：`GET /openapi.json`（`/docs` 页面就是用它渲染的）。

---

## 五、怎么自己测（三种方式 + 一个必踩的坑）

### 方式 1：`/docs` 页面直接点（最直观，推荐新手）

打开 http://127.0.0.1:8010/docs → 展开接口 → "Try it out" → 填参数 → Execute。

### 方式 2：curl

```bash
curl "http://127.0.0.1:8010/health"
curl -X POST "http://127.0.0.1:8010/analyze" \
     -H "Content-Type: application/json" \
     -d '{"question":"2025年11月的实付额是多少？","max_steps":4}'
```

### 方式 3：Python 脚本（**本项目推荐**）

```bash
python agent_lab/api_smoke_test.py            # 跑全部接口
python agent_lab/api_smoke_test.py --with-llm # 额外测 /analyze
```

### ⚠️ 必踩的坑：PowerShell 测中文接口会"假故障"

用 PowerShell 5.1 的 `Invoke-RestMethod` 测中文接口时：
1. **发送**：默认**不按 UTF-8** 编码 body → 中文变乱码 → 模型收到乱码问题，行为诡异（我们就遇到过"4 步乱查还没结论"）
2. **接收**：响应按 Latin-1 解码 → 中文显示成 `å®žä»˜é¢` 这种乱码

**这不是接口的 bug，是客户端的坑。** 正确写法：

```powershell
$json  = @{ question = "2025年11月的实付额是多少？" } | ConvertTo-Json
$bytes = [Text.Encoding]::UTF8.GetBytes($json)          # ← 关键：显式转 UTF-8 字节
Invoke-RestMethod -Uri "http://127.0.0.1:8010/analyze" -Method Post `
    -Body $bytes -ContentType "application/json; charset=utf-8"
```

> 用 Python/curl 测就没有这个问题 —— 所以本项目的冒烟测试用 Python 写。

---

## 六、动手练习（做完就真会了）

1. **加一个接口** `GET /top/{dimension}`：返回该维度实付额 TOP N（复用 `tools.rank_dimension`）；
   要求：`dimension` 用 `pattern` 限制成 `platform|channel|product`，非法值应返回 **422**。
2. 给 `/analyze` 加一个 `dimension` 字段，让它能把维度传给 Agent。
3. 把 `/report/{period_b}` 换成"先返回任务 id，再 `GET /report/{id}/status` 查状态" —— 想想为什么生产上要这么做。
4. 故意把 `/health` 写错（比如查询一个不存在的表），观察返回码是不是 503。

---

## 七、上生产还缺什么（面试主动说，别等被问）

| 缺什么 | 为什么 | 怎么做 |
|---|---|---|
| **鉴权** | 现在谁都能调 `/analyze`，会烧钱 | API Key / JWT，参考存量后端 `backend/routes/auth.py` |
| **限流** | 防刷 | 令牌桶；存量项目有 `rate_limiter.py` |
| **长任务异步化** | `/report` 让 HTTP 干等 1~8 秒，并发一高就顶不住 | 提交任务 → 返回 id → 轮询/回调（不必上 Celery，任务表+状态机即可） |
| **缓存** | 同一个月报告反复生成 | 按 `(period, dimension)` 缓存结果 |
| **结构化日志 + trace id** | 线上排障 | 每次请求记录耗时/参数/下游调用 |
| **依赖注入做配置** | `Depends` 管理 DB 连接池/配置 | 现在每次请求新建 pymysql 连接，生产应使用连接池 |
