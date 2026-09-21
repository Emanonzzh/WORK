# config.py：全局常量集中管理（修改需有依据——大部分由 RAG 评测得出）

# ===== RAG 检索参数（V2-C 评测：12题×四组实验，83%→100%，详见 rag_eval.py）=====
CHUNK_SIZE = 400      # 切片大小（评测最优）
CHUNK_OVERLAP = 20    # 切片重叠
TOP_K = 4             # 检索条数（评测最优；k 越大 token 越贵，取最小够用）

# ===== 风险评估参数（day23_risk.py 规则，验收于川西示范区）=====
RATE_THRESHOLD = 10   # 近3期平均月增量阈值(mm)，超 → +40
RATE_SCORE = 40
ACCEL_SCORE = 30      # 后半段速率>前半段 → +30
ANOMALY_RATIO = 3     # 最近一期增量 > 均值×3 → +30
LEVEL_LOW = 30        # 分级边界
LEVEL_MID = 60

# ===== 数据库 =====
SQL_LIMIT = 5         # query_dataset 单次返回行数上限（防上下文撑爆）
