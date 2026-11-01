"""Reviewed local ETL: paid orders and visits become the daily metric table."""

DAILY_ETL_SELECT = """
SELECT v.biz_date, p.industry, a.activity_type, v.visitor_uv,
       COUNT(o.order_id), COALESCE(SUM(o.pay_amount_cents), 0)
FROM fact_visit_daily AS v
JOIN dim_product AS p ON p.product_id = v.product_id
JOIN dim_activity AS a ON a.activity_id = v.activity_id
LEFT JOIN fact_order AS o
  ON o.biz_date = v.biz_date
 AND o.product_id = v.product_id
 AND o.activity_id = v.activity_id
 AND o.order_status = 'paid'
GROUP BY v.biz_date, p.industry, a.activity_type, v.visitor_uv
""".strip()

DAILY_ETL_SQL = (
    "INSERT INTO commerce_daily "
    "(biz_date, industry, activity_type, visitor_uv, order_count, pay_amount_cents) "
    + DAILY_ETL_SELECT
)
