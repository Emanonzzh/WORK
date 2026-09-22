# 销售经营分析 Agent · 项目框架

> ⚠️ **编号提醒**：简历上这是**项目一**；仓库早期文档因历史原因称它"项目二"。本文一律用项目名，不提编号。
> 状态标注：✅ 已实现**并有落盘产物可查** ｜ 🟡 代码在但未端到端验证 ｜ ⬜ 不存在
> **最后核对：2026-09-23**（按磁盘文件 + 实跑结果核对，非记忆。核对方法见**第十二节**，照着复跑一遍即可）

---

## 一、整体框架（现状标注）

```
┌─────────────────────────────────────────────────────────────────────┐
│ ① 交互层                                                      ✅     │
│    agent_lab/streamlit_app.py 310 行 · 看板 5 页签：                  │
│      量价归因(瀑布图) · 维度下钻 · 异常发现 · 报告与对账 · 自然语言问数 │
│    agent_lab/api.py 251 行 · 6 条路由：                               │
│      GET  /health  /metrics  /anomalies                              │
│          /report/{period_b}  /reconciliation/{period_b}              │
│      POST /analyze   （唯一调 LLM 的入口）                            │
│    冒烟：api_smoke_test.py(7 项) + ui_smoke_test.py(官方 AppTest 无头)│
├─────────────────────────────────────────────────────────────────────┤
│ ② 编排层                                                      ✅     │
│    实验线：手写 ReAct，不依赖框架 → agent_lab/p1_react.py 411 行      │
│      步数上限 / token 预算 / 重复动作检测 / observation 截断 /         │
│      错误回传 / 全轨迹(每步 thought·action·args·observation·耗时·token)│
│    主线：确定性流水线 report.py 432 行 —— 已实现，但**不是 LangGraph** │
│      collect → build_view → render → verify_numbers → reconcile_db   │
│      → llm_narrative   （LLM 只在最后一环）                           │
├─────────────────────────────────────────────────────────────────────┤
│ ③ 分析层                                                      ✅     │
│    tools.py 466 行 · 4 个工具 + 口径表 + 参数白名单：                  │
│      query_metrics / monthly_trend / rank_dimension                  │
│      contribution_breakdown（量价三因子分解，decompose 在模块级可测）  │
│    anomaly.py 283 行 · 同星期几基线 + 滚动中位数 + MAD 稳健 z(3.5)     │
│      + 最小支持度 + 脉冲/漂移两类                                     │
│    归因下钻：DIMENSIONS 白名单 3 维（platform/channel/product）        │
│    report.py + templates/business_report.md.j2（Jinja2 → Markdown）   │
├─────────────────────────────────────────────────────────────────────┤
│ ④ 数据层                                                      ✅     │
│    MySQL 8.4.9 便携版 · 库 ai_commerce_intelligence_platform          │
│      orders 102,287 行 / orders_injected 102,287 行 / 2025-01-01~12-31│
│    KPI 口径随工具返回值一并返回；数据能力盘点（无成本字段、无同比、     │
│      无区域/销售员维度 —— 所以不做假装有数据的分析）                   │
├─────────────────────────────────────────────────────────────────────┤
│ ⑤ 评测层                                                      ✅     │
│    异常检测 P/R/F1（注入集 + 差分口径 + 事件化后处理）                 │
│    数字对账双级（文本级 + 数据库级）+ **对账器自测**                   │
│    pytest 230 项（不连库、不调模型）                                  │
│    6 场景故障注入压测 p1_stress.py 144 行                             │
│    ⬜ 单轮基线 p0_single_call.py（"循环 vs 单轮"对比数字还没有）        │
│    ⬜ 25 题评测集（单步10/多步10/开放5）                              │
├─────────────────────────────────────────────────────────────────────┤
│ ⑥ 部署层                                                      🟡/⬜  │
│    存量 docker-compose.yml 216 行 / 7 服务 —— **只在开发目录**，       │
│      且是别人的代码；端到端从未跑过 → 对外不得说"已容器化"             │
│    ⬜ CI（本仓库无 .github/workflows）                                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 二、两条线（本项目最重要的设计）

| | 主线 | 实验线 |
|---|---|---|
| **目的** | 求职作品：结论可复现、可对账 | 学 Agent：把每种机制亲手实现一遍 |
| **形态** | 确定性流水线，LLM 只在 narrate | 手写 ReAct 循环（不依赖框架） |
| **为什么** | 分析类任务 90% 步骤有唯一正确答案，不该交给模型 | 先搞清框架替你做了什么，再决定用不用它 |
| **共用** | 同一套 `tools.py`、同一个库、同一套对账 | 同左 |
| **现状** | ✅ `report.py` 已是这条线（未用 LangGraph） | ✅ 已跑通并有落盘轨迹 |

> 原计划"主线用 LangGraph 确定性 DAG"。**实际没引入 LangGraph**——`report.py` 的
> `collect→…→llm_narrative` 本身就是个确定性 DAG，只是用函数调用而非图框架表达。
> 面试被问"为什么不用 LangGraph"，诚实答案是：**这条链路没有分支决策需要图来编排，
> 引入框架只会增加我解释不了的部分**。想要"用过框架"的证据在另一个项目（项目二用了 LangGraph `create_agent`）。

---

## 三、代码住在哪（三处，搞错就会分叉）

| 位置 | 内容 | git 状态 |
|---|---|---|
| **本仓库 `agent_lab/`** | 源码 + `tests/` + 落盘产物（`eval/` `reports/` `templates/`） | ✅ **唯一版本化副本**，一切以此为准 |
| 开发目录 `ai-commerce-intelligence-platform/agent_lab/` | 同上，用于跑（那边有 `.venv` 与数据环境） | ❌ **未跟踪**（`git ls-files` 为空），不受版本控制 |
| 公开作品仓库 | 本仓库的干净导出（无求职材料） | ⚠️ 快照，不自动同步 |

2026-09-22 核对：两份 `agent_lab/` **逐文件完全一致**（`diff -rq` 排除 `__pycache__` 后零差异）。
**改完必须双向同步**，否则分叉。开发目录那个仓库的 remote 是 `super-ZXQ/...`，不是本人的。

存量（不是我写的）里**仍不在本仓库**的：`sql/02_import_data.sql`(原版)、`sql/03_analysis.sql`、
`data/cleaned_orders.csv`(16MB)、`backend/`(32 接口)、
`agent_core/`(Text-to-SQL)、`ai-ecommerce-assistant/`(RAG)、`streamlit_app.py`(存量 BI)、
`docker-compose.yml`、`deploy/` —— 全部只在开发目录。

**09-23 起已进本仓库**（为了让人 clone 完能跑）：`sql/01_create_table.sql`（署名摘自上游，表名保持 `orders`
所以分析代码零改动）、`sql/02_import_data.sample.sql`、`agent_lab/make_sample_data.py`、
`agent_lab/load_sample_data.py`，以及 README 的"三步建样例库"一节。
真实那 102,287 行**仍然不分发**——数据集属上游 MIT 项目，且 16MB 不该进作品库。

---

## 四、一次完整请求的数据流（2026-09-22 实跑复核）

以 **"11 月销售涨了，是不是靠单量堆的？"** 为例：

```
用户问题
   ↓
