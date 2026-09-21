import sqlite3
conn = sqlite3.connect("monitoring.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS monitoring_series (
id INTEGER PRIMARY KEY AUTOINCREMENT,
region TEXT,
date TEXT,
deformation_mm REAL,
source TEXT
)
""")
cursor.execute("DELETE FROM monitoring_series")

data = [
    ("川西示范区", "2024-01", 2.1, "示例演示数据"),
    ("川西示范区", "2024-02", 3.5, "示例演示数据"),
    ("川西示范区", "2024-03", 5.0, "示例演示数据"),
    ("川西示范区", "2024-04", 7.2, "示例演示数据"),
    ("川西示范区", "2024-05", 9.8, "示例演示数据"),
    ("川西示范区", "2024-06", 12.5, "示例演示数据"),
    ("川西示范区", "2024-07", 15.3, "示例演示数据"),
    ("川西示范区", "2024-08", 18.0, "示例演示数据"),
    ("川西示范区", "2024-09", 21.4, "示例演示数据"),
    ("川西示范区", "2024-10", 25.0, "示例演示数据"),
    ("川西示范区", "2024-11", 28.9, "示例演示数据"),
    ("川西示范区", "2024-12", 33.2, "示例演示数据"),
    ("川西示范区", "2025-01", 39.5, "示例演示数据"),
    ("川西示范区", "2025-02", 47.0, "示例演示数据"),
    ("川西示范区", "2025-03", 56.5, "示例演示数据"),
    ("川西示范区", "2025-04", 68.0, "示例演示数据"),
    ("川西示范区", "2025-05", 82.0, "示例演示数据"),
    ("川西示范区", "2025-06", 99.5, "示例演示数据"),
]

cursor.executemany(
    "INSERT INTO monitoring_series (region, date, deformation_mm, source) VALUES(?, ?, ?, ?)",
    data,

)
conn.commit()

cursor.execute("SELECT COUNT(*) FROM monitoring_series")
print("总条数:", cursor.fetchone()[0])
conn.close()



