-- Q4. Split of sales by channel.
SELECT
    c.channel_name,
    c.is_online,
    COUNT(*) FILTER (WHERE f.is_paid) AS tickets_sold,
    SUM(f.sales_amount) AS gross_sales_xaf,
    ROUND(100.0 * SUM(f.sales_amount) / SUM(SUM(f.sales_amount)) OVER (), 1) AS share_pct
FROM {{ ref('fct_tickets') }} AS f
INNER JOIN {{ ref('dim_channel') }} AS c ON f.channel_id = c.channel_id
GROUP BY c.channel_name, c.is_online
ORDER BY gross_sales_xaf DESC
