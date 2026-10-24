-- 本项目手工编造的教学数据；不是淘宝、Olist 或 UCI 的真实订单。
-- 一行代表一笔订单，金额以分保存，日期按教学样例的本地自然日理解。
CREATE TABLE demo_orders (
    order_id TEXT PRIMARY KEY,
    paid_date TEXT,
    payment_status TEXT NOT NULL CHECK (payment_status IN ('paid', 'pending', 'cancelled')),
    category TEXT NOT NULL,
    order_amount_fen INTEGER NOT NULL CHECK (order_amount_fen >= 0),
    CHECK ((payment_status = 'paid') = (paid_date IS NOT NULL))
);

INSERT INTO demo_orders (order_id, paid_date, payment_status, category, order_amount_fen) VALUES
    ('O001', '2026-10-21', 'paid',      '手机', 30000),
    ('O002', '2026-10-22', 'paid',      '手机', 12000),
    ('O003', '2026-10-22', 'paid',      '家电',  8000),
    ('O004', NULL,         'pending',   '家电',  7000),
    ('O005', NULL,         'cancelled', '手机',  5000),
    ('O006', '2026-10-23', 'paid',      '手机',  9000);
