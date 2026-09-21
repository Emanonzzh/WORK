# SQL 练习 · 销售项目进阶（真实数据）

> 前置：已完成 `SQL增删改查入门.md`。本份是**进阶版**，所有题目直接打你销售项目里的 `orders` 表（102,287 行真实订单），
> 练 SQL 的同时把项目的分析逻辑也吃透 —— 这正是面试要问的。
>
> **用法**：先在纸上/记事本手写 SQL，再看答案；"预期结果"来自真实数据，手写时可对照核对。
> 想实际执行就在主力机跑（MySQL `ai_commerce_intelligence_platform.orders`），低配机用 Python `sqlite3` 建同结构小表也能练。

## 表结构（orders，16 字段）

`order_id`(订单号) · `user_name`(客户) · `product_id`(商品) · `order_amount`(下单额) · `payment_amount`(实付额)
`discount_amount`(优惠额) · `channel_id`(渠道) · `platform_type`(平台) · `is_refund`(是否退款，是/否)
`order_time`/`payment_time`(下单/付款时间) · `payment_duration_sec`(支付耗时) · `order_date`(日期) · `order_hour`(小时) · `weekday`(星期)

---

## Level 1 · 聚合与分组（查数）

**Q1** 统计订单总数、实付总额、客单价（实付额/订单数），保留 2 位小数。
预期：`102287` 单 / `101755818.98` 元 / `994.81`

<details><summary>答案</summary>

```sql
SELECT COUNT(*) AS orders,
       ROUND(SUM(payment_amount), 2) AS revenue,
       ROUND(SUM(payment_amount) / COUNT(*), 2) AS aov
FROM orders;
```
</details>

**Q2** 按平台统计订单数与实付额，按实付额降序。
预期：微信公众号 42,032 单 / 47,100,892.19 元；APP 51,294 单 / 46,041,870.47 元（注意：**APP 单量多但金额低，公众号单量少但金额高**——这就是"结构差异"，后面量价分解要用）。

<details><summary>答案</summary>

```sql
SELECT platform_type, COUNT(*) AS orders, ROUND(SUM(payment_amount), 2) AS revenue
FROM orders
GROUP BY platform_type
ORDER BY revenue DESC;
```
</details>

**Q3**（HAVING）找出订单数 < 100 的平台 —— 这就是"长尾"，异常检测时要剔除。
预期：微信小商店 87 单、wap网站 3 单。

<details><summary>答案</summary>

```sql
SELECT platform_type, COUNT(*) AS orders
FROM orders
GROUP BY platform_type
HAVING COUNT(*) < 100;
```
</details>

**Q4**（时间聚合）按月统计订单数与实付额。
预期：2 月最低（4,898 单 / 526.9 万，春节+天数少）；6 月金额最高（987.96 万）；11 月单量最多（10,910 单）。

<details><summary>答案</summary>

```sql
SELECT DATE_FORMAT(order_date, '%Y-%m') AS ym,
       COUNT(*) AS orders,
       ROUND(SUM(payment_amount), 2) AS revenue
FROM orders
GROUP BY ym
ORDER BY ym;
```
</details>

---

## Level 2 · 环比（对比两个期间）

**Q5** 算 11 月相对 10 月的实付额增长率（环比）。
预期：10 月 8,728,115.07 → 11 月 10,117,593.28，**+15.92%**。

<details><summary>答案（CASE WHEN 版）</summary>

```sql
SELECT
  ROUND(SUM(CASE WHEN DATE_FORMAT(order_date,'%Y-%m')='2025-10' THEN payment_amount END),2) AS oct,
  ROUND(SUM(CASE WHEN DATE_FORMAT(order_date,'%Y-%m')='2025-11' THEN payment_amount END),2) AS nov,
  ROUND((SUM(CASE WHEN DATE_FORMAT(order_date,'%Y-%m')='2025-11' THEN payment_amount END)
       - SUM(CASE WHEN DATE_FORMAT(order_date,'%Y-%m')='2025-10' THEN payment_amount END))
       / SUM(CASE WHEN DATE_FORMAT(order_date,'%Y-%m')='2025-10' THEN payment_amount END) * 100, 2) AS mom_pct
FROM orders;
```
</details>

**Q6**（量价分解的 SQL 部分）分别算 10 月和 11 月的订单数 N 与客单价 AOV。
预期：10 月 N=8,859、AOV=985.23；11 月 N=10,910、AOV=927.37。

<details><summary>答案</summary>

```sql
SELECT DATE_FORMAT(order_date, '%Y-%m') AS ym,
       COUNT(*) AS n,
       ROUND(AVG(payment_amount), 2) AS aov
FROM orders
WHERE DATE_FORMAT(order_date, '%Y-%m') IN ('2025-10', '2025-11')
GROUP BY ym
ORDER BY ym;
```
</details>

> **串联项目**：拿到 Q6 的两个 N 和 AOV，就能算量价分解（不用 SQL 了）：
> 量效应 = (10910−8859)×985.23 ≈ **+202.07 万**；价效应 = 8859×(927.37−985.23) ≈ **−51.26 万**。
> 这就是你项目里"10→11 月 +15.92%，量在拉、价在拖"的来处。

---

## Level 3 · 退款与折扣（业务口径）

