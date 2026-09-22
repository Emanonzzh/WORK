-- 订单表结构 —— 摘自上游开源项目 ai-commerce-intelligence-platform（README 声明 MIT License）
-- 出处：sql/01_create_table.sql，字段与索引保持原样，仅去掉 docker-entrypoint 相关注释。
--
-- 保留原表名 orders 是刻意的：agent_lab/tools.py 的四个工具都写死查 orders 表，
-- 这样别人用样例数据建出来的库，可以直接跑本项目全部代码，不需要改一行。

CREATE TABLE IF NOT EXISTS orders (
    order_seq_id INT NOT NULL,
    order_id VARCHAR(50) NOT NULL,
    user_name VARCHAR(100) NULL,
    product_id VARCHAR(50) NULL,
    order_amount DECIMAL(18, 2) NULL,
    payment_amount DECIMAL(18, 2) NULL,
    channel_id VARCHAR(50) NULL,
    platform_type VARCHAR(50) NULL,
    order_time DATETIME NULL,
    payment_time DATETIME NULL,
    is_refund VARCHAR(10) NULL,
    discount_amount DECIMAL(18, 2) NULL,
    payment_duration_sec INT NULL,
    order_date DATE NULL,
    order_hour TINYINT UNSIGNED NULL,
    weekday VARCHAR(20) NULL,
    PRIMARY KEY (order_seq_id),
    UNIQUE KEY uq_orders_order_id (order_id),
    KEY idx_orders_order_time (order_time),
    KEY idx_orders_order_date (order_date),
    KEY idx_orders_platform_date (platform_type, order_date),
    KEY idx_orders_user_date (user_name, order_date),
    KEY idx_orders_product_id (product_id),
    KEY idx_orders_payment_amount (payment_amount),
    KEY idx_orders_is_refund (is_refund)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci;
