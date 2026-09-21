# Day 19：SQLite 入门 —— 真实监测数据入库
import sqlite3

# ① 连接数据库（文件不存在会自动创建 monitoring.db）
conn = sqlite3.connect("monitoring.db")
cursor = conn.cursor()

# ② 建表：定义"一张表长什么样"（列名 + 类型）
cursor.execute("""
CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    region TEXT,
    method TEXT,
    period TEXT,
    accuracy TEXT,
    source TEXT
)
""")
cursor.execute("DELETE FROM datasets")
# ③ 插入真实数据（元数据来自国家级科学数据中心公开页面，可查证）
data = [
    ("青藏工程走廊多年冻土区地表形变数据集", "青藏公路/铁路沿线（西大滩-安多）",
     "时序InSAR（LiCSAR+LiCSBAS）", "2017-2022",
     "垂直形变误差多在10mm内，最大不超过30mm", "国家冰川冻土沙漠科学数据中心"),
    ("理塘地区地表形变反演数据集", "四川省理塘县",
     "SBAS-InSAR（Sentinel-1）", "2017-2022",
     "空间分辨率20米", "国家冰川冻土沙漠科学数据中心"),
    ("中巴经济走廊地表变形数据集", "新疆喀什至瓜达尔港",
     "PS-InSAR（SARProZ）", "2014-2018",
     "分辨率20米，城市区精度3-5mm", "甘肃省生态环境科学数据中心"),
]
cursor.executemany(
    "INSERT INTO datasets (name, region, method, period, accuracy, source) VALUES (?, ?, ?, ?, ?, ?)",
    data,
)
conn.commit()
print("已入库 3 条真实数据集记录")

# ④ 查询：按关键词模糊搜索
keyword = input("输入关键词（如 青藏 / InSAR / 理塘）：")
cursor.execute(
    "SELECT * FROM datasets WHERE name LIKE ? OR region LIKE ? OR method LIKE ?",
    (f"%{keyword}%", f"%{keyword}%", f"%{keyword}%"),
)
rows = cursor.fetchall()
for row in rows:
    print(f"数据集：{row[1]}")
    print(f"  区域：{row[2]} | 方法：{row[3]} | 时间：{row[4]}")
    print(f"  精度：{row[5]} | 来源：{row[6]}")
    print()

conn.close()