**Q7** 算整体退款单量率和退款金额率（两个口径，别混）。
预期：单量率 13.19%，金额率 13.35%。

<details><summary>答案</summary>

```sql
SELECT
  ROUND(SUM(is_refund = '是') / COUNT(*), 4) AS refund_order_rate,
  ROUND(SUM(CASE WHEN is_refund = '是' THEN payment_amount ELSE 0 END) / SUM(payment_amount), 4) AS refund_amt_rate
FROM orders;
```
</details>

**Q8** 算平均折扣深度（优惠额/下单额），并统计折扣深度超过 50% 的订单数。
预期：平均 8.06%；超 50% 的订单数需运行确认（这是异常值候选）。

<details><summary>答案</summary>

```sql
SELECT ROUND(AVG(discount_amount / NULLIF(order_amount, 0)) * 100, 2) AS avg_discount_pct,
       SUM(discount_amount / NULLIF(order_amount, 0) > 0.5) AS over_50pct_cnt
FROM orders;
```
</details>

---

## Level 4 · 窗口函数（排名 / 环比 / 占比）

**Q9**（RANK）每个平台实付额最高的商品是哪个？
<details><summary>答案</summary>

```sql
WITH t AS (
  SELECT platform_type, product_id, SUM(payment_amount) AS rev,
         RANK() OVER (PARTITION BY platform_type ORDER BY SUM(payment_amount) DESC) AS rk
  FROM orders
  GROUP BY platform_type, product_id
)
SELECT platform_type, product_id, ROUND(rev,2) AS rev
FROM t WHERE rk = 1;
```
</details>

**Q10**（LAG）用窗口函数算月度实付额环比（不用自连接）。
<details><summary>答案</summary>

```sql
WITH m AS (
  SELECT DATE_FORMAT(order_date,'%Y-%m') AS ym, SUM(payment_amount) AS rev
  FROM orders GROUP BY ym
)
SELECT ym, ROUND(rev,2) AS rev,
       ROUND((rev - LAG(rev) OVER (ORDER BY ym)) / LAG(rev) OVER (ORDER BY ym) * 100, 2) AS mom_pct
FROM m ORDER BY ym;
```
</details>

**Q11**（结构效应铺垫）每个平台的订单量占比 + 客单价，和整体客单价对比。
预期：公众号 AOV 1120.60（高于整体 994.81）、APP AOV 897.61（低于整体）——**这就是"结构效应"的来源：高客单平台占比变化会拉动整体客单价**。

<details><summary>答案</summary>

```sql
SELECT platform_type, COUNT(*) AS n,
       ROUND(COUNT(*) / SUM(COUNT(*)) OVER (), 4) AS n_share,
       ROUND(AVG(payment_amount), 2) AS aov
FROM orders
GROUP BY platform_type
ORDER BY n DESC;
```
</details>

---

## Level 5 · 子查询 / CTE（复购与异常）

**Q12** 算下单 ≥2 次的用户数及其占全部用户的比例（复购率）。
预期：复购用户 19,809 / 总用户 78,044 = **25.38%**。

<details><summary>答案</summary>

```sql
SELECT SUM(cnt >= 2) AS repeat_users, COUNT(*) AS total_users,
       ROUND(SUM(cnt >= 2) / COUNT(*), 4) AS repeat_rate
FROM (SELECT user_name, COUNT(*) AS cnt FROM orders GROUP BY user_name) t;
```
</details>

**Q13**（异常检测相关）找实付额波动最大的前 5 个商品（用标准差衡量，且订单数 ≥30 过滤长尾）。
预期：需运行确认；注意 **必须有 `HAVING n>=30`**，否则长尾商品（几单就一个极端值）会霸榜。

<details><summary>答案</summary>

```sql
SELECT product_id, COUNT(*) AS n,
       ROUND(AVG(payment_amount), 2) AS avg_pay,
       ROUND(STDDEV(payment_amount), 2) AS sd
FROM orders
GROUP BY product_id
HAVING n >= 30
ORDER BY sd DESC
LIMIT 5;
```
</details>

**Q14**（时段）星期几的平均订单量最高？
预期：需运行确认（这是"周内效应"——做季节基线校正时的输入）。

<details><summary>答案</summary>

```sql
SELECT weekday, COUNT(*) AS orders, ROUND(AVG(payment_amount), 2) AS avg_rev
FROM orders
GROUP BY weekday
ORDER BY orders DESC;
```
</details>

---

## 自检标准（做完对照）

- [ ] Q1–Q4 能一口气写对，不用查文档 —— 聚合/分组/HAVING/时间函数是底线
- [ ] Q5、Q10 两种"环比"写法都会（CASE WHEN 和 LAG）—— 面试常问"环比怎么算"
- [ ] Q6 之后能**口述**量价分解怎么从 SQL 结果推出来 —— 这是项目核心，必须能讲
- [ ] Q7 能说清"单量率 vs 金额率"为什么不同 —— 口径意识
- [ ] Q11 能解释"结构效应"为什么和平台 AOV 差异有关 —— 进阶分
- [ ] Q13 能说出为什么必须 `HAVING n>=30` —— 最小支持度门槛，面试加分

> 做完想对真实结果，就在主力机 `mysql --defaults-file=F:\soft\my-client.ini ai_commerce_intelligence_platform` 里跑每条 SQL 核对。
