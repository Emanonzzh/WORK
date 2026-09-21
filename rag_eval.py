import os
import sys
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from rag_eval_questions import test_cases   # ← 导入你昨天的测试集

api_key = os.getenv("DASHSCOPE_API_KEY")
if not api_key:   # 缺 key 直接报明白错，fail fast——别等 401 才发现
    sys.exit("缺少环境变量 DASHSCOPE_API_KEY：请先执行 setx 或 $env:DASHSCOPE_API_KEY=xxx 再运行")
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

def build_store(chunk_size):   # ← 接收切片大小参数
    embeddings = OpenAIEmbeddings(
        model="text-embedding-v3",
        api_key=api_key,
        base_url=base_url,
        check_embedding_ctx_length=False,
        chunk_size=10,   # 阿里云 embedding 每次最多 10 条，必须分批发（这个是"条数"，与切片大小无关）
    )
    with open("地质灾害防治条例.txt", "r", encoding="utf-8") as f:
        text = f.read()
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=20)
    chunks = splitter.split_text(text)
    print(f"  chunk_size={chunk_size}：切成 {len(chunks)} 块")
    # 集合名按参数区分，避免两组实验混库
    return Chroma.from_texts(chunks, embeddings, collection_name=f"rs_kb_eval_{chunk_size}")

def eval_hits(store, k):
    hits = 0
    for question, expect in test_cases:
        docs = store.similarity_search(question, k=k)
        material = "\n".join(d.page_content for d in docs)
        if expect in material:
            hits = hits + 1
    return hits

# 主流程：4 组实验
for chunk_size in [100, 400]:
    store = build_store(chunk_size)
    for k in [2, 4]:
        hits = eval_hits(store, k)
        print(f"chunk_size={chunk_size}, k={k} → 命中 {hits}/12")