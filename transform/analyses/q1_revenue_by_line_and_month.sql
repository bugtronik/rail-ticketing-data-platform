-- Q1. Revenue by line and month of SALE (the commercial team's main view).
-- Gross = what was sold; net = after the refunds paid on those same sales.
SELECT
    d.month_key,
    l.line_name,
    SUM(f.sales_amount) AS gross_sales_xaf,
    SUM(f.net_sales_amount) AS net_sales_xaf,
    COUNT(*) FILTER (WHERE f.is_paid) AS tickets_sold
FROM {{ ref('fct_tickets') }} AS f
INNER JOIN {{ ref('dim_date') }} AS d ON f.order_date = d.date_day
INNER JOIN {{ ref('dim_line') }} AS l ON f.line_id = l.line_id
GROUP BY d.month_key, l.line_name
ORDER BY d.month_key, l.line_name
