import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
conn = sqlite3.connect(os.path.join(BASE_DIR,"monitoring.db"))
cursor = conn.cursor()
data = ("测试数据集", "测试区域", "SBAS-InSAR", "2020-2024", "±5mm", "测试来源")
cursor.execute(
    "INSERT INTO datasets (name, region, method, period, accuracy, source) VALUES (?,?,?,?,?,?)", 
    data,
    )
conn.commit()

new_id = cursor.lastrowid
print("新增id = ", new_id)

cursor.execute("SELECT * FROM datasets WHERE name LIKE ?" , ("%测试%",))
print("验证查到：", cursor.fetchall())

cursor.execute(
    "UPDATE datasets SET accuracy = ? WHERE id = ? ", ("±10mm", new_id),
               )
conn.commit()
print("影响行数 =", cursor.rowcount)

cursor.execute("DELETE FROM datasets WHERE id = ?", (new_id,))
conn.commit()

cursor.execute("SELECT * FROM datasets WHERE id = ?", (new_id,))
print("删除后还能查到吗：", cursor.fetchall())
print("datasets 行数= ", cursor.execute("SELECT COUNT(*) FROM datasets").fetchall()[0])
print("monitoring_series 行数 =", cursor.execute("SELECT COUNT(*) FROM monitoring_series").fetchone()[0])

# ...上面是你已经跑通的 INSERT / SELECT / UPDATE / DELETE...

# ---------- 批量清理（放在 conn.close() 之前）----------
cursor.execute("SELECT id FROM datasets WHERE name LIKE ?", ("%测试%",))
print("待删除：", cursor.fetchall())

cursor.execute("DELETE FROM datasets WHERE name LIKE ?", ("%测试%",))
conn.commit()
print("删除行数 =", cursor.rowcount)          # 这次应该是 6（4,5,6,7,8 + 本次新插的）

print("剩余测试数据 =", cursor.execute("SELECT COUNT(*) FROM datasets WHERE name LIKE ?", ("%测试%",)).fetchone()[0])   # 0
print("datasets 总行数 =", cursor.execute("SELECT COUNT(*) FROM datasets").fetchone()[0])                              # 3

conn.close()


