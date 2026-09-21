# 第二个项目选型：销售数据分析 Agent（电商场景）· 2026-09-14

> 目的：用**真实场景（销售数据 → 自动分析 → 生成报告）**替代/补充"伪需求"项目。
> 选型标准：① 电商/销售场景 ② 技术栈含 **Text-to-SQL + Agent + 报告生成** ③ 可复现（不需付费 API，可换 DeepSeek/通义）④ 能补上你的缺口（后端 FastAPI / 数据库 / Docker / 评测）

---

## 一、候选项目（按推荐度排序）

### 🥇 主推：[super-ZXQ/ai-commerce-intelligence-platform](https://github.com/super-ZXQ/ai-commerce-intelligence-platform)
**Portfolio-ready LangGraph commerce agent**：**Text-to-SQL + RAG + BI 看板 + FastAPI + MySQL + Docker + 可复现评测**
- ✅ **最贴你要的**：电商 + 自然语言问数 + 图表/看板 + 报告
- ✅ 技术栈**正好是你的缺口清单**：FastAPI（后端）、MySQL（数据库）、Docker（部署）、**可复现 evals（评测）**
- ✅ 自称 portfolio-ready → 结构清晰，适合当模板复现
- ⚠️ 需要 MySQL（用 Docker 起）+ LLM API（可换 DeepSeek/通义）

