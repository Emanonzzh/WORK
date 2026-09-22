import os
import sys
import streamlit as st
from langchain_core.tools import tool
from langchain.agents import create_agent
import sqlite3
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma

api_key = os.getenv("DASHSCOPE_API_KEY")
if not api_key:   # 缺 key 直接报明白错，fail fast——别等 401 才发现
    sys.exit("缺少环境变量 DASHSCOPE_API_KEY：请先执行 setx 或 $env:DASHSCOPE_API_KEY=xxx 再运行")
base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
llm = ChatOpenAI(model="qwen-plus",api_key=api_key,base_url=base_url)

@st.cache_resource
def build_store():
    embeddings = OpenAIEmbeddings(
        model="text-embedding-v3",
        api_key=api_key,
        base_url=base_url,
        check_embedding_ctx_length=False,
        chunk_size=10
    )
    with open("地质灾害防治条例.txt","r",encoding="utf-8") as f:
        text = f.read()
    splitter = RecursiveCharacterTextSplitter(chunk_size=100,chunk_overlap=20)
    chunks = splitter.split_text(text)
    return Chroma.from_texts(chunks,embeddings,collection_name="rs_kb_web")

store = build_store()

@tool
def query_regulation(keyword: str) -> str:
    """查询《地质灾害防治条例》条款。keyword 是问题关键词，如"地质灾害等级"、"预报制度"。"""
    docs = store.similarity_search(keyword,k=2)
    if not  docs:
        return"无结果"
    lines = []
    for d in docs:
        lines.append(d.page_content)
    return"\n\n".join(lines)

@tool
def query_dataset(keyword: str) -> str:
    """查询遥感监测数据集元数据。keyword 是关键词，例如"青藏"、"InSAR"、"理塘"。"""
    conn = sqlite3.connect("monitoring.db")
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM datasets WHERE name LIKE ? OR region LIKE ? OR method LIKE ?", 
        (f"%{keyword}%",f"%{keyword}%",f"%{keyword}%")
        )

    rows = cursor.fetchall()
    if not rows:
        conn.close()  # 先关连接
        return "无结果"
    lines = []
    for row in rows:
        lines.append(
            f"数据集{row[1]}, 区域{row[2]}, 方法{row[3]}"
            f"时间{row[4]} ,精度{row[5]}, 来源{row[6]}"
        )
    result_text = "\n".join(lines)
    conn.close()
    return result_text
  
agent = create_agent(llm,[query_dataset,query_regulation])

st.title("遥感地质灾害智能助手")
st.write("法规问答 + 数据集查询")

with st.form("qa_form"):
    question = st.text_input("你的问题是：",  placeholder="例如：地质灾害分为哪几个等级？")
    submitted = st.form_submit_button("提问")

if submitted and question:
    result = agent.invoke({"messages": [("user", question)]})
    st.markdown("###回答")
    st.write(result["messages"][-1].content)