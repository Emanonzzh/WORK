# Dockerfile：把遥感地质灾害智能助手装进容器
FROM python:3.14-slim

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple \
    streamlit openai chromadb langchain langchain-openai \
    langchain-chroma langchain-text-splitters langgraph

EXPOSE 8501

CMD ["streamlit", "run", "day21.py", "--server.address", "0.0.0.0", "--server.port", "8501"]
