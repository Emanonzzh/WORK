# 复现指南：ai-commerce-intelligence-platform（逐文件讲解 + 步骤清单）

> 项目：[super-ZXQ/ai-commerce-intelligence-platform](https://github.com/super-ZXQ/ai-commerce-intelligence-platform)（MIT）
> 定位：**基于 102,287 条真实电商订单的智能数据决策平台**——BI 看板 + LangGraph Text-to-SQL Agent + RAG 业务知识库 + FastAPI 32 个接口 + RFM 用户画像
> 为什么选它：**真实数据（仓库自带 `data/cleaned_orders.csv`，16MB / 102,287 行）+ 真实业务（电商销售分析）+ 完整工程化（Docker/Nginx/CI/244 测试/评测体系）**
> 对你的价值：一次性补上 **Text-to-SQL、FastAPI、MySQL、Docker、评测** 五个 JD 高频缺口

---

## 一、先看架构（README 里的核心链路）

```
用户提问
 → 输入安全检查
 → 载入最近 6 轮会话（thread_id 隔离，TTL 30 分钟）
 → 确定性意图路由
     ├─ blocked       → 安全拒绝（隐私/注入/写库）
     ├─ clarification → 反问澄清
     ├─ knowledge     → Chroma RAG Top3（不碰数据库）
     ├─ data          → 读 Schema → 生成 SQL
     └─ hybrid        → 先 RAG 再 Schema/SQL
                        ↓
                SQLGlot AST 只读校验（禁多语句/写操作/锁表/SLEEP/BENCHMARK/LOAD_FILE）
                        ↓
                LIMIT 500 + 10 秒超时（MySQL 侧 MAX_EXECUTION_TIME）
                        ↓
                只读账号执行 → 最多一次 SQL 纠错
                        ↓
                答案合成 + 来源引用 → 保存会话与公开轨迹
```

**三种问法的走向**（面试必答）：
- 定义/公式/规则（"复购率怎么算"）→ **只走 RAG**，不碰数据库
- 数量/排名/趋势（"各平台销售额 TOP10"）→ **只走 SQL**
- 口径 + 实际值（"按我们的口径，各平台退款率"）→ **hybrid：先 RAG 拿口径，再 SQL 取数**

---

## 二、逐文件讲解（按"该读的顺序"排）

### 第 0 层：先读文档（30 分钟）
| 文件 | 看什么 |
|---|---|
| `README.md` (36KB) | 全局：功能表、架构图、**关键决策与取舍**、A/B 评测数据 |
| `DESIGN.md` / `PRODUCT.md` | 设计取舍与产品定义（面试讲"为什么"的素材） |
| `docs/adr/0001-dual-retriever.md` | **ADR 范例**：向量检索 vs 词面基线为什么并存、怎么收敛 |
| `docs/evaluation/README.md` + `glm-4-flash-250414.json` | 真实模型评测快照（15 条：结构化 100%、SQL 正确率 90%、P50/P95 延迟） |

### 第 1 层：Agent 核心（★最该读懂的一层）
| 文件 | 职责 | 学习点 |
|---|---|---|
| `agent_core/runtime.py` (18.6KB) | **LangGraph 分节点工作流**：路由→检索/SQL→校验→执行→合成；含一次纠错、超时、公开轨迹 | 企业级 Agent 的"工作流编排"长什么样 |
| `agent_core/routing.py` (4.3KB) | **确定性意图路由**（关键词/规则，零 token 成本） | 为什么"规则路由"在可复现性上优于让模型自主选工具 |
| `agent_core/llm_router.py` (3.4KB) | **LLM 路由对照实现**（A/B 实验的 B 侧） | 怎么做"用数据证明取舍"而不是嘴上说 |
| `agent_core/model_adapter.py` (6.4KB) | OpenAI 兼容模型适配 + **结构化 SQL 输出** + Token 元数据 | **JSON Mode + Pydantic**；兼容 DeepSeek/GLM |
| `agent_core/sql_safety.py` (4.1KB) | **SQLGlot AST 白名单** + 自动 LIMIT | 安全层怎么做（企业必问） |
| `agent_core/session.py` (6.6KB) | 会话：**Redis 优先 + 有界内存降级**（最多 1000 会话） | 有界降级思路 |
| `agent_core/evaluation.py` (5.0KB) | 离线评测入口（不花 API 钱） | 离线评测怎么设计 |
| `agent_core/live_evaluation.py` (12.5KB) | 真实模型评测（显式开关 `--allow-network`） | 为什么"不在 CI 偷用 Key" |
| `agent_core/eval/*.jsonl` | 评测集：`agent_cases`(100) / `routing_robustness`(25) / `routing_robustness_heldout`(10 留出) / `rag_cases`(15) / `live_model_cases`(15) | **调优集与留出集分开**（避免按考卷改答案） |
| `agent_core/eval/ab_routing_report.md` | 规则路由 vs LLM 路由的 A/B 报告 | 量化取舍的写法 |

### 第 2 层：RAG（知识库）
| 文件 | 职责 |
|---|---|
| `ai-ecommerce-assistant/knowledge_base/*.md`（6 份） | 业务术语、数据字典、KPI 公式、业务规则、**黄金 SQL 样例**、API 文档 ← **RAG 的"语料"长什么样** |
| `ai-ecommerce-assistant/build_knowledge_base.py` (10KB) | 切分策略：**按 H2 切 → 单 chunk ≤1500 字 → H3 优先 → 200 字重叠**；稳定 `doc_id` 支持增量 |
| `ai-ecommerce-assistant/rag/embeddings.py` | BGE-small-zh-v1.5 工厂（多线程单例 + 设备检测） |
| `ai-ecommerce-assistant/rag/vector_store.py` | Chroma 封装：增删查改、阈值过滤、元数据过滤、增量构建 |
| `ai-ecommerce-assistant/rag/retriever.py` (8.8KB) | **LRU+TTL 缓存、超时保护、上下文格式化、统计埋点** |
| `ai-ecommerce-assistant/rag/metrics.py` | 跨进程 stats + **Prometheus 渲染** + JSONL 事件 |
| `ai-ecommerce-assistant/eval/run_eval.py` | RAG 评估（20 条 gold_qa：命中率/doc_type 一致性/Top1 score/耗时） |
| `ai-ecommerce-assistant/eval/run_sql_eval.py` | **Text-to-SQL 评估**（AST 通过率 + gold 关键词命中 + 可选执行） |

### 第 3 层：后端（FastAPI）
| 文件 | 职责 |
|---|---|
| `backend/main.py` | 应用入口 + 生命周期（启动校验 CSV SHA-256） |
| `backend/config.py` | Pydantic Settings 配置 |
| `backend/database.py` | AsyncSession 连接池 |
| `backend/routes/`（8 个） | `auth`(JWT) / `orders` / `analytics` / `ai` / `export`(5,000 行分块流式) / `monitor` / `rfm` / `products` |
| `backend/utils/sql_guard.py` | 执行前的 SQL 只读拦截（与 agent_core 的 AST 校验互补） |
| `backend/utils/cache.py` / `rate_limiter.py` | 缓存 / 限流 |
| `backend/utils/rfm_scoring.py` | R/F/M 五分位 → 8 类用户分群 |
| `backend/scripts/sync_orders.py` | CSV 哈希校验 + 临时表 + 原子换表（**数据同步范式**） |
| `backend/scripts/init_ci_schema.py` | CI 建表 + 5 条 fake orders（不依赖全量数据跑测试） |
| `backend/tests/`（11 个文件） | 147+ 项测试：Runtime 核心、路由 A/B、流式、安全加固、MySQL 超时 |

### 第 4 层：前端与数据
| 文件 | 职责 |
|---|---|
| `streamlit_app.py` (42KB) + `bi_style.py` | BI 数据看板（Plotly 多维度交叉筛选） |
| `ai-ecommerce-assistant/app.py` (56KB) | AI 助手 UI（对话 + 引用折叠面板 + 👍/👎 反馈） |
| `backend/static/*.html/js/css` | 统一导航页 / Studio / 监控 / 健康面板 |
| `sql/01_create_table.sql` `02_import_data.sql` `03_analysis.sql` | 建表 / 导入 / 分析 SQL |
| `data/cleaned_orders.csv` (16MB) + `.parquet` | **真实数据集：102,287 条订单**（仓库自带，不用另找） |
| `notebook/01~05*.ipynb` | 数据清洗 → 销售分析 → 时间分析 → 商品/用户分析 → 可视化 |
| `output/*` | 分析产物（CSV + PNG：TOP10 商品、平台占比、每日趋势…） |

### 第 5 层：工程化
| 文件 | 职责 |
|---|---|
| `docker-compose.yml` (6.3KB) | 7 个服务：nginx / streamlit / backend / ai-assistant / db-bootstrap / mysql / redis |
| `Dockerfile` / `Dockerfile.streamlit` / `ai-ecommerce-assistant/Dockerfile` | 三个镜像 |
| `deploy/.env.example` | **环境变量模板**（四类 MySQL 密码 + JWT Secret + LLM Key） |
| `deploy/nginx.conf` | 统一入口路由 + **WebSocket 支持**（Streamlit 白屏坑） |
| `deploy/mysql/bootstrap-users.sh` | 创建**最小权限账号**（API/AI 只读） |
| `deploy/redis.conf` | AOF 持久化 + LRU 淘汰 |
| `scripts/health_check.py` | 全栈健康检查（8 项，输出 JSON，可 `--fail-on-error`） |
| `.github/workflows/{ci,docker-smoke,release}.yml` | CI（244 测试 + Ruff）/ 空卷冷启动验收 / 多架构镜像发布 |
| `requirements*.txt` / `ruff.toml` / `pytest.ini` | 依赖与规范 |

---

## 三、复现步骤清单（Windows + PowerShell）

### 步骤 0：环境准备
- [ ] **Python 3.12**（项目要求 3.12；3.13 也在 CI 覆盖）
- [ ] **Docker Desktop**（起 MySQL 8 + Redis 7；Compose 一键模式必需）
- [ ] 空闲内存 ≥ 8GB（MySQL+Redis+3 个 Python 服务）
- [ ] **DeepSeek API Key**（项目原生支持 OpenAI 兼容，填 DeepSeek 即可）
- [ ] 磁盘 ≥ 5GB（BGE 模型 93MB + 数据 + 镜像）

### 步骤 1：克隆
```powershell
cd F:\
git clone https://github.com/super-ZXQ/ai-commerce-intelligence-platform.git
cd ai-commerce-intelligence-platform
```

### 步骤 2（推荐先做）：Docker 一键跑通，建立信心
```powershell
Copy-Item deploy\.env.example .env
notepad .env      # 填 4 类 MySQL 密码、JWT_SECRET、LLM_API_KEY（DeepSeek）
docker compose up -d --build
docker compose ps
```
访问：`http://localhost/`（导航）· `/BI/`（看板）· `/ai/`（AI 助手）· `/docs`（Swagger）· `/health-panel`

> 先跑通再研究代码——**先看到效果，读代码才有方向**。

### 步骤 3：本地开发环境（学习用，能改代码）
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r ai-ecommerce-assistant\requirements.txt
pip install -r backend\requirements-dev.txt
```
只起数据库/缓存（应用跑本地，改完即生效）：
```powershell
docker run -d --name ea-mysql -e MYSQL_ROOT_PASSWORD=你的密码 -p 3306:3306 mysql:8.0
docker run -d --name ea-redis -p 6379:6379 redis:7-alpine
```

### 步骤 4：配置 .env（两个位置，README 有模板）
```powershell
# backend\.env
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=你的MySQL密码
DB_NAME=ai_commerce_intelligence_platform
REDIS_ENABLED=false
JWT_SECRET=dev-secret
LLM_API_KEY=sk-你的DeepSeekKey
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat

# ai-ecommerce-assistant\.env
LLM_API_KEY=sk-你的DeepSeekKey
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
DB_HOST=localhost
DB_PORT=3306
DB_USER=root
DB_PASSWORD=你的MySQL密码
DB_NAME=ai_commerce_intelligence_platform
```
> ⚠️ `.env` **绝不提交**（项目 `.gitignore` 已忽略；也别贴到聊天/截图里）

### 步骤 5：初始化数据库 + 校验数据量
```powershell
mysql -u root -p
SOURCE sql/01_create_table.sql;
SOURCE sql/02_import_data.sql;
SELECT COUNT(*) FROM orders;   -- 应为 102,287
```

### 步骤 6：构建 RAG 知识库（首次下载 BGE ~93MB）
```powershell
python ai-ecommerce-assistant\build_knowledge_base.py --rebuild
```

### 步骤 7：启动三个服务
```powershell
# 1) FastAPI 后端 :8000
Start-Process -WindowStyle Hidden -FilePath ".venv\Scripts\python.exe" -ArgumentList "-m uvicorn backend.main:app --host 0.0.0.0 --port 8000"
# 2) BI 看板 :8501
Start-Process -WindowStyle Hidden -FilePath ".venv\Scripts\streamlit.exe" -ArgumentList "run streamlit_app.py --server.port 8501"
# 3) AI 助手 :8505
Start-Process -WindowStyle Hidden -FilePath ".venv\Scripts\streamlit.exe" -ArgumentList "run ai-ecommerce-assistant/app.py --server.port 8505"
```

### 步骤 8：验证（三件事）
```powershell
python scripts\health_check.py          # 8 项健康检查
# 浏览器打开 http://localhost:8000/docs 看 Swagger
# 在 AI 助手问三条，分别命中 knowledge / data / hybrid：
#   "复购率怎么算？"            → knowledge（只走 RAG）
#   "各平台销售额 TOP10？"       → data（只走 SQL）
#   "按我们的退款率口径，各平台退款率是多少？" → hybrid
```

### 步骤 9：跑测试与评测（这是项目最值钱的部分）
```powershell
python -m pytest backend\tests\ -v                      # 后端（需 MySQL）
python -m pytest ai-ecommerce-assistant\tests\ -v       # RAG（不需要真实模型）
python -m agent_core.evaluation                         # 离线路由评测（不花钱）
cd ai-ecommerce-assistant
python eval\run_eval.py                                 # RAG 冒烟评估（fake embedder）
python eval\run_eval.py --real                          # 真实 BGE+Chroma 评估
python eval\run_sql_eval.py --execute                   # Text-to-SQL 评估（需 LLM Key）
```

### 步骤 10：改成你自己的（**3 处必改**，否则面试会问"哪部分是你写的"）
| # | 改动 | 说明 |
|---|---|---|
| 1 | **换数据集** | 把 `data/cleaned_orders.csv` 换成你自己的（或 Olist 全量多表），改 `sql/01/02` 与 `sync_orders.py` |
| 2 | **加 HITL 人工审批** | SQL 执行前弹"待确认"（项目已有 AST 校验，你补"人工确认"这一层） |
| 3 | **加你的评测** | 新增 20 条问数用例（问题 → 期望关键词/期望结果），跑出准确率写进 README |

---

## 四、这个项目最值得"偷"的 5 个设计（面试直接用）

1. **确定性路由 vs LLM 路由的 A/B**：规则路由 100/100（curated）、留出集 60% 且**如实披露**；用数据证明取舍，而不是嘴上说"规则更好"
2. **四层 SQL 防护**：输入规则 → SQLGlot AST → SQLAlchemy 执行拦截 → 数据库只读账号（单层失效不等于拿到写权限）
3. **JSON Mode + Pydantic**：拒绝 Markdown SQL；但明确"结构正确 ≠ 结果正确"，所以要**结果集评测**
4. **评测四件套**：路由回归（100+25+10 留出）· RAG 评估（20 条）· Text-to-SQL 评估 · 真实模型评测（15 条，显式运行不烧 CI 额度）
5. **有界降级**：Redis 挂了 → 内存会话（上限 1000）；SQL 只纠错一次 → 不做无限 Agent 循环

---

## 五、复现避坑

| 坑 | 处理 |
|---|---|
| Python 版本 | 必须 3.12（3.13 CI 也已覆盖）；别用系统默认的 3.14 |
| MySQL 密码 | `.env` 里 4 类密码要对上（root/api/ai/sync），不一致会启动失败 |
| BGE 首次下载 93MB | 首次 `--rebuild` 会下载，慢是正常的；别中断 |
| Redis 未起 | 项目会降级到内存会话，但要把 `REDIS_ENABLED=false` 或在 `backend/.env` 里设对 |
| Docker 内存不足 | MySQL+3 服务容易吃满；给 Docker Desktop 分配 ≥6GB |
| 端口冲突 | 8000/8501/8505/3306/6379；本地已有 MySQL 就改用 3307 映射 |
| Streamlit 白屏（Docker） | 项目已在 nginx 配好 WebSocket；自己改 nginx 时别删 `Upgrade`/`Connection` 头 |
| 直接把 .env 提交 | 别提交（含 Key/密码）；仓库已有 `.gitignore` |
| 照抄当自己项目 | **必须能讲清 runtime/routing/sql_safety/retriever 四个文件** |

---

## 六、面试会问什么（对着这个项目准备）

| 问题 | 答案要点（项目里的位置） |
|---|---|
| 为什么用确定性路由，不让模型自己选工具？ | 可复现/零成本/可评测；A/B 数据在 `agent_core/eval/ab_routing_report.md` |
| SQL 注入/误删怎么防？ | 四层防护：`sql_safety.py` AST + `backend/utils/sql_guard.py` + 只读账号 + `MAX_EXECUTION_TIME` |
| 怎么保证模型输出可用？ | JSON Mode + Pydantic 校验；结构对≠结果对 → 结果集评测 |
| RAG 怎么评估？ | `eval/run_eval.py`：命中率 / doc_type 一致性 / Top1 score / 耗时 |
| Text-to-SQL 怎么评估？ | `eval/run_sql_eval.py`：AST 通过率 + gold 关键词命中 + 可选执行 |
| 上下文/会话怎么管？ | 最近 6 轮 + thread_id 隔离 + TTL 30 分钟；Redis 挂了降级内存（上限 1000） |
| 部署架构？ | Nginx 统一入口 + 3 应用 + MySQL + Redis，仅暴露 80；`docker-compose.yml` |
| 数据同步怎么保证一致？ | `sync_orders.py`：CSV SHA-256 校验 → 临时表 → 校验行数 → 原子换表 |

---

## 七、本机复现记录（2026-09-14）

### 环境体检
| 项 | 结果 |
|---|---|
| git | 2.54.0 ✅ |
| Python | 默认 3.14，但 **3.12 已安装**（用 `py -3.12`）✅ |
| **Docker** | ❌ **未安装** |
| 磁盘 F: | 267 GB 可用 ✅ |

### 无 Docker 跑通方案（关键）
`app.py` 里 `USE_MYSQL = bool(DB_USER and DB_PASSWORD)` → **不配 DB_USER/DB_PASSWORD 就自动回落 SQLite 演示库**。

| 步骤 | 实际执行 |
|---|---|
| 1. 生成 SQLite 库 | 写了 `build_sqlite_demo.py`（**纯标准库**，不用 pandas）：`py -3.12 build_sqlite_demo.py` → `ai-ecommerce-assistant/ecommerce.db`，**102,287 行，2.0 秒**（含 7 个索引） |
| 2. 根目录 `.env` | `_PROJECT_ROOT` = 仓库根目录 → `.env` 放根目录；**故意不设 DB_USER/DB_PASSWORD**；`REDIS_ENABLED=false` |
| 3. 虚拟环境 | `py -3.12 -m venv .venv` + `pip install -r ai-ecommerce-assistant/requirements.txt` |
| 4. 依赖版本 | langchain 1.3.10 / langgraph 1.2.11 / chromadb 1.5.9 / sqlglot 30.17.0 / **torch 2.13.0+cpu** / sentence-transformers 5.6.0 |
| 5. 构建 RAG 知识库 | `HF_ENDPOINT=https://hf-mirror.com` + `build_knowledge_base.py --rebuild` → 6 份文档 → **60 chunks** → chroma 集合 `business_knowledge`（60 向量，0.8MB，38s） |
| 6. 离线评测 | `python -m agent_core.evaluation` → **路由 100/100**（多数类基线 52）、**Recall@3 100% / MRR 0.7444**，与 README 声称一致 ✅ |
| 7. 启动应用 | AI 助手 `:8505`、BI 看板 `:8501`，两者 `_stcore/health` 均 **HTTP 200** |

### 两个应用的数据来源（重要）
| 应用 | 数据来源 | 是否需要 MySQL |
|---|---|---|
| AI 助手（`ai-ecommerce-assistant/app.py`） | SQLite 回落（`ecommerce.db`） | ❌ 不需要 |
| BI 看板（`streamlit_app.py`） | **直接 `pd.read_csv`**（中文表头 CSV） | ❌ 不需要 |
| FastAPI 后端（`backend/`） | **硬编码 `mysql+pymysql`** | ✅ **需要 MySQL** |

### 尚未跑通的部分与选项
- FastAPI 后端（:8000 / Swagger / 32 接口 / RFM / 监控）需要 MySQL：
  - 选项 A：装 **Docker Desktop** → `docker compose up -d --build` 一键全栈（推荐）
  - 选项 B：`winget install MySQL.MySQL` 装 MySQL 8，用 `sql/01_create_table.sql` + `02_import_data.sql` 建表导入
  - 选项 C：先不动后端（第 2 周补，因为 FastAPI 是 JD 高频项）
- `.env` 里的 `LLM_API_KEY` 仍是占位符 → **填 DeepSeek Key 后重启 AI 助手**才能用 Text-to-SQL；不填时知识类问题仍可用（RAG 照常返回来源）

### 复现中的第一个"真实发现"（可当你自己的改动 #1）
`knowledge_base/data_dictionary.md` 说 `platform_type` 只有 4 个枚举值（`APP` / `微信公众号` / `Web网站` / `其他`），但**真实数据里有 6 个**：

| 实际值 | 订单数 | 销售额 |
|---|---|---|
| 微信公众号 | 42,032 | 47,100,892.19 |
| APP | 51,294 | 46,041,870.47 |
| web网站（小写 w，与字典的 `Web网站` 不一致） | 6,898 | 6,699,451.08 |
| 淘宝 | 1,973 | 1,823,308.43 |
| 微信小店 | 87 | 88,521.17 |
| wap网站 | 3 | 1,775.64 |

→ **数据字典与实际数据不一致**（还多了 3 个值 + 大小写差异）。这既是"数据质量问题"的真实案例，也是你**改动的第一项**：修正字典 + 让路由/枚举做兼容（这正好是"数据质量 + 异常处理"的面试素材）。
