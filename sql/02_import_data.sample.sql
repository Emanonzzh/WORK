-- 导入**样例数据**（由 agent_lab/make_sample_data.py 生成），不是上游那 102,287 行真实订单。
--
-- 与上游 02_import_data.sql 的区别只有两处：文件路径、以及明确这是样例。
-- 字段映射、分隔符、SET 清洗逻辑保持与上游一致——这正是"样例 CSV 用同一套表头"的目的。
--
-- 前置 1：MySQL 的 secure_file_priv 目录。查它：  SHOW VARIABLES LIKE 'secure_file_priv';
-- 前置 2：把生成的 sample_orders.csv 放进那个目录（本机是 F:/soft/mysql-files/）。
--         LOAD DATA 只能从 secure_file_priv 读，这是 MySQL 的防任意文件读机制，改不了路径就白跑。
-- 执行：  mysql -u <user> -p <你的库名> -e "source sql/02_import_data.sample.sql"
--
-- ⚠️ 跑出来的任何数字都不可与 README/报告里的真实结论对照。样例是随机数。
--
-- 【本机实测的权限结论 —— 决定你该用哪条路导入】
--   LOAD DATA INFILE        需要**全局 FILE 权限**。缺它时 MySQL 返回的是 **1045 Access denied
--                           (using password: YES)**，不是 1044，极容易被误判成"密码写错了"。
--                           本项目的分析账号 commerce 只有 USAGE ON *.* + ALL ON <业务库>.*，
--                           所以它跑不了这条 —— 这是最小权限设计的结果，不是 bug。
--   LOAD DATA LOCAL INFILE  还额外受服务端开关约束：本机 `local_infile = OFF` → 报 3948。
--   所以：**小数据量（样例）请用 `python agent_lab/load_sample_data.py`，只需 INSERT 权限。**
--   本文件留给"有 FILE 权限 + 十万行以上"的真实数据导入场景。

LOAD DATA INFILE 'F:/soft/mysql-files/sample_orders.csv'
INTO TABLE orders
CHARACTER SET utf8mb4
FIELDS TERMINATED BY ','
OPTIONALLY ENCLOSED BY '"'
ESCAPED BY '"'
LINES TERMINATED BY '\n'
IGNORE 1 LINES
(
    @order_seq_id,
    @order_id,
    @user_name,
    @product_id,
    @order_amount,
    @payment_amount,
    @channel_id,
    @platform_type,
    @order_time,
    @payment_time,
    @is_refund,
    @discount_amount,
    @payment_duration_sec,
    @order_date,
    @order_hour,
    @weekday
)
SET
    order_seq_id = NULLIF(TRIM(BOTH '\r' FROM @order_seq_id), ''),
    order_id = NULLIF(@order_id, ''),
    user_name = NULLIF(@user_name, ''),
    product_id = NULLIF(@product_id, ''),
    order_amount = NULLIF(@order_amount, ''),
    payment_amount = NULLIF(@payment_amount, ''),
    channel_id = NULLIF(@channel_id, ''),
    platform_type = NULLIF(@platform_type, ''),
    order_time = NULLIF(@order_time, ''),
    payment_time = NULLIF(@payment_time, ''),
    is_refund = NULLIF(@is_refund, ''),
    discount_amount = NULLIF(@discount_amount, ''),
    payment_duration_sec = NULLIF(@payment_duration_sec, ''),
    order_date = NULLIF(@order_date, ''),
    order_hour = NULLIF(@order_hour, ''),
    weekday = NULLIF(TRIM(BOTH '\r' FROM @weekday), '');
