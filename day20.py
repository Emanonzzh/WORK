import os
import sys
from langchain_core.tools import tool
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
import sqlite3

api_key = os.getenv("DASHSCOPE_API_KEY")
if not api_key:   # 缺 key 直接报明白错，fail fast——别等 401 才发现
    sys.exit("缺少环境变量 DASHSCOPE_API_KEY：请先执行 setx 或 $env:DASHSCOPE_API_KEY=xxx 再运行")
base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")

llm = ChatOpenAI(model="qwen-plus", api_key=api_key, base_url=base_url)

@tool
def query_dataset(keyword:str) -> str:
    """查询遥感监测数据集元数据。keyword 是关键词，例如"青藏"、"InSAR"、"理塘"。"""
    conn = sqlite3.connect("monitoring.db")
    cursor = conn.cursor()  
    cursor.execute(
    "SELECT * FROM datasets WHERE name LIKE ? OR region LIKE ? OR method LIKE ?",
    (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"),
)
    rows = cursor.fetchall()
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
    return result_text               # 最后返回

agent = create_agent(llm, [query_dataset])

question = input("你：")
result = agent.invoke({"messages":[("user",question)]})
print(result["messages"][-1].content)