### 🥈 中文教学配套：[123Icecream/shopkeeper-agent](https://github.com/123Icecream/shopkeeper-agent)（电商问数）
**NL2SQL + LangGraph + MySQL + Qdrant + Elasticsearch + FastAPI SSE**
- ✅ 有**中文教程章节**配套（来自 [ai-agents-from-zero](https://github.com/XingJi-love/ai-agents-from-zero) 的实战项目）→ 学习曲线最平
- ✅ 覆盖**向量检索 + 关键词检索 + SQL**（企业级"多路"思路）
- ⚠️ 组件较多（MySQL+Qdrant+ES），复现成本比第一个高

### 🥉 最易起步（CSV 版）：[JohnDBSDSN/amazon-vendor-ai-analyst](https://github.com/JohnDBSDSN/amazon-vendor-ai-analyst)
**Amazon Vendor 销售分析**：CSV pipeline + GPT-4o insights + **Streamlit UI + FastAPI**
- ✅ 不依赖数据库集群，**一两天能跑通**，直接产出"销售数据 → 洞察报告"
- ✅ 起点低、适合先跑通再升级（把 CSV 换成 MySQL/真实数据集）
- ⚠️ LLM 用的是 GPT-4o → 换成 DeepSeek/通义

### 其他参考
| 项目 | 特点 |
|---|---|
| [sharptoolbox/Onto-DataAnalyse](https://github.com/sharptoolbox/Onto-DataAnalyse) | 本体驱动的**电商数据智能分析**（中文） |
| [rit-ops14/nexus-analyst](https://github.com/rit-ops14/nexus-analyst) | LangGraph + **MCP** + RAG 多智能体对话式分析 |
| [MahmoudNashat950/Retail-Analytics-Copilot-DSPy-LangGraph-](https://github.com/MahmoudNashat950/-Retail-Analytics-Copilot-DSPy-LangGraph-) | Retail Analytics Copilot（DSPy + LangGraph） |
| [MaduAraujo/Relatorio-de-Vendas](https://github.com/MaduAraujo/Relatorio-de-Vendas) | 销售报告 Agent（葡语） |
| [Shahidali78/Ai-Data-Analyst-Agent](https://github.com/Shahidali78/Ai-Data-Analyst-Agent) | AI 数据分析师 Agent |

### 行业标杆（**只读架构，别复现**）
| 项目 | 为什么看 |
|---|---|
| [Canner/WrenAI](https://github.com/Canner/WrenAI)（17.6k★，活跃） | **GenBI**：语义层 + 受治理 text-to-SQL + 仪表盘，支持 20+ 数据源（含 pgvector 相关生态）→ 理解"企业怎么做问数" |
| [vanna-ai/vanna](https://github.com/vanna-ai/vanna)（23.8k★，已归档） | Text-to-SQL + **Agentic Retrieval**（检索相似 SQL 样例）→ 学"怎么提高问数准确率" |
| [agno-agi/dash](https://github.com/agno-agi/dash) | 自学习 data agent：**6 层上下文**、随查询自我改进 |
| [getnao/nao](https://github.com/getnao/nao) | 开源分析 Agent：**context engineering** + 部署聊天界面 |
| [togethercomputer/open-data-scientist](https://github.com/togethercomputer/open-data-scientist) | ReAct 数据分析 + **自动生成分析报告** |
| [HKUSTDial/awesome-data-agents](https://github.com/HKUSTDial/awesome-data-agents) | Data Agent 论文清单（含 NL2SQL / NL2VIS / **report-generation** 分类） |

---

## 二、真实数据集（别再用示例数据）

| 数据集 | 规模/内容 | 获取 |
|---|---|---|
| **Olist 巴西电商**（推荐） | 9 万+ 订单、商品/卖家/评价/地理，真实多表 | Kaggle: `olistbr/brazilian-ecommerce` |
| UCI Online Retail | 英国电商交易流水（50 万行） | UCI ML Repository |
| Amazon Vendor / 淘宝用户行为 | 电商行为日志 | 天池 / Kaggle |

> Olist 是多表关系型数据（orders/order_items/products/customers/sellers/payments/reviews），**天生适合 Text-to-SQL + 多表 JOIN + 销售分析报告**，且体量小（几百 MB），能直接塞进 MySQL/pgvector。

---

## 三、2 周复现计划（对齐《学习路径》第 2-4 周）

| 天 | 任务 | 产出 |
|---|---|---|
| D1-D2 | clone 主推项目，用 Docker 起 MySQL，跑通默认流程；**画一张架构图**（用户问题 → Text-to-SQL → 执行 → 分析 → 报告） | 能跑通的 demo + 架构图 |
| D3-D4 | 下载 **Olist** 数据集 → 导入 MySQL（写 `load_olist.py`） | 真实数据入库 |
| D5-D6 | 把 LLM 换成 **DeepSeek/通义**（改 base_url/model）；**加 Pydantic 校验** SQL 输出 | 中文可用 + 输出受控 |
| D7-D9 | 加**评测**：手工造 20 条"问题 → 期望 SQL/期望结果"，算**问数准确率**（对齐你原项目的评测经验） | `eval_olist.md` |
| D10-D11 | 加 **报告生成**（按"销售概况/趋势/异常/建议"分节）+ Streamlit 页面 | 可演示的报告 |
| D12-D13 | 加 **HITL**：生成的 SQL 先给人确认再执行（防误删/误查） | 审批流 |
| D14 | **Docker 化 + docker compose（app + MySQL）+ `/health`**；写 README（架构图 + 评测结果 + 演示截图） | 可上线、可展示 |

---

## 四、复现避坑

| 坑 | 处理 |
|---|---|
| 项目默认用 GPT-4o / OpenAI SDK | 改 `base_url` + `model` + `api_key`（你已有 DeepSeek/通义 key） |
| 需要 MySQL | 用 `docker compose` 起（正好练第 5 周内容）；或先降级 SQLite 跑通逻辑 |
| 依赖老版本 langchain | 锁版本或用 `uv`/`venv` 隔离环境，别污染现有项目 |
| 数据集版权 | 只用公开数据集（Olist/UCI 可商用研究），别用公司真实数据 |
| 直接照抄 | **必须能讲清每一层**：为什么要 NL2SQL、为什么要样例检索（Vanna 思路）、怎么评测、怎么防注入 |

---

## 五、这个项目能补上什么（对照 JD 词频）

| JD 高频要求 | 这个项目覆盖 |
|---|---|
| Python / LangChain-LangGraph | ✅ |
| **Text-to-SQL / 数据库 / SQL 优化** | ✅✅（你的最大缺口） |
| **FastAPI 后端** | ✅ |
| **Docker / 部署** | ✅ |
| **评测（可复现 evals）** | ✅（可复用到"问数准确率"） |
| RAG / 向量库 | ✅（部分项目含 Qdrant/pgvector） |
| 真实业务场景（销售/电商） | ✅✅（真实数据集 + 真实问题） |
| 异常处理 / HITL | ✅（D12-D13 加） |

---

## 六、建议的最终形态（简历怎么写）

> **项目二：电商销售数据智能分析 Agent（真实数据集）**
> - 基于 **LangGraph** 构建 NL2SQL Agent：自然语言 → SQL → 执行 → 分析 → **自动生成销售报告**
> - 数据集：Olist 巴西电商真实数据（9 万+ 订单，多表关联）
> - **问数准确率评测**：20 条测试用例，准确率 X%（可复现脚本）
> - **受控执行**：SQL 白名单校验 + **人工审批（HITL）** 后执行，防误操作
> - 工程化：FastAPI + Streamlit + Docker Compose + `/health` + 评测回归
> - 价值：把"查数+做报表"从人工 30 分钟缩短到 1 分钟（**可找真实用户验证**）

> ⚠️ 复现 ≠ 抄。面试官会问"这是你写的吗？哪部分是你加/改的？"——**至少要有 3 处你自己的改动**：换数据集、加评测、加 HITL/权限。
