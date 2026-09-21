# SQL 增删改查入门（20 分钟，用你自己的 monitoring.db）

> 目标：看懂并能自己写出 增(INSERT) / 删(DELETE) / 改(UPDATE) / 查(SELECT) 四条语句，并在 Python 里跑通。
> 数据：`F:\Python\gongc\rs-ai-assistant\monitoring.db`
> - `datasets(id, name, region, method, period, accuracy, source)` —— 3 条数据集元数据
> - `monitoring_series(id, region, date, deformation_mm, source)` —— 18 条形变时序

---

## 一、先建立心智模型

| 数据库概念 | 类比 |
|---|---|
| 表（table） | 一张 Excel 表 |
| 行（row） | 表里的一条记录 |
| 列（column / 字段） | Excel 的一列 |
| `id` | 主键，每行唯一编号（自增） |

**SQL 就 4 个动词**：`INSERT`（增）、`SELECT`（查）、`UPDATE`（改）、`DELETE`（删）。

---

## 二、四条语句模板（背下来）

### 1) 增：INSERT
```sql
INSERT INTO datasets (name, region, method, period, accuracy, source)
VALUES (?, ?, ?, ?, ?, ?);
```
> 列名按顺序写，`VALUES` 后面用 `?` 占位（见第四节为什么）。

### 2) 查：SELECT
```sql
SELECT * FROM datasets WHERE region LIKE ? ORDER BY id DESC LIMIT 10;
SELECT COUNT(*) FROM monitoring_series;
SELECT region, deformation_mm FROM monitoring_series WHERE region = ? ORDER BY date;
```
- `*` = 所有列；也可以只写想要的列
- `WHERE` = 筛选条件；`ORDER BY` = 排序（`DESC` 倒序）；`LIMIT` = 只取前 N 条
- `LIKE '%川西%'` = 模糊匹配（⚠️ **是连续子串匹配**，中间断开就匹配不到）

### 3) 改：UPDATE
```sql
UPDATE datasets SET accuracy = ? WHERE id = ?;
```
> ⚠️ **忘写 `WHERE` 会把整张表都改掉**（同理 DELETE）。

### 4) 删：DELETE
```sql
DELETE FROM datasets WHERE id = ?;
```

**常用组合**：`AND` / `OR` 连接条件；`ORDER BY 列名 ASC|DESC`；`LIMIT n OFFSET m` 分页。

---

## 三、Python 里怎么跑（sqlite3 六步）

```python
import sqlite3

conn = sqlite3.connect("monitoring.db")   # 1. 连接
cursor = conn.cursor()                    # 2. 游标

cursor.execute("SELECT * FROM datasets WHERE region LIKE ?", ("%青藏%",))  # 3. 执行（参数化）
rows = cursor.fetchall()                  # 4. 取结果（查）
for r in rows:
    print(r)

conn.commit()                             # 5. 写操作必须提交，否则不落库！
conn.close()                              # 6. 关闭
```

**要点**
- 查 → 要 `fetchall()` / `fetchone()` 才拿到数据
- 增/改/删 → 必须 `conn.commit()`，否则**看起来成功、其实没写进去**
- 参数化：`execute(sql, (值1, 值2))`，**第二个参数是元组**；只有 1 个值也要写逗号：`(值,)`

---

## 四、完整示例：一次搞定增 / 改 / 删（复制可跑）

```python
import sqlite3

conn = sqlite3.connect(r"F:\Python\gongc\rs-ai-assistant\monitoring.db")
cursor = conn.cursor()

# ① 增：6 个 ? 就要对应 6 个值的元组
data = ("测试数据集", "测试区域", "SBAS-InSAR", "2020-2024", "±5mm", "测试来源")
cursor.execute(
    "INSERT INTO datasets (name, region, method, period, accuracy, source) VALUES (?,?,?,?,?,?)",
    data,
)
conn.commit()
print("新增后 id =", cursor.lastrowid)

# ② 查：确认刚插进去的
cursor.execute("SELECT * FROM datasets WHERE name LIKE ?", ("%测试%",))
print("查到：", cursor.fetchall())

# ③ 改：用 id 定位（不要用 name，可能重名）
cursor.execute("UPDATE datasets SET accuracy = ? WHERE id = ?", ("±10mm", cursor.lastrowid))
conn.commit()
print("影响行数 =", cursor.rowcount)          # 检查是否真的改到了

# ④ 删：同样用 id 定位
cursor.execute("DELETE FROM datasets WHERE id = ?", (cursor.lastrowid,))
conn.commit()
print("删除后还能查到吗：", cursor.fetchall())

conn.close()
```

**易错点**
- `?` 的个数必须等于元组元素个数，否则报 `Incorrect number of bindings supplied`
- 单个值的元组要写逗号：`(值,)`
- 增/改/删必须 `conn.commit()`，否则不落库
- 改/删**用 `id` 定位**，并看 `cursor.rowcount` 确认影响行数（防静默失败）

---

## 五、为什么必须用 `?`（而不是拼字符串）

```python
# ❌ 危险写法：字符串拼接
sql = "SELECT * FROM datasets WHERE region = '" + region + "'"
```
问题有两个：
1. **SQL 注入**：如果 `region` 里带引号/`OR 1=1`，整个语句语义就被改了
2. **转义地狱**：中文、引号、空格都要自己处理，容易出错

```python
# ✅ 正确写法：参数化
cursor.execute("SELECT * FROM datasets WHERE region = ?", (region,))
```

---

## 六、练习题（在 monitoring.db 上做，不许照抄上面的示例）

写一个 `practice_sql.py`，依次完成：

1. **INSERT**：往 `datasets` 插一条**测试数据**（name 里带"测试"两字，便于清理）
2. **SELECT**：按 `region` 模糊查询刚插的这条，打印出来
3. **UPDATE**：把它 `accuracy` 字段改成别的值，再查一次确认变了
4. **DELETE**：把它删掉，再查一次确认查不到
5. **计数**：打印 `datasets` 和 `monitoring_series` 各有多少行（用 `COUNT(*)`）

**做完自问三句**（面试会问）：
- 为什么写操作要 `commit()`？
- 为什么用 `?` 而不是拼字符串？
- `DELETE` 忘写 `WHERE` 会发生什么？

---

## 七、常见坑（新手必踩）

| 坑 | 现象 | 正确做法 |
|---|---|---|
| 忘记 `commit()` | 查询看不到新数据 | 增改删后 `conn.commit()` |
| 忘记 `fetchall()` | 拿到的是游标不是数据 | 查询后 `fetchall()` / `fetchone()` |
| 参数化少写逗号 | `(值)` 是普通括号不是元组 | 单个参数写 `(值,)` |
| 路径写错 | 连上了别的目录的空库 | 先 `cd` 到项目目录再跑（你踩过这个坑） |
| 忘写 `WHERE` | 整表被改/删 | 改删前先 `SELECT` 确认范围 |
