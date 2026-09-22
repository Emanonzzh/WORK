# Day 15：RAG + FastAPI —— 知识库问答系统变成 API
import os, sys
sys.stdout.reconfigure(encoding="utf-8")

from fastapi import FastAPI
import uvicorn
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma

api_key = os.getenv("DASHSCOPE_API_KEY")
if not api_key:
    sys.exit("缺少环境变量 DASHSCOPE_API_KEY：请先执行 setx 或 $env:DASHSCOPE_API_KEY=xxx 再运行")
base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

app = FastAPI()

# ========== 第一部分：全局准备（服务启动时执行一次）==========
llm = ChatOpenAI(model="qwen-plus", api_key=api_key, base_url=base_url)

embeddings = OpenAIEmbeddings(
    model="text-embedding-v3",
    api_key=api_key,
    base_url=base_url,
    check_embedding_ctx_length=False,
)

def build_store():
    kb_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "知识库.txt")
    if not os.path.exists(kb_path):
        sys.exit(f"找不到知识库文件：{kb_path}")
    with open(kb_path, "r", encoding="utf-8") as f:
        text = f.read()
    splitter = RecursiveCharacterTextSplitter(chunk_size=100, chunk_overlap=20)
    chunks = splitter.split_text(text)
    store = Chroma.from_texts(chunks, embeddings, collection_name="rs_kb_api")
    return store

store = build_store()   # 启动时就建好知识库，之后每次请求直接复用

# ========== 第二部分：接口（每次访问网址时执行）==========
@app.get("/ask")
def ask(question: str):
    # ① 检索：找最相关的 2 块资料
    docs = store.similarity_search(question, k=2)
    # ② 拼 prompt
    material = "\n".join(d.page_content for d in docs)
    prompt = f"参考材料：\n{material}\n\n问题：{question}\n请根据上面的材料回答。材料里没有的，就说不知道。"
    # ③ 生成答案
    answer = llm.invoke(prompt)
    # ④ 返回给浏览器（顺便把检索到的资料也返回，方便调试）
    return {
        "question": question,
        "answer": answer.content,
        "sources": [d.page_content for d in docs],
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)