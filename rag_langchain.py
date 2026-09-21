# Day 11：用 LangChain 重做 RAG（代码量：60 行 → 25 行）
import os, sys
sys.stdout.reconfigure(encoding="utf-8")   # 防止中文输出 GBK 报错

api_key = os.getenv("DASHSCOPE_API_KEY")
if not api_key:
    sys.exit("缺少环境变量 DASHSCOPE_API_KEY：请先执行 setx 或 $env:DASHSCOPE_API_KEY=xxx 再运行")
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"

def build_store():
    from langchain_openai import OpenAIEmbeddings
    embeddings = OpenAIEmbeddings(
        model="text-embedding-v3",
        api_key=api_key,
        base_url=base_url,
        check_embedding_ctx_length=False,   # 关键坑！阿里云只认原始字符串，见下面讲解
    )
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    kb_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "知识库.txt")
    if not os.path.exists(kb_path):
        sys.exit(f"找不到知识库文件：{kb_path}")
    with open(kb_path, "r", encoding="utf-8") as f:
        text = f.read()
    splitter = RecursiveCharacterTextSplitter(chunk_size=100, chunk_overlap=20)
    chunks = splitter.split_text(text)
    print(f"切成 {len(chunks)} 块")

    from langchain_chroma import Chroma
    store = Chroma.from_texts(chunks, embeddings, collection_name="rs_kb_lc")

    return store

store = build_store()    # 用变量接住 return 回来的东西

# ④ 检索 + 生成
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="qwen-plus", api_key=api_key, base_url=base_url)

while True:
    question = input("\n你：")          # 等你敲内容，回车后存进 question

    if question == "quit":             # 输入 quit 就退出
        print("再见！")
        break

    docs = store.similarity_search(question, k=2)

    material = "\n".join(d.page_content for d in docs)
    prompt = f"参考材料：\n{material}\n\n问题：{question}\n请根据上面的材料回答。材料里没有的，就说不知道。"

    answer = llm.invoke(prompt)
    print("AI：", answer.content)



