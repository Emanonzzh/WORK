# AGENTS.md — 项目说明与既有事实

> 本文件只记录**项目本身的事实、约定和踩过的坑**，供任何在本仓库工作的人/Agent 使用。
> 学习进度、求职材料、个人记录不在本仓库（本仓库是作品库，不是笔记本）。

## 这个仓库里有什么

| 目录 | 内容 |
|---|---|
| `agent_lab/` | **项目一**：销售经营分析 Agent（FastAPI + 手写 ReAct + 量价三因子分解 + 异常检测 + 报告数字对账） |
| `agent_lab/tests/` | 245 项 pytest 单测，**不需要 MySQL、不需要 LLM**，约 0.35 秒跑完（数字以 `python -m pytest -q` 的输出为准，别信文档里的快照） |
| `day19_sqlite.py` … `day24_analysis.py`、`day21.py` | **项目二**：遥感监测智能问答 Agent（RAG + 三工具 LangGraph Agent）的演进过程与成品 |
| `rag_eval.py` / `rag_eval_questions.py` | 项目二的 RAG 检索评测脚本与 12 题测试集 |
| `monitoring.db`、`*.txt`、`*.json` | 项目二使用的真实数据与知识库 |
| `docs/` | 复现指南、项目框架、SQL 入门 |
| `Dockerfile`、`deploy.sh` | 项目二的容器化与部署脚本 |

## 两个项目的真实状态（未完成的别当成已完成的）

- ✅ 项目二**已部署**：阿里云 ECS `47.76.101.97`，**对外入口 = Nginx 反向代理 `:80`**，地址 `http://47.76.101.97`
  （容器内 Streamlit 的 `8501` 已收回 `127.0.0.1`，公网不可直连；`deploy.sh` 用 `-p 127.0.0.1:8501:8501`，**不要改回 `-p 8501:8501`**）
  更新方式 `cd /root/study && git pull && bash deploy.sh`
  （2026-09-23 01:38 复验：`http://47.76.101.97/_stcore/health` 返回 `ok`；`:8501` 直连被拒，符合预期）
- ⚠️ 该入口**仍然只有 HTTP、没有 TLS**。换到 80 端口**没有**解决手机打不开的问题——
  手机 Chrome 仍会把地址升级成 `https://47.76.101.97`（443 未开）而失败。
  发给别人（含面试官）时必须写全 `http://47.76.101.97`（**不要再带 `:8501`**，那个端口已关闭），并准备截图/录屏作兜底。
- ⚠️ 项目一**从未端到端跑过容器化** → 任何文档/README 不得声称"已容器化"。
  精确表述：`docker-compose.yml`（7 服务 / 216 行）**存在，但是上游开源项目自带的，不在本仓库内**；
  本仓库根目录那个 `Dockerfile` 是给项目二用的。**可说**"存量项目自带 compose 编排，我在它上面开发"；
  **不可说**"我写了 compose""已容器化"。
- ⚠️ 项目一的"单轮基线 vs 手写循环"对比评测**缺 baseline 脚本**，尚未做
- ✅ `README.md` 三张演示截图 **09-24 已补**（从公网实例实拍：首页 / 形变风险分析 / 法规问答）
- ⚠️ 顺带纠正一处 README 过度声称：原文写"条款检索结果带`【来源：第X条】`标签，**用户可核对原文**"——
  实际该标签只拼在工具返回值里**喂给模型**，界面上不展示，用户看到的是模型转述的条款号。已改成写明边界。
- ⚠️ 项目一仅在本机以 localhost 运行过 → 对外表述为"可复现"，不写"已上线"

**通用红线**：未经验证的功能与指标，不得写进 README、注释或文档。

## 已修过的真 bug（可作为技术讨论的入口）

1. **`/analyze` 全局状态并发竞态**：原实现临时改写模块级 `MAX_STEPS` 再还原，而同步 `def` 接口跑在线程池里 → 并发请求互相污染步数上限（会多烧 token）。改为 `run_agent(max_steps=...)` 请求级参数，模块常量退化为默认值。
   **反证过**：把修复临时还原后重跑 = 15 failed + 1 collection error；恢复后 = 245 passed。
2. **`_check_date` 只校验格式不校验语义**：原来只判 `day ∈ 01~31`，`2025-02-31`、`2025-04-31` 被放行 → 改用 `calendar.monthrange` 按当月实际天数校验（闰年交给标准库）。
3. **`decompose` 原是嵌套函数**（import 不到 = 不可测）→ 提到模块级，并断言三因子加总闭合为 `0.0`。
4. **端口 8000 被静默抢占**：`agent_lab/api.py` 的 `PORT` 从 8000 改为 8010。原因是本机 `CLodopPrint32.exe`（打印控件，开机自启）绑 `0.0.0.0:8000`；Windows 仍允许我们再绑 `127.0.0.1:8000`，uvicorn 照常打印 "running on 127.0.0.1:8000"，但请求全被那条通配 socket 抢走——`/docs` 返回打印控件页面，`/health` 返回 **HTTP 200 + HTML**。旧启动脚本只判断"端口是否在监听"，于是拿这个 200 宣布启动成功。
   → 健康检查改为**校验响应体**（`/health` 必须解析成 JSON 且 `status=ok`；Streamlit 用 `/_stcore/health` 返回 `ok`）；启动前查占用者，是别人就拒绝启动并点名。
