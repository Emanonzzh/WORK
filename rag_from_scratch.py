import os
import sys

import chromadb
from openai import OpenAI

api_key = os.getenv("DASHSCOPE_API_KEY")
if not api_key:
    sys.exit("缺少环境变量 DASHSCOPE_API_KEY：请先执行 setx 或 $env:DASHSCOPE_API_KEY=xxx 再运行")
base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
client = OpenAI(api_key=api_key, base_url=base_url)

def embed(text):
    response = client.embeddings.create(model="text-embedding-v3",input=text)
    return response.data[0].embedding

def chunk(text):
    return text.split("\n\n")
    

KB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "知识库.txt")
if not os.path.exists(KB_PATH):
    sys.exit(f"找不到知识库文件：{KB_PATH}")
with open(KB_PATH, "r", encoding="utf-8") as f:
    text = f.read()
chunks = chunk(text)
print(f"切成{len(chunks)}块")

vectors = []
for chunk in chunks:
    vectors.append(embed(chunk))
print(f"转成 {len(vectors)} 个向量")

db = chromadb.Client()
collection = db.create_collection("rs_kb")

ids = []
for i in range(len(chunks)):
    ids.append(f"块{i}")

collection.add(ids=ids, documents=chunks, embeddings=vectors)
print("已存入向量库")

question = "什么是ps-insar"
questionembed = embed(question)
result = collection.query(query_embeddings=[questionembed],n_results=2)

docs = result["documents"][0]
mates = "\n".join(docs)
prompt = "参考材料：\n" + mates + "\n\n问题：" + question + "\n请根据上面的材料回答问题。材料里没有的，就说不知道。"

response = client.chat.completions.create(
    model="qwen-plus",
    messages=[
        {"role": "system", "content": prompt},
        {"role": "user", "content": "用一句话介绍什么是 InSAR 技术？"},
    ],
)

answer = response.choices[0].message.content
print("模型回答：")
print(answer)
print(docs)



