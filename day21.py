import os
import re
import sys
import streamlit as st
from langchain_core.tools import tool
from langchain.agents import create_agent
import sqlite3
import config
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma

api_key = os.getenv("DASHSCOPE_API_KEY")
if not api_key:
    sys.exit("缺少环境变量 DASHSCOPE_API_KEY：请先执行 setx 或 $env:DASHSCOPE_API_KEY=xxx 再运行")
base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

llm = ChatOpenAI(model="qwen-plus", api_key=api_key, base_url=base_url)

@st.cache_resource
def get_article_labels(chunk):
    """从切片文本里抽取出现的条款号列表，如【'第四条', '第五条'】"""
    return re.findall(r"第[一二三四五六七八九十百]+条", chunk)


def build_store():
    embeddings = OpenAIEmbeddings(
        model="text-embedding-v3",
        api_key=api_key,
        base_url=base_url,
        check_embedding_ctx_length=False,
        chunk_size=10,   # 阿里云 embedding 每次最多 10 条，必须分批发
    )
    with open("地质灾害防治条例.txt", "r", encoding="utf-8") as f:
        text = f.read()
    splitter = RecursiveCharacterTextSplitter(chunk_size=config.CHUNK_SIZE, chunk_overlap=config.CHUNK_OVERLAP)
    chunks = splitter.split_text(text)
    metadatas = [{"articles":",".join(get_article_labels(c))} for c in chunks]
    return Chroma.from_texts(chunks, embeddings, collection_name="rs_kb_web_v2",
                             metadatas = metadatas,)

store = build_store()

@tool
def query_regulation(keyword: str) -> str:
    """查询《地质灾害防治条例》条款。keyword 是问题关键词，如"地质灾害等级"、"预报制度"。"""
    docs = store.similarity_search(keyword, k=config.TOP_K)
    if not docs:
        return"条例中未找到相关内容"
    lines = []
    for d in docs:
        source_tag = d.metadata.get("articles","未识别条款")
        lines.append(f"【来源：{source_tag}】{d.page_content}")
    return "\n\n".join(lines) 

@tool
def query_dataset(keyword:str) -> str:
    """查询遥感监测数据集元数据。keyword 是关键词，例如"青藏"、"InSAR"、"理塘"。"""
    try:
        conn = sqlite3.connect("monitoring.db")
        cursor = conn.cursor()  
        cursor.execute(
        f"SELECT * FROM datasets WHERE name LIKE ? OR region LIKE ? OR method LIKE ? LIMIT {config.SQL_LIMIT}",
        (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"),
        )
        rows = cursor.fetchall()

    except Exception as e:
        return f"数据查询出错：{e}"
    
    if not rows:
        conn.close()
        return "未找到相关数据集"
    line = []
    for row in rows:
        line.append(
            f"数据集:{row[1]}，区域:{row[2]}, 方法:{row[3]},"
            f"时间:{row[4]}，精度:{row[5]}, 来源:{row[6]},"
        )
    result_text = "\n".join(line)   # 先拼好结果，存进变量
    conn.close()                     # 再关连接（在函数里面！）
    return result_text  

@tool
def risk_assessment(region:str) -> str:
    """计算指定区域的形变风险评分与风险等级（例如"川西示范区"）。

    仅在用户询问某区域的形变风险时调用，例如"川西示范区风险怎么样""XX区域处于什么风险等级""需不需要防范"。
    不要用于：① 概念/原理解释（如"什么是InSAR、PS-InSAR、SBAS-InSAR"）；② 数据集查询（如"有哪些监测数据集"，
    请用 query_dataset）；③ 法规条款问答（请用 query_regulation）。
    参数 region：区域名称，如"川西示范区"。"""
    try:
        conn = sqlite3.connect("monitoring.db")
        cursor = conn.cursor()
        cursor.execute("SELECT deformation_mm FROM monitoring_series WHERE region = ? ORDER BY date",
            (region,),
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        return f"查询出错:{e}"
    deforms = []
    for row in rows:
        deforms.append(row[0])

    if len(deforms) < 4 :
        return"数据不足"

    recent_rate = (deforms[-1] - deforms[-4]) / 3
    mid = len(deforms) // 2 
    first_rate = (deforms[mid] - deforms[0]) / mid
    second_rate = (deforms[-1] - deforms[mid]) / (len(deforms) - 1 - mid)
    last_step = deforms[-1] - deforms[-2]
    avg_step = (deforms[-1] - deforms[0]) / (len(deforms) - 1)

    score = 0
    if recent_rate > config.RATE_THRESHOLD:
        score += config.RATE_SCORE
    if second_rate > first_rate:
        score += config.ACCEL_SCORE
    if last_step > avg_step * config.ANOMALY_RATIO:
        score += config.ACCEL_SCORE

    if score <= config.LEVEL_LOW:
        level = "低风险"
    elif score <= config.LEVEL_MID:
        level = "中风险"
    else:
        level = "较高风险"
    return (
    f"风险评分：{score} 分，等级：{level}。"
    f"指标明细：近3期平均月增量 {recent_rate:.1f}mm（阈值10），"
    f"前半段 {first_rate:.1f} vs 后半段 {second_rate:.1f} mm/期，"
    f"最近一期增量 {last_step:.1f}mm vs 历史平均 {avg_step:.1f}mm。")



agent = create_agent(llm, [query_regulation, query_dataset, risk_assessment])

st.title("遥感地质灾害智能助手")
st.write("法规问答 + 数据集查询 + 形变风险分析")

with st.form("qa_form"):
    question = st.text_input("你的问题：", placeholder="例如：地质灾害分为哪几个等级？")
    submitted = st.form_submit_button("提问")

if submitted and question:
    user_message = (
        f"{question}\n\n"
        "回答要求： 1.设计风险分析时，按'数据概况/形变分析/风险等级/结论'分节输出；"
        "2.末尾必须带：本系统结果仅基于公开数据辅助分析，不构成专业灾害预测或官方预警。"
    )
    result = agent.invoke({"messages": [("user", user_message)]})
    st.markdown("### 回答")
    st.write(result["messages"][-1].content)