p1_react.run_agent(question, max_steps=...)                        ✅ 请求级参数（见第七节 8）
   ├─ Step 1 · LLM 决策
   │    Action: contribution_breakdown
   │    Action Input: {"dimension":"platform","period_a":"2025-10","period_b":"2025-11"}
   │         ↓ tools.call_tool()
   │      ① 参数白名单（维度枚举 / 月份 / 年份 / 日期语义）          ✅ 实跑反证：传 region → ToolError
   │      ② 只读 SQL（仅 SELECT）→ MySQL 102,287 行                 ✅ 实跑
   │      ③ 代码计算：量 / 价 / 交互 + 加总校验                      ✅ 实跑 check=0.0
   │      ④ 返回口径（definition/unit）+ reading_guide              ✅
   │         ↓ Observation 回传（>1800 字符截断）
   ├─ Step 2 · Final Answer：引用工具给的数字，不自己算
   ↓
结果 + 完整轨迹                                                     ✅
```

**2026-09-22 本机实跑 `contribution_breakdown` 的输出**（与 09-16 生成的报告逐位一致）：

```
total_delta           1,389,478.21     total_mom_pct   +15.92
total_volume_effect   2,020,698.05     total_price_effect  -512,555.14
total_interaction      -118,664.70     total_decompose_check   0.0
```

**`/analyze` 端到端跑过的证据**（`F:\soft\api_analyze_test.txt`，2026-09-16 01:54）：
问"2025年11月的实付额是多少？" → 步数 2 / 工具调用 1 / 1406 tokens / 1500ms /
答 10,117,593.28 元并附口径说明。该文件同时留了一次**中文编码问题的复现记录**（响应 mojibake），
现已由 `api_smoke_test.py` 的中文 UTF-8 用例覆盖。

**关键分工**：整条链路 LLM 只出现在 Step 1、Step 2 与 `llm_narrative`；取数、校验、计算、加总校验全是代码。

---

## 五、模块职责与现状

| 模块 | 行数 | 职责 | 现状 |
|---|---|---|---|
| `tools.py` | 466 | 业务问题 → 只读 SQL，口径统一 | ✅ |
| `p1_react.py` | 411 | 多步编排：模型决定查什么、查几次 | ✅ |
| `report.py` | 432 | 报告渲染 + **双级对账** + 对账器自测 | ✅ |
| `evaluate_anomaly.py` | 315 | 差分口径 + 事件化的 P/R/F1 评测 | ✅ |
| `streamlit_app.py` | 310 | 看板 5 页签 | ✅ |
| `anomaly.py` | 283 | 时间序列异常检测 | ✅ |
| `api.py` | 251 | 6 条 HTTP 路由 | ✅ |
| `p1_stress.py` | 144 | 6 场景故障注入 | ✅ |
| `inject_anomalies.py` | 126 | 注入 6 个已知异常，产 ground truth | ✅ |
| `api_smoke_test.py` / `ui_smoke_test.py` | 120/73 | 接口与 UI 冒烟 | ✅ |
| `db.py` | 69 | 统一连接 | ✅ |
| `tests/` | 892（5 文件 56 个 test 函数） | 单测，不连库不调模型 | ✅ **230 项，09-22 复跑通过** |
| `attribution.py` | — | 自动多层下钻（总量→平台→商品→时间） | ⬜ 单维分解已有，**串成自动链路缺** |
| `p0_single_call.py` | — | 单轮调用基线 | ⬜ |
| 25 题评测集 | — | 回归评测 | ⬜ |

---

## 六、技术栈（分层 + 现状）

| 层 | 技术 | 现状 |
|---|---|---|
| 数据 | MySQL 8.4.9 便携版（免管理员）、`LOAD DATA`、索引、只读账号 | ✅ |
| 分析 | Python 纯函数、SQL 聚合、量价三因子分解、MAD 稳健统计 | ✅ |
| Agent | 手写 ReAct（原生 HTTP 调 LLM）、工具 schema、停止条件、错误回传、全轨迹 | ✅ |
| LLM | DeepSeek（OpenAI 兼容协议，`deepseek-chat`），key 走 `.env` | ✅ |
| 服务 | FastAPI + Pydantic 响应模型；`def` 而非 `async def`（同步阻塞丢线程池） | ✅ 本机可跑 |
| 前端 | Streamlit + Plotly | ✅ |
| 测试 | pytest 230 项 + 官方 `AppTest` UI 冒烟 + 接口冒烟 | ✅ |
| 编排 | LangGraph | ⬜ **本项目未用**（别和另一个项目混着说） |
| 部署 | Docker Compose 7 服务（存量） | 🟡 未端到端跑过 |
| CI | GitHub Actions | ⬜ |

---

## 七、关键设计决策（面试被问"为什么"的答案）

1. **为什么不让 LLM 自己算数？** 代码算的可复现、可对账；模型只负责"理解问题"和"表达结论"。
2. **为什么工具返回值要带口径？** 避免同一个问题两次问出两个数；口径随值返回，模型直接引用不推断。
3. **为什么量价分解是三因子不是两因子？** `ΔR = 量 + 价 + 交互`，三项**精确加总**才能断言校验（实测闭合 0.0）。少一项就变近似，无法验证。
4. **为什么工具只读 + 参数白名单？** Agent 安全第一层。且**错误信息越明确，模型自我修正越可靠**——实测 `2025-13` 被放过时，模型只能自己推断"13 月不存在"。列名不能参数化，所以只能白名单。
5. **为什么手写 ReAct 不上框架？** 先知道自己实现会踩什么坑（重复检测误伤重试、上下文膨胀、畸形输出），才能在框架上做出正确选型。
6. **为什么不硬塞 RAG？** 结构化数据分析的正确工具是 SQL 和代码。RAG 在本项目只保留一个用途：口径说明与政策依据的引用。
7. **为什么砍掉区域/销售员维度？** 数据里根本没有这两个字段（能力盘点实测）。不做假装有数据的分析。
8. **为什么 `max_steps` 要是参数而不是模块常量？** 见第九节缺陷 3——这是本项目最能讲的一个。
9. **为什么"测试通过"本身不是证据？** 除非它曾经失败过。把修复临时还原重跑 = 15 failed + 1 collection error；恢复后 = 230 passed。falsification 才是测试有没有价值的分界线。

---

## 八、与存量系统的边界（面试必答："哪部分是你写的"）

| 类别 | 内容 |
|---|---|
| **我新增** | `agent_lab/` **全部 18 个 py 文件 / 3903 行**（源码 3011 + 测试 892）、`templates/business_report.md.j2`、`sql/02_import_data.windows.sql`（仅改路径，原文件未动）、本机 MySQL 8.4.9 便携版部署与 102,287 行导入、`.env` 配置 |
| **我修改** | `ai-ecommerce-assistant/eval/run_sql_eval.py`（修 `.env` 加载顺序 bug：先读 `os.environ` 才 `load_dotenv`，CLI 裸环境必报"Key 未配置"） |
| **存量复用（不是我写的）** | `agent_core/*`、`backend/*`(32 接口)、`ai-ecommerce-assistant/*`、`streamlit_app.py`(存量 BI)、`docker-compose.yml`、`deploy/*`、`sql/01~03`、`data/cleaned_orders.csv` |

> 话术："我在一个已跑通的开源企业级项目上做增量演进——它有数据、接口、部署、评测，但**只有单轮问数，
> 没有'发现异常→下钻→归因→报告'的分析闭环**。我补的是分析内核和 Agent 机制这两块。"
> ⚠️ 不得说：已上线、已容器化、"4 个纯函数工具"（它们会查 MySQL）、把注入集的指标说成真实业务准确率。

---

## 九、已定位并修好的缺陷（可直接当故事讲）

1. **异常检测第一版 175 个误报** → 根因是**周内效应**（拿周六比周一）。修法是同星期几基线，不是调阈值。
2. **评测方法本身错了**：P 只有 35% 时先逐条归因，发现"误报"全是同一事件的多次检出 →
   加**事件化后处理**（同维度、间隔 ≤7 天合并）+ **差分口径**（干净表 vs 注入表相减，剔除元旦暴涨这类真实业务事件的噪声地板）。
3. **`/analyze` 并发竞态**：原实现临时改写模块全局 `MAX_STEPS` 再还原，而同步 `def` 跑在线程池里 →
   A 请求会按 B 请求设的步数跑满（**真的多花钱**），`finally` 的还原顺序还互相覆盖。
   改 `run_agent(max_steps=...)` 请求级参数；`test_concurrent_runs_keep_their_own_step_limit` 防回退。
4. **`_check_date` 只校验格式不校验语义**：`2025-02-31` 被放行 → 改 `calendar.monthrange` 按当月实际天数。
5. **`decompose` 原是嵌套函数**（import 不到 = 不可测）→ 提到模块级并断言加总闭合。
6. **端口 8000 被静默抢占**：`CLodopPrint32.exe` 绑 `0.0.0.0:8000`，Windows 仍允许再绑 `127.0.0.1:8000`，
   uvicorn 照常打印"running"，但 `/health` 返回 **HTTP 200 + HTML**。→ 端口改 8010，健康检查改为**校验响应体**。
   **教训：200 不是证据。**

---

## 十、评测结果（全部有落盘文件，可当场打开）

| 指标 | 值 | 证据 |
|---|---|---|
| 异常检测（事件化，主指标） | **P 54.55% / R 100% / F1 70.59%**，TP6 FP5 FN0 | `agent_lab/eval/anomaly_report.md` |
| 严格一对一（对照口径） | P 35.29% / R 100% / F1 52.17% | 同上 |
| 原始口径（含真实事件，偏悲观） | P 15.79% / R 100% / F1 27.27% | 同上 |
| 注入事件检出 | **6/6**；"与所有注入都无关"的误报 **0** | 同上 |
| 门槛取舍实验 | `min_orders` 10/30/50/80 四档；提到 80 开始漏检 | 同上 |
| 文本级对账 | 报告内 **88 个数字，0 违规**，pass_rate 1.0 | `reports/reconciliation_2025-11.json` |
| 对账器自测 | 注入假数字 `999,999.99` → `caught: true` | 同上（验证了验证器本身） |
| 数据库级复算 | `all_ok: true`，逐指标 `abs_diff = 0.0` | 同上 |
| 量价分解闭合 | `total_decompose_check = 0.0` | 09-22 本机实跑复核 |
| 单元测试 | **230 passed / 0 failed / 0.34s** | 09-22 本机复跑（不连库、不调模型） |

**已知局限（面试主动说，别等被问）**：
① 注入异常是人工构造的，幅度偏理想化，真实异常更隐蔽；
② 只评了 platform 维度（其他维度日样本量达不到最小支持度）；
③ 事件匹配容差 ±7 天偏宽会高估查全率，所以同时给严格口径对照；
④ 真实数据只有 1 年（2025 全年），做不了同比基线，只能用周内效应 + 滚动中位数；
⑤ **未做多重比较校正（FDR）**——维度组合一多，误报率必然上升。

---

## 十一、缺口清单（按优先级）

| 优先级 | 任务 | 预估 | 为什么是它 |
|---|---|---|---|
| **P0** | `p0_single_call.py` 单轮基线 + 25 题评测集，跑出"循环 vs 单轮"对比 | 半天 | 简历上**唯一还没有的对比数字**；数据、模型、工具今晚全就绪 |
| ~~P0~~ ✅ | ~~让公开仓库能被别人跑起来~~ **09-23 已做**：`sql/01_create_table.sql` + 样例生成器 + INSERT 载入器 + README 三步 | 已交付 | 实测跑通到 KPI 层；`LOAD DATA` 那条因需全局 FILE 权限而放弃（缺权限回 1045，易误判成密码错） |
| P1 | 自动多层下钻（把单维分解串成 总量→平台→商品→时间） | 半天 | 简历写了"下钻"，现在只有单维 |
| P1 | GitHub Actions 跑 pytest | 1 小时 | 230 项不连库不调模型，**天生适合 CI**，成本极低 |
| P2 | Compose 端到端 | 半天~1天 | 卡在网络与 Docker Desktop；做完才能改"已容器化"的表述 |
| P2 | README 三张演示截图 | 30 分钟 | 公网地址无 TLS，手机打开会失败，截图是兜底 |
| P3 | HITL 审批 / 多 Agent / 行级权限 | — | 面试谈资，非必需 |

---

## 十二、怎么复跑这份核对（防止它再次悄悄过期）

**解释器是最大的坑**：`F:\python\python.exe` 是 **Python 3.14，项目依赖一个都没装**。
必须用开发目录的 venv（3.12.4，含 pymysql 2.2.8 / pytest 9.1.1 / jinja2 / fastapi / uvicorn / dotenv）：

```
F:\Python\gongc\ai-commerce-intelligence-platform\.venv\Scripts\python.exe
```

```bash
# ① 单测（不需要 MySQL、不需要 LLM，0.34 秒）
cd /f/Python/gongc/rs-ai-assistant
<venv>/Scripts/python.exe -m pytest agent_lab/tests -q

# ② 数据在不在（3306 要起来；MySQL 便携版见 F:\soft\mysql-start.ps1）
cd agent_lab && <venv>/Scripts/python.exe -c "
from dotenv import load_dotenv; load_dotenv(r'F:\Python\gongc\ai-commerce-intelligence-platform\.env')
import tools; c=tools._connect().cursor(); c.execute('SELECT COUNT(*) AS c FROM orders'); print(c.fetchone())"

# ③ 分解闭合校验（本核对用的就是这条）
#    tools.call_tool('contribution_breakdown', {'dimension':'platform','period_a':'2025-10','period_b':'2025-11'})
#    → 看 total_decompose_check 是否为 0.0

# ④ 样例数据链路（不需要真实数据；本仓库自带的可复现路径）
<venv>/Scripts/python.exe agent_lab/make_sample_data.py --rows 1000 --out sample_orders.csv
<venv>/Scripts/python.exe agent_lab/load_sample_data.py --csv sample_orders.csv --table <临时表>
```

⚠️ 不 `load_dotenv` 就连接，会以 OS 用户名 + 空密码报 **1045 Access denied**——这是配置缺失，不是数据丢了。
⚠️ **MySQL 有两种失败都回 1045，靠报错里的用户名区分**：
`'123'@'localhost' (using password: NO)` = 没加载 `.env`；
`'commerce'@'%' (using password: YES)` = 密码是对的，但**权限不够**——典型就是 `LOAD DATA INFILE` 要全局
`FILE` 权限，而它不属于库级 `ALL PRIVILEGES`。把后者当成密码错，今晚在这里绕了两轮。
⚠️ `TRUNCATE` 要排他元数据锁：同表若另有未提交事务会**无限等且零输出**；先 `SET SESSION lock_wait_timeout=8`。
⚠️ ② 里的 `tools._connect` 自 09-23 起是 `agent_lab.db.connect` 的别名（连接层已统一），行为不变。
⚠️ Windows 控制台是 cp936：脚本输出保持纯 ASCII，或先 `PYTHONIOENCODING=utf-8`，否则中文乱码、`✓` 直接抛异常。

---

## 附：本次订正了什么（2026-09-22 vs 上一版 09-14）

上一版把下面 **7 项标成 ⬜ 未开始，实际全部已完成且有产物**：
`anomaly.py`、`report.py` + Jinja2 模板、FastAPI 6 条路由、Streamlit 看板、pytest 单测、
异常检测 P/R/F1 评测、数字对账。
上一版确实说对了的只有 3 项：`p0_single_call.py`、25 题评测集、`attribution.py` 自动多层下钻。
"我新增 `agent_lab/` 全部 4 个文件（786 行）" → 实际 **18 个文件 3903 行**。
另外补上两条上一版没写的事实：主线**并未使用 LangGraph**；存量文件**不在本仓库**，
导致公开仓库无法独立运行（已列为 P0 缺口）。