5. **GBK 控制台崩溃**：脚本打印 ✅/❌ 触发 `UnicodeEncodeError` → `sys.stdout.reconfigure(encoding="utf-8")`。

## 环境与运行

- **能不能跑项目一，取决于你在哪台机器上——不要把任何一台机器的结论搬去另一台。**
  2026-09-22 实测：开发机上 MySQL 在运行、`orders` 与 `orders_injected` 各 102,287 行、依赖齐全，跑得起来；
  另一台机器则无 `.env`、无 MySQL。判定只需三条：① 3306 有没有监听、库里行数对不对；② 用的是哪个解释器；③ 有没有 `.env`。
- **两个会被误判成"这台机器跑不了"的假信号**（都别据此下结论）：
  ① `No module named 'pymysql' / 'pytest'` —— 多半是**解释器选错了**。项目依赖装在上游项目目录的 `.venv`（Python 3.12）里，
  而系统里可能还有一个版本更高、什么都没装的 `python.exe`。先确认解释器再判断。
  ② `1045 Access denied for user '<OS用户名>' (using password: NO)` —— 是**没加载 `.env`**，不是数据丢了。
  凭据来自 `DB_USER/DB_PASSWORD/DB_NAME` 等变量，运行前先 `load_dotenv`。
- 缺 `.env`（只有 `.env.example`）时**只读代码，不要安排"跑一下试试"**；`pytest agent_lab/tests` 是例外——它不连库不调模型，只要有依赖就能跑。
- 大模型走阿里云百炼：LLM `qwen-plus`，Embedding `text-embedding-v3`；key 一律用环境变量 `DASHSCOPE_API_KEY` 读取（`os.getenv`）。
  **曾有真实 key 被硬编码 push 出去，已吊销** → 任何情况下不得把 key 写进源码。
- 模型服务地址统一读 `DASHSCOPE_BASE_URL`（默认值 = 旧共享域名 `https://dashscope.aliyuncs.com/compatible-mode/v1`，不设置则行为不变）。
  动因：阿里云公告该共享域名 **2026-09-30 起进入维护状态**（不再迭代新特性，现有服务不受影响），建议迁到业务空间专属域名
  `https://{workspaceId}.{region}.maas.aliyuncs.com/compatible-mode/v1`。原先 8 个文件各写死一份 URL，迁移要改 8 处；现在只改环境变量。
- **已装库（2026-09-22 按解释器实测，别当成"这台机器装了啥"）**：
  | 解释器 | 项目二那套<br>openai/chromadb/langchain/langgraph/fastapi/uvicorn/streamlit | 项目一那套<br>pymysql/pytest |
  |---|---|---|
  | 系统 Python 3.14 | ✅ 全有 | ❌ 全缺 → **能跑项目二，跑不了项目一** |
  | 上游项目的 `.venv`（3.12） | ✅ 全有 | ✅ 全有 → 跑项目一用它 |
  | 另一个系统 Python 3.12 | ❌ 全缺 | ❌ 全缺 |

  同一台机器上"跑不了"往往只是**解释器不对**，不是环境缺失。

## 本项目踩过的技术坑（写代码前先扫一眼）

- 阿里云 embedding **每批最多 10 条**，必须传 `chunk_size=10`；它与 splitter 的 `chunk_size`（切片长度）是两回事，别混。
- `langchain` 新版连阿里云 embedding 必须加 `check_embedding_ctx_length=False`。
- Chroma 集合名**不能含中文**。
- SQL `LIKE` 是连续子串匹配，不是模糊分词。
- `conn.close()` 必须在函数内 `return` 之前；`UPDATE` 定位用业务字段（如 `date`）而非自增 `id`；`AUTOINCREMENT` 的 ID 永不复用；写操作要用 `cursor.rowcount` 检查影响行数，防止静默失败。
- **相对路径相对的是当前工作目录**，不是脚本所在目录——`monitoring.db` 曾被误建进别的目录。
- Streamlit 只能用 `streamlit run` 启动。
- 项目二有一个**已知幻觉案例**（保留作反面素材）：回答青藏数据集时给出了元数据里不存在的速率数字。

## 仓库约定

- `agent_lab/` 在上游开源项目 `ai-commerce-intelligence-platform` 里是**未跟踪文件**（那个仓库 remote 不是本人的）。**本仓库这份 `agent_lab/` 才是该项目唯一的版本化副本**；改动后需同步回开发目录，否则两份会分叉。
- 提交信息格式：`类型: 做了什么（原因/关键点）`，中文，一行说清"改了什么 + 为什么"。
- 代码：英文变量名 + 中文注释；函数要有 docstring。
- **本仓库不存放**：简历（含手机号/邮箱）、投递记录、学习打卡、面试题答案、个人进度——求职过程材料一律放在另一个私有仓库里。
  本仓库已于 2026-09-24 转为 private，也不再印在简历上（对外是 `WORK`）。**但 `WORK` 是从本仓库导出的**——
  写在这里的每一句都会被原样搬进面试官看得见的地方，所以仍按"面试官随时会点开"的标准自律